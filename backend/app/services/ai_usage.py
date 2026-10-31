"""Append minimal, secret-free Gemini usage telemetry for local evaluation."""

import json
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path

_lock = threading.Lock()
_TOKEN = re.compile(r"(?i)(AIza[0-9A-Za-z_-]{20,}|bearer\s+\S+|ya29\.[0-9A-Za-z_-]+)")


def record_ai_usage(*, operation: str, model: str, tokens_in: int | None = None,
                    tokens_out: int | None = None, latency_ms: int,
                    failure: str = "none") -> None:
    """Write only metadata. Never pass prompts, provider bodies, headers, or credentials."""
    destination = Path(os.environ.get(
        "STREAM_PILOT_AI_USAGE_LOG", Path(__file__).resolve().parents[2] / "logs" / "ai_usage.jsonl"
    ))
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "operation": operation[:40],
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
        return
