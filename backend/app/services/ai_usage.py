"""Append minimal, secret-free Gemini usage telemetry for local evaluation."""

import json
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path

_lock = threading.RLock()
_in_flight_calls = 0
_TOKEN = re.compile(r"(?i)(AIza[0-9A-Za-z_-]{20,}|bearer\s+\S+|ya29\.[0-9A-Za-z_-]+)")


def record_ai_usage(*, operation: str, model: str, tokens_in: int | None = None,
                    tokens_out: int | None = None, latency_ms: int,
                    failure: str = "none", call_made: bool = True,
                    reserved: bool = False) -> None:
    """Write only metadata. Never pass prompts, provider bodies, headers, or credentials."""
    destination = Path(os.environ.get(
        "STREAM_PILOT_AI_USAGE_LOG", Path(__file__).resolve().parents[2] / "logs" / "ai_usage.jsonl"
    ))
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "operation": operation[:40],
        "call_made": call_made,
        "model": _TOKEN.sub("[REDACTED]", model)[:120],
        "tokens_in": tokens_in if isinstance(tokens_in, int) and tokens_in >= 0 else None,
        "tokens_out": tokens_out if isinstance(tokens_out, int) and tokens_out >= 0 else None,
        "latency_ms": max(0, int(latency_ms)),
        "failure": _TOKEN.sub("[REDACTED]", failure)[:60],
    }
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with _lock, destination.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except OSError:
        # Telemetry must never break a product request.
        pass
    finally:
        if reserved:
            global _in_flight_calls
            with _lock:
                _in_flight_calls = max(0, _in_flight_calls - 1)


def live_test_gemini_budget_available() -> bool:
    """Enforce the harness cap inside the running API only when explicitly enabled."""
    if os.environ.get("LIVE_TEST_MODE", "").strip().casefold() not in {"true", "1", "yes"}:
        return True
    try:
        limit = int(os.environ.get("LIVE_TEST_MAX_GEMINI_CALLS", "150"))
    except ValueError:
        return False
    if not 1 <= limit <= 1000:
        return False
    try:
        calls = _logged_call_count()
        with _lock:
            return calls + _in_flight_calls < limit
    except OSError:
        # A fresh dedicated usage log is required for live mode; fail closed.
        return False


def _logged_call_count() -> int:
    destination = Path(os.environ.get(
        "STREAM_PILOT_AI_USAGE_LOG", Path(__file__).resolve().parents[2] / "logs" / "ai_usage.jsonl"
    ))
    calls = 0
    if destination.exists():
        with destination.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("call_made", True):
                    calls += 1
    return calls


def reserve_live_test_gemini_call() -> bool | None:
    """Reserve one model call atomically; None means live test mode is disabled."""
    global _in_flight_calls
    if os.environ.get("LIVE_TEST_MODE", "").strip().casefold() not in {"true", "1", "yes"}:
        return None
    try:
        limit = int(os.environ.get("LIVE_TEST_MAX_GEMINI_CALLS", "150"))
        if not 1 <= limit <= 1000:
            return False
        with _lock:
            if _logged_call_count() + _in_flight_calls >= limit:
                return False
            _in_flight_calls += 1
            return True
    except (OSError, ValueError):
        return False


def release_live_test_gemini_call(reserved: bool | None) -> None:
    """Release an in-flight budget slot when the provider operation finishes."""
    global _in_flight_calls
    if reserved:
        with _lock:
            _in_flight_calls = max(0, _in_flight_calls - 1)
