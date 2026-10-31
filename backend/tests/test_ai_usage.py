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
