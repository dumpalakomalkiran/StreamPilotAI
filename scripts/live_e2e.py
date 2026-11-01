"""Synthetic-only Twitch/Gemini live E2E checks for StreamPilot AI."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
load_dotenv(BACKEND / ".env", override=False)

API_BASE = os.environ.get("LIVE_TEST_API_BASE", "http://localhost:8000").rstrip("/")
FRONTEND_BASE = os.environ.get("LIVE_TEST_FRONTEND_BASE", "http://localhost:5173").rstrip("/")
VIEWERS_FILE = Path(os.environ.get("STREAM_PILOT_TEST_VIEWER_TOKENS", ROOT / "data/live_test_viewer_tokens.json"))
REPORT_JSON = ROOT / "docs/live_test_results.json"
REPORT_MD = ROOT / "docs/LIVE_TEST_REPORT.md"
USAGE_LOG_CANDIDATES = (
    (Path(os.environ["STREAM_PILOT_AI_USAGE_LOG"]),) if os.environ.get("STREAM_PILOT_AI_USAGE_LOG")
    else (BACKEND / "logs/ai_usage.jsonl", ROOT / "logs/ai_usage.jsonl")
)
REDACTIONS = (
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+=*"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"(?i)(access_token|refresh_token|client_secret|api_key|session_token)[=: ]+[^\s,;\"']+"),
)
SCENARIOS = {
    "S0": "Baseline live EventSub message flow",
    "S1": "Authentication, overlay and broadcaster isolation",
    "S2": "Question detection, grouping and negative filtering",
    "S3": "Grounded AI reply and explicit send",
    "S4": "Poll suggestion, approved challenge, votes and timer",
    "S5": "Prompt injection, malformed input and rendering safety",
    "S6": "Flag-only moderation review",
    "S7": "Message deletion and chat clear regression guard",
    "S8": "Session end, aggregate recap and caching",
    "S9": "Restart, token rotation and Gemini degradation",
    "S10": "Optional native Twitch polls",
    "S11": "Dashboard Playwright screenshots and console errors",
}


def _redact(value):
    if isinstance(value, dict):
        return {str(key): _redact(item) for key, item in value.items()
                if str(key).casefold() not in {"authorization", "cookie", "set-cookie", "access_token", "refresh_token", "client_secret", "api_key"}}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        result = value
        for pattern in REDACTIONS:
            result = pattern.sub("[REDACTED]", result)
        return result[:500]
    return value


def limits_from_env() -> dict[str, int]:
    def bounded(name: str, default: int, low: int, high: int) -> int:
        raw = os.environ.get(name)
        try:
            value = int(raw) if raw is not None else default
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
        if not low <= value <= high:
            raise ValueError(f"{name} must be between {low} and {high}")
        return value

    return {
        "messages": bounded("LIVE_TEST_MAX_MESSAGES", 200, 1, 2000),
        "gemini_calls": bounded("LIVE_TEST_MAX_GEMINI_CALLS", 150, 1, 1000),
        "seconds": bounded("LIVE_TEST_MAX_SECONDS", 1800, 60, 7200),
    }


def load_script() -> list[dict]:
    path = ROOT / "data/live_test_script.jsonl"
    script = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in script:
        if not isinstance(row.get("text"), str) or not row["text"] or len(row["text"]) > 500:
            raise ValueError("A scripted message is empty or exceeds Twitch's 500-character maximum")
        if set(row) != {"t_offset_seconds", "account_alias", "text", "scenario_tag"}:
            raise ValueError("Unexpected live test schedule fields")
        if not isinstance(row["account_alias"], str) or not row["account_alias"].replace("_", "").isalnum():
            raise ValueError("A schedule row has an invalid account alias")
        if not isinstance(row["scenario_tag"], str) or not row["scenario_tag"].startswith("S"):
            raise ValueError("A schedule row has an invalid scenario tag")
    if any(not isinstance(row["t_offset_seconds"], int) or row["t_offset_seconds"] < 0 for row in script):
        raise ValueError("Schedule offsets must be nonnegative")
    if script != sorted(script, key=lambda row: row["t_offset_seconds"]):
        raise ValueError("Schedule rows must be sorted by offset")
    previous_by_account: dict[str, int] = {}
    for row in script:
        alias = str(row["account_alias"])
        offset = int(row["t_offset_seconds"])
        if alias in previous_by_account and offset - previous_by_account[alias] < 5:
            raise ValueError("Schedule exceeds the conservative per-account Twitch chat pacing limit")
        previous_by_account[alias] = offset
    return script


def scenario_plan() -> tuple[tuple[str, ...], tuple[str, ...]]:
    return ("S0", "S2", "S3", "S4", "S5", "S7", "S8"), ("S1", "S6", "S9", "S10", "S11")


def _record(results: list[dict], scenario_id: str, status: str, expected: str,
            observed: str, evidence: dict | None = None) -> None:
    results.append({
        "scenario_id": scenario_id,
        "scenario": SCENARIOS[scenario_id],
        "expected": expected,
        "observed": observed,
        "status": status,
        "evidence": _redact(evidence or {}),
    })


def _report_markdown(document: dict) -> str:
    lines = [
        "# StreamPilot AI — Live Synthetic E2E Report", "",
        f"- Run: `{document['run_id']}`", f"- Started (UTC): `{document['started_at']}`",
        f"- Finished (UTC): `{document.get('finished_at', 'in progress')}`",
        f"- Target identity gate: `{document['target_identity_match']}`",
        f"- Messages sent: **{document['totals']['messages_sent']}**",
        f"- Gemini calls: **{document['totals']['gemini_calls']}**; tokens in/out: **{document['totals']['tokens_in']} / {document['totals']['tokens_out']}**",
        f"- Estimated cost (USD): **{document['totals']['estimated_cost_usd']}** (configurable estimate; see `config/gemini_pricing.json`)",
        f"- Stop reason: {document.get('stop_reason') or 'all scheduled checks completed'}", "",
        "| Scenario | Status | Key evidence | Latency |", "|---|---|---|---:|",
    ]
    for row in document["results"]:
        evidence = row.get("evidence", {})
        latency = evidence.get("latency_ms", "—")
        lines.append(f"| {row['scenario_id']} {row['scenario']} | **{row['status']}** | {row['observed'][:180]} | {latency} |")
    lines += ["", "## Bugs found and fixed", "", "No live product defects were assessed; this was an offline preparation run.",
              "", "## Open issues / blocked checks", ""]
    lines.extend(f"- {item}" for item in document.get("blockers", []) or ["None recorded."])
    lines += ["", "## What this does NOT prove", "",
              "This run does not prove behavior under high-volume chat, representative viewer slang, provider load, or missed-event recovery. A Twitch subscription marked enabled is not proof of EventSub delivery; only a received canary establishes that for this run. Synthetic accounts and a curated repository dataset do not represent real viewers. No result is inferred from credentials alone.",
              "", "## Safety and artifacts", "",
              "Only messages stored in `data/live_test_script.jsonl` are designated for live sends. Secrets, tokens, headers, raw provider bodies, and local viewer token files are excluded from reports. This report was generated in offline mode; the live driver has not been run.", ""]
    return "\n".join(lines)


def _write_report(document: dict) -> None:
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT_JSON.with_suffix(".tmp")
    temporary.write_text(json.dumps(_redact(document), indent=2) + "\n", encoding="utf-8")
    temporary.replace(REPORT_JSON)
    REPORT_MD.write_text(_report_markdown(document), encoding="utf-8")


def _api_client(session_token: str = "") -> httpx.Client:
    cookies = {"stream_pilot_session": session_token} if session_token else {}
    return httpx.Client(timeout=httpx.Timeout(8, connect=3), cookies=cookies)


def _twitch_main_credential() -> tuple[str, str, dict]:
    """Refresh if needed, validate with Twitch and return token, ID, safe identity info."""
    from app.database import SessionLocal
    from app.models import TwitchCredential
    from app.services.twitch_auth import refresh_access_token_if_needed

    with SessionLocal() as db:
        credential = db.get(TwitchCredential, os.environ.get("LIVE_TEST_BROADCASTER_ID", "")) if os.environ.get("LIVE_TEST_BROADCASTER_ID") else None
        if credential is None:
            from sqlalchemy import select
            credential = db.scalar(select(TwitchCredential).where(TwitchCredential.auth_session_hash.is_not(None)).limit(1))
        if credential is None:
            raise RuntimeError("no_authenticated_broadcaster_credential")
        # A forced refresh verifies refresh-token functionality and persists Twitch's rotation.
        token = asyncio.run(refresh_access_token_if_needed(credential, db, force=True))
        broadcaster_id = credential.broadcaster_id
    response = httpx.get("https://id.twitch.tv/oauth2/validate",
                         headers={"Authorization": f"OAuth {token}"}, timeout=10)
    response.raise_for_status()
    info = response.json()
    if str(info.get("user_id")) != broadcaster_id:
        raise RuntimeError("stored_broadcaster_identity_mismatch")
    return token, broadcaster_id, {
        "login": str(info.get("login", "")).casefold(),
        "user_id": broadcaster_id,
        "client_id": str(info.get("client_id", "")),
        "scopes": sorted(info.get("scopes", [])),
    }


def _check_db_writable() -> None:
    from app.database import engine

    with engine.connect() as connection:
        connection.exec_driver_sql("CREATE TEMP TABLE live_e2e_write_check (id INTEGER PRIMARY KEY, value TEXT)")
        connection.exec_driver_sql("INSERT INTO live_e2e_write_check (value) VALUES ('ok')")
        if connection.exec_driver_sql("SELECT value FROM live_e2e_write_check").scalar_one() != "ok":
            raise RuntimeError("database_write_check_failed")
        connection.exec_driver_sql("DROP TABLE live_e2e_write_check")


def _test_isolated_tenant_db() -> dict:
    """Use a temporary database only; never create a synthetic broadcaster on Twitch or in the live DB."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.database import Base
    from app.models import InteractionGroup, StreamSession

    with tempfile.TemporaryDirectory(prefix="streampilot-live-scope-") as temp_dir:
        engine = create_engine(f"sqlite:///{Path(temp_dir) / 'scope.sqlite'}")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            own = StreamSession(broadcaster_id="synthetic-broadcaster-a", mode="live")
            other = StreamSession(broadcaster_id="synthetic-broadcaster-b", mode="live")
            db.add_all([own, other])
            db.flush()
            db.add_all([
                InteractionGroup(broadcaster_id=own.broadcaster_id, session_id=own.id,
                                 canonical_text="Synthetic tenant A", normalized_text="tenant a"),
                InteractionGroup(broadcaster_id=other.broadcaster_id, session_id=other.id,
                                 canonical_text="Synthetic tenant B", normalized_text="tenant b"),
            ])
            db.commit()
            visible = db.scalars(select(InteractionGroup).where(
                InteractionGroup.broadcaster_id == own.broadcaster_id
            )).all()
            if len(visible) != 1 or visible[0].broadcaster_id != own.broadcaster_id:
                raise RuntimeError("tenant_scope_test_failed")
            result = {"synthetic_records": 2, "visible_to_a": len(visible), "other_broadcaster_leaked": False}
        engine.dispose()
        return result


