import json

from app.services import ai_usage


def test_usage_log_contains_only_redacted_metadata(tmp_path, monkeypatch):
    path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("STREAM_PILOT_AI_USAGE_LOG", str(path))

    ai_usage.record_ai_usage(
        operation="reply_suggestion",
        model="gemini-3.5-flash-lite",
        tokens_in=14,
        tokens_out=7,
        latency_ms=123,
        failure="Bearer secret-value",
    )

    entry = json.loads(path.read_text(encoding="utf-8"))
    assert entry["operation"] == "reply_suggestion"
    assert entry["model"] == "gemini-3.5-flash-lite"
    assert entry["tokens_in"] == 14
    assert entry["tokens_out"] == 7
    assert entry["latency_ms"] == 123
    assert "secret-value" not in path.read_text(encoding="utf-8")


def test_usage_log_failure_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("STREAM_PILOT_AI_USAGE_LOG", str(tmp_path / "missing" / "usage.jsonl"))
    (tmp_path / "missing").write_text("not a directory", encoding="utf-8")

    ai_usage.record_ai_usage(operation="grouping", model="model", latency_ms=0)


def test_live_test_budget_counts_actual_calls_only(tmp_path, monkeypatch):
    path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("STREAM_PILOT_AI_USAGE_LOG", str(path))
    monkeypatch.setenv("LIVE_TEST_MODE", "true")
    monkeypatch.setenv("LIVE_TEST_MAX_GEMINI_CALLS", "2")

    assert ai_usage.live_test_gemini_budget_available()
    ai_usage.record_ai_usage(operation="preflight", model="model", latency_ms=1)
    assert ai_usage.live_test_gemini_budget_available()
    ai_usage.record_ai_usage(operation="reply", model="model", latency_ms=0,
                             failure="limit", call_made=False)
    assert ai_usage.live_test_gemini_budget_available()
    ai_usage.record_ai_usage(operation="grouping", model="model", latency_ms=1)
    assert not ai_usage.live_test_gemini_budget_available()


def test_live_test_budget_rejects_invalid_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("STREAM_PILOT_AI_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("LIVE_TEST_MODE", "true")
    monkeypatch.setenv("LIVE_TEST_MAX_GEMINI_CALLS", "not-a-number")

    assert not ai_usage.live_test_gemini_budget_available()


def test_live_test_budget_reservations_are_atomic_and_released(tmp_path, monkeypatch):
    monkeypatch.setenv("STREAM_PILOT_AI_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("LIVE_TEST_MODE", "true")
    monkeypatch.setenv("LIVE_TEST_MAX_GEMINI_CALLS", "1")

    reservation = ai_usage.reserve_live_test_gemini_call()
    assert reservation is True
    assert ai_usage.reserve_live_test_gemini_call() is False
    ai_usage.record_ai_usage(operation="grouping", model="model", latency_ms=1)
    ai_usage.release_live_test_gemini_call(reservation)
    assert not ai_usage.live_test_gemini_budget_available()