def _websocket_rejection(url: str, *, origin: str, cookie: str = "", subprotocols=None) -> tuple[bool, int | None]:
    try:
        import websockets
    except ImportError:
        return False, None
    from websockets.exceptions import InvalidStatus

    async def connect_and_check() -> tuple[bool, int | None]:
        headers = {"Cookie": cookie} if cookie else {}
        try:
            async with websockets.connect(url, origin=origin, additional_headers=headers,
                                          subprotocols=subprotocols, open_timeout=3):
                return False, 101
        except InvalidStatus as exc:
            return True, getattr(exc.response, "status_code", None)
        except Exception as exc:  # noqa: BLE001 - network rejection/status varies by websockets version
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)
            return status in (401, 403), status

    return asyncio.run(connect_and_check())


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-live", action="store_true",
                        help="Enable the bounded synthetic scenario run after all identity and safety gates pass.")
    parser.add_argument("--offline", action="store_true",
                        help="Validate checked-in inputs and write a report without contacting any provider or local API.")
    args = parser.parse_args()

    started_at = datetime.now(UTC).isoformat()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results: list[dict] = []
    blockers: list[str] = []
    stop_reason = None
    limits = limits_from_env()
    script = load_script()
    input_limit_error = None
    if len(script) > limits["messages"]:
        input_limit_error = f"LIVE_TEST_MAX_MESSAGES={limits['messages']} is below the checked-in schedule length ({len(script)}); no scenario was run."
    elif int(script[-1]["t_offset_seconds"]) > limits["seconds"]:
        input_limit_error = f"LIVE_TEST_MAX_SECONDS={limits['seconds']} is below the final schedule offset ({script[-1]['t_offset_seconds']}); no scenario was run."
    usage_path = next((p for p in USAGE_LOG_CANDIDATES if p.exists()), USAGE_LOG_CANDIDATES[0])
    totals = {"messages_sent": 0, "gemini_calls": 0, "tokens_in": 0, "tokens_out": 0,
              "estimated_cost_usd": None}
    doc = {"run_id": run_id, "started_at": started_at, "finished_at": None,
           "target_identity_match": False, "limits": limits, "results": results,
           "totals": totals, "blockers": blockers, "stop_reason": stop_reason,
           "ai_usage_log": str(usage_path.relative_to(ROOT)) if usage_path.is_relative_to(ROOT) else "backend/logs/ai_usage.jsonl"}

    if input_limit_error:
        run_scenarios, skipped_scenarios = scenario_plan()
        for scenario_id in run_scenarios:
            _record(results, scenario_id, "BLOCKED", "Scenario stayed within configured run limits.", input_limit_error)
        for scenario_id in skipped_scenarios:
            _record(results, scenario_id, "NOT RUN", "Scenario is explicitly deferred in this rehearsal.",
                    "Skipped by the selected scenario plan.")
        blockers.append(input_limit_error)
        doc["finished_at"] = datetime.now(UTC).isoformat()
        doc["stop_reason"] = "configured_limit_below_checked_in_schedule"
        _write_report(doc)
        print(json.dumps(_redact(doc), indent=2))
        print(f"Scenario report written to {REPORT_MD.relative_to(ROOT)} and {REPORT_JSON.relative_to(ROOT)}")
        return 0

    if args.offline:
        run_scenarios, skipped_scenarios = scenario_plan()
        for scenario_id in run_scenarios:
            _record(results, scenario_id, "BLOCKED", "Live scenario executed with Twitch EventSub and Gemini.",
                    "Offline mode deliberately makes no Twitch, Gemini, or app API requests.")
        for scenario_id in skipped_scenarios:
            _record(results, scenario_id, "NOT RUN", "Scenario is explicitly deferred in this rehearsal.",
                    "Skipped by the selected scenario plan.")
        blockers.append("Live run deferred by the owner; no provider/API requests or chat actions were made.")
        doc["finished_at"] = datetime.now(UTC).isoformat()
        doc["stop_reason"] = "offline_report_only"
        _write_report(doc)
        print(json.dumps(_redact(doc), indent=2))
        print(f"Offline scenario report written to {REPORT_MD.relative_to(ROOT)} and {REPORT_JSON.relative_to(ROOT)}")
        return 0

    from live_scenarios import run_live_scenarios

    return run_live_scenarios(
        args=args, limits=limits, script=script, doc=doc, results=results,
        record=_record, write_report=_write_report, api_client=_api_client,
        twitch_main_credential=_twitch_main_credential, check_db_writable=_check_db_writable,
        websocket_rejection=_websocket_rejection,
    )

if __name__ == "__main__":
    raise SystemExit(_main())
