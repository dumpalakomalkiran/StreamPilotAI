"""Synthetic-only Twitch/Gemini rehearsal checks for StreamPilot AI.

Only offline validation is currently enabled; the end-to-end scenario driver is
not complete, so live modes fail closed before any provider or app API request.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

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
USAGE_LOG_CANDIDATES = (BACKEND / "logs/ai_usage.jsonl", ROOT / "logs/ai_usage.jsonl")
REDACTIONS = (
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+=*"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"(?i)(access_token|refresh_token|client_secret|api_key|session_token)[=: ]+[^\s,;\"']+"),
)
SCENARIOS = {
    "S0": "Baseline live EventSub message flow",
    "S1": "Authentication, overlay and broadcaster isolation",
    "S2": "Question detection, grouping, lifecycle and fallback",
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
    demo_path = ROOT / "data/demo_chat.jsonl"
    script = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    demo_texts = {json.loads(line)["text"] for line in demo_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    repo_messages = demo_texts | {row.get("text") for row in script}
    for row in script:
        if not isinstance(row.get("text"), str) or not row["text"] or len(row["text"]) > 500:
            raise ValueError("A scripted message is empty or exceeds Twitch's 500-character maximum")
        if row["text"] not in repo_messages:
            raise ValueError("A live test message is not from a repository data file")
        if set(row) != {"t_offset_seconds", "account_alias", "text", "scenario_tag"}:
            raise ValueError("Unexpected live test schedule fields")
    if any(int(row["t_offset_seconds"]) < 0 for row in script):
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
    lines += ["", "## Bugs found and fixed", "", "No product defects were assessed in this blocked/offline run.",
              "", "## Open issues / blocked checks", ""]
    lines.extend(f"- {item}" for item in document.get("blockers", []) or ["None recorded."])
    lines += ["", "## What this does NOT prove", "",
              "This run does not prove behavior under high-volume chat, representative viewer slang, provider load, or missed-event recovery. A Twitch subscription marked enabled is not proof of EventSub delivery; only a received canary establishes that for this run. Synthetic accounts and a curated repository dataset do not represent real viewers. No result is inferred from credentials alone.",
              "", "## Safety and artifacts", "",
              "Only messages stored in `data/live_test_script.jsonl` are designated for a future driver. Secrets, tokens, headers, raw provider bodies, and local viewer token files are excluded from reports. Live mode currently fails closed because the scenario driver and evidence capture are incomplete.", ""]
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
    parser.add_argument("--confirm-live-synthetic", action="store_true",
                        help="Reserved; live execution is disabled until the scenario driver is complete.")
    parser.add_argument("--allow-streamer-actions", action="store_true",
                        help="Reserved; live execution is disabled until the scenario driver is complete.")
    parser.add_argument("--preflight-only", action="store_true", help="Reserved; use --offline until live preflight is implemented.")
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
    if len(script) > limits["messages"]:
        raise SystemExit(f"Refusing to run: schedule has {len(script)} messages, above LIVE_TEST_MAX_MESSAGES={limits['messages']}.")
    if int(script[-1]["t_offset_seconds"]) > limits["seconds"]:
        raise SystemExit(f"Refusing to run: final schedule offset exceeds LIVE_TEST_MAX_SECONDS={limits['seconds']}.")
    initial_usage_lines = 0
    usage_path = next((p for p in USAGE_LOG_CANDIDATES if p.exists()), USAGE_LOG_CANDIDATES[0])
    if usage_path.exists():
        initial_usage_lines = sum(1 for _ in usage_path.open(encoding="utf-8"))
    totals = {"messages_sent": 0, "gemini_calls": 0, "tokens_in": 0, "tokens_out": 0,
              "estimated_cost_usd": None}
    doc = {"run_id": run_id, "started_at": started_at, "finished_at": None,
           "target_identity_match": False, "limits": limits, "results": results,
           "totals": totals, "blockers": blockers, "stop_reason": stop_reason,
           "ai_usage_log": str(usage_path.relative_to(ROOT)) if usage_path.is_relative_to(ROOT) else "backend/logs/ai_usage.jsonl"}

    if args.offline:
        _record(results, "S0", "BLOCKED", "Live EventSub delivery and Gemini reachability are observed.",
                "Offline mode deliberately makes no Twitch, Gemini, or app API requests.")
        for scenario_id in list(SCENARIOS)[1:11]:
            _record(results, scenario_id, "BLOCKED", "Scenario is executed against the live demo channel.",
                    "No live scenario was run; this mode performs no network or account action.")
        _record(results, "S11", "NOT BUILT", "Playwright captures and console checks are recorded.",
                "Automated screenshot capture is not implemented in the harness.")
        blockers.append("Live run deferred by the owner; no provider/API requests or chat actions were made.")
        doc["finished_at"] = datetime.now(UTC).isoformat()
        doc["stop_reason"] = "offline_report_only"
        _write_report(doc)
        print(json.dumps(_redact(doc), indent=2))
        print(f"Offline scenario report written to {REPORT_MD.relative_to(ROOT)} and {REPORT_JSON.relative_to(ROOT)}")
        return 0

    blockers.append("Live mode is disabled: the complete scenario driver and evidence capture are not implemented. No network requests were made.")
    for scenario_id in list(SCENARIOS)[:-1]:
        _record(results, scenario_id, "BLOCKED", "The live scenario is performed and evidenced.",
                "Live execution was refused before any app/provider API request.")
    _record(results, "S11", "NOT BUILT", "Playwright screenshots and console checks are captured.",
            "Automated screenshot capture is not implemented.")
    doc["finished_at"] = datetime.now(UTC).isoformat()
    doc["stop_reason"] = "live_mode_disabled_incomplete_scenario_driver"
    _write_report(doc)
    print("Live mode refused before network access: full scenario automation is not implemented.")
    print(f"Blocked-run report written to {REPORT_MD.relative_to(ROOT)} and {REPORT_JSON.relative_to(ROOT)}")
    return 2

    channel_login = os.environ.get("LIVE_TEST_CHANNEL_LOGIN", "").strip().casefold()
    if not channel_login:
        blockers.append("LIVE_TEST_CHANNEL_LOGIN is not set; channel identity cannot be confirmed, so all chat sends are refused.")
    dashboard_session = os.environ.get("LIVE_TEST_DASHBOARD_SESSION", "")
    if not dashboard_session:
        blockers.append("LIVE_TEST_DASHBOARD_SESSION is not set; the harness cannot reuse the dashboard's authenticated session.")
    if os.environ.get("DEMO_MODE", "").strip().casefold() not in ("false", "0", "no"):
        blockers.append("Environment DEMO_MODE is not explicitly false.")

    try:
        from app.config import get_settings
        settings = get_settings()
        if settings.demo_mode:
            blockers.append("Backend Settings DEMO_MODE is true.")
        if not settings.twitch_client_id or not settings.twitch_client_secret:
            blockers.append("Twitch app credentials are not configured.")
        if not settings.gemini_api_key:
            blockers.append("Gemini API key is not configured.")
    except Exception as exc:  # noqa: BLE001 - redact all provider/config exception details
        settings = None
        blockers.append(f"Backend settings unavailable ({type(exc).__name__}).")

    api_ok = False
    app_mode = None
    worker = None
    try:
        with _api_client(dashboard_session) as client:
            health = client.get(f"{API_BASE}/api/health")
            health.raise_for_status()
            status_response = client.get(f"{API_BASE}/api/status")
            status_response.raise_for_status()
            status_data = status_response.json()
            api_ok = True
            app_mode = "demo" if status_data.get("demo_mode") else "live"
            worker = status_data.get("eventsub_worker")
            if status_data.get("demo_mode"):
                blockers.append("Running API is in demo mode.")
            if status_data.get("eventsub_worker") != "running":
                blockers.append("Running API reports no active EventSub worker.")
            if not status_data.get("twitch_authorized"):
                blockers.append("Running API does not report an authenticated Twitch broadcaster.")
            if dashboard_session:
                auth_check = client.get(f"{API_BASE}/api/interactions")
                if auth_check.status_code != 200:
                    blockers.append(f"Dashboard API session was rejected (HTTP {auth_check.status_code}).")
            else:
                auth_check = client.get(f"{API_BASE}/api/interactions")
                if auth_check.status_code in (401, 403):
                    _record(results, "S1", "PASS", "Unauthenticated management API requests are rejected.",
                            f"management endpoint rejected unauthenticated request with HTTP {auth_check.status_code}.",
                            {"http_status": auth_check.status_code})
    except Exception as exc:  # noqa: BLE001 - report only the exception type
        blockers.append(f"Backend health/status/API check failed ({type(exc).__name__}).")

    try:
        _check_db_writable()
        _record(results, "S1", "PASS", "Application database accepts a reversible temporary write.",
                "temporary table create/insert/read/drop succeeded.")
    except Exception as exc:  # noqa: BLE001 - report only the exception type
        blockers.append(f"Database write check failed ({type(exc).__name__}).")

    try:
        tenant_evidence = _test_isolated_tenant_db()
        _record(results, "S1", "PASS", "Synthetic broadcaster records remain tenant scoped in an isolated test database.",
                "isolated tenant query returned only its owner's row.", tenant_evidence)
    except Exception as exc:  # noqa: BLE001 - report only the exception type
        _record(results, "S1", "FAIL", "Tenant data is scoped to broadcaster.",
                f"isolated tenant check failed ({type(exc).__name__}).")

    # Test auth rejection independently of the available dashboard session.
    try:
        with _api_client() as client:
            response = client.get(f"{API_BASE}/api/interactions")
        _record(results, "S1", "PASS" if response.status_code in (401, 403) else "FAIL",
                "Management API rejects unauthenticated access.",
                f"HTTP {response.status_code}.", {"http_status": response.status_code})
    except Exception as exc:  # noqa: BLE001 - report only the exception type
        _record(results, "S1", "BLOCKED", "Management API auth is checked.",
                f"API unreachable ({type(exc).__name__}).")

    ws_scheme = "wss" if API_BASE.startswith("https:") else "ws"
    ws_host = urlparse(API_BASE).netloc
    dashboard_rejected, dashboard_ws_status = _websocket_rejection(
        f"{ws_scheme}://{ws_host}/ws/dashboard", origin=FRONTEND_BASE)
    overlay_rejected, overlay_ws_status = _websocket_rejection(
        f"{ws_scheme}://{ws_host}/ws/overlay", origin=FRONTEND_BASE)
    _record(results, "S1", "PASS" if dashboard_rejected else "FAIL",
            "Dashboard WebSocket rejects a connection without authentication.",
            "connection rejected" if dashboard_rejected else "connection was accepted or rejection was not observed.",
            {"http_status": dashboard_ws_status})
    _record(results, "S1", "PASS" if overlay_rejected else "FAIL",
            "Overlay WebSocket rejects a connection without its private token.",
            "connection rejected" if overlay_rejected else "connection was accepted or rejection was not observed.",
            {"http_status": overlay_ws_status})

    twitch_identity = None
    twitch_token = None
    subscription_active = False
    try:
        twitch_token, broadcaster_id, twitch_identity = _twitch_main_credential()
        if not channel_login:
            blockers.append("Twitch token validated, but LIVE_TEST_CHANNEL_LOGIN is required before the identity can authorize sends.")
        elif twitch_identity["login"] != channel_login:
            blockers.append("LIVE_TEST_CHANNEL_LOGIN does not match the saved Twitch broadcaster; no chat can be sent.")
        else:
            doc["target_identity_match"] = True
        if settings and twitch_identity["client_id"] != settings.twitch_client_id:
            blockers.append("Saved broadcaster OAuth token belongs to a different Twitch app client ID.")
        subscriptions = httpx.get(
            "https://api.twitch.tv/helix/eventsub/subscriptions",
            headers={"Authorization": f"Bearer {twitch_token}", "Client-Id": settings.twitch_client_id},
            params={"status": "enabled"}, timeout=10,
        )
        subscriptions.raise_for_status()
        rows = subscriptions.json().get("data", [])
        subscription_active = any(row.get("type") == "channel.chat.message"
                                  and row.get("condition", {}).get("broadcaster_user_id") == broadcaster_id
                                  and row.get("status") == "enabled"
                                  for row in rows)
        if not subscription_active:
            blockers.append("Twitch has no enabled channel.chat.message EventSub subscription for the connected broadcaster.")
    except Exception as exc:  # noqa: BLE001 - provider SDK exceptions vary
        blockers.append(f"Twitch credential validation/refresh or subscription check failed ({type(exc).__name__}).")

    _record(results, "S0", "BLOCKED", "The expected demo broadcaster token validates and its chat EventSub subscription is enabled; then a synthetic canary is received.",
            "Twitch credential and subscription checked; receiving can only be verified after the guarded synthetic canary.",
            {"channel_identity_match": doc["target_identity_match"], "eventsub_enabled": subscription_active,
             "eventsub_receiving": False})

    # A tiny direct Gemini request is a provider preflight call and consumes one call from the cap.
    if settings and settings.gemini_api_key:
        if totals["gemini_calls"] >= limits["gemini_calls"]:
            stop_reason = "LIVE_TEST_MAX_GEMINI_CALLS reached during preflight; no scenarios were started."
        else:
            try:
                from google import genai
                started = time.monotonic()
                client = genai.Client(api_key=settings.gemini_api_key,
                                      http_options={"timeout": min(10000, max(1000, settings.ai_request_timeout_seconds * 1000))})
                totals["gemini_calls"] += 1
                response = client.models.generate_content(model=settings.gemini_model,
                    contents='Return exactly the word "ready".', config={"max_output_tokens": 8, "temperature": 0})
                usage = getattr(response, "usage_metadata", None)
                totals["tokens_in"] += int(getattr(usage, "prompt_token_count", 0) or 0)
                totals["tokens_out"] += int(getattr(usage, "candidates_token_count", 0) or 0)
                _record(results, "S0", "PASS", "Gemini responds to a tiny non-user-data preflight request.",
                        "Gemini returned a response; body omitted from report.",
                        {"latency_ms": round((time.monotonic() - started) * 1000), "tokens_in": totals["tokens_in"], "tokens_out": totals["tokens_out"]})
            except Exception as exc:  # noqa: BLE001 - provider SDK exceptions vary
                blockers.append(f"Gemini reachability call failed ({type(exc).__name__}).")
                _record(results, "S0", "BLOCKED", "Gemini answers a tiny request.",
                        f"provider call failed ({type(exc).__name__}); response omitted.")

    try:
        viewer_tokens = json.loads(VIEWERS_FILE.read_text(encoding="utf-8"))
        if not isinstance(viewer_tokens, dict) or not viewer_tokens:
            raise ValueError("no_viewers")
    except (OSError, ValueError, json.JSONDecodeError):
        viewer_tokens = {}
        blockers.append("No locally authorized synthetic viewer tokens were found; run authorize_test_viewer.py for each account.")

    # Validate all viewer credentials before any message can be sent.
    viewer_safe: dict[str, dict] = {}
    for alias, record in viewer_tokens.items():
        try:
            validation = httpx.get("https://id.twitch.tv/oauth2/validate",
                headers={"Authorization": f"OAuth {record['access_token']}"}, timeout=10)
            validation.raise_for_status()
            value = validation.json()
            scopes = set(value.get("scopes", []))
            if scopes != {"user:write:chat"}:
                raise ValueError("viewer_scope_not_exact")
            if twitch_identity and value.get("user_id") == twitch_identity["user_id"]:
                raise ValueError("viewer_is_broadcaster")
            if settings and value.get("client_id") != settings.twitch_client_id:
                raise ValueError("viewer_app_client_mismatch")
            viewer_safe[alias] = {"access_token": record["access_token"],
                                  "user_id": str(value["user_id"]),
                                  "login": str(value.get("login", "")).casefold()}
        except Exception as exc:  # noqa: BLE001 - never include the token response in output
            blockers.append(f"Synthetic viewer alias {alias!r} failed validation ({type(exc).__name__}); its token is not usable.")
    if not viewer_safe:
        blockers.append("No validated non-broadcaster viewer account is available.")
    elif len(viewer_safe) == 1:
        blockers.append("Only one viewer account is authorized; it may be reused, but distinct asker/poll counts will be lower.")

    if not args.confirm_live_synthetic and not args.preflight_only:
        blockers.append("Use --confirm-live-synthetic to explicitly permit only the checked-in synthetic chat schedule.")
    if not args.allow_streamer_actions and not args.preflight_only:
        blockers.append("Use --allow-streamer-actions to permit separately listed explicit streamer approval/send actions.")
    if not dashboard_session:
        blockers.append("Dashboard-authenticated API/WS tests and streamer actions cannot run without LIVE_TEST_DASHBOARD_SESSION.")
    if not api_ok or app_mode != "live" or worker != "running":
        blockers.append("The running API must report live mode, Twitch authorization, and an active EventSub worker before the synthetic canary.")

    # Detect implemented surface by route text; report missing capabilities honestly rather than treating them as failures.
    source = (BACKEND / "app/main.py").read_text(encoding="utf-8")
    route_checks = {
        "S2": ("/api/interactions",), "S3": ("/response/suggest", "/api/responses/{response_id}/send"),
        "S4": ("/api/polls/suggestions", "/api/polls/suggestions/{suggestion_id}/approve"),
        "S6": ("/api/moderation/incidents",), "S8": ("/api/analytics/current", "/api/analytics/current/end"),
        "S10": ("/api/polls/twitch/create", "/api/polls/twitch/{poll_id}/end"),
    }
    for scenario_id, routes in route_checks.items():
        missing = [route for route in routes if route not in source]
        if missing:
            _record(results, scenario_id, "NOT BUILT", "Required feature/API exists.",
                    "Required API surface is absent from the inspected source.", {"missing_routes": missing})

    # The detailed sequence requires a ready preflight. This fail-closed first run never sends partial chat.
    hard_blockers = list(dict.fromkeys(blockers))
    if hard_blockers:
        stop_reason = "Preflight gate blocked; no synthetic viewer message was sent."
        for scenario_id in ("S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10", "S11"):
            if not any(row["scenario_id"] == scenario_id and row["status"] == "NOT BUILT" for row in results):
                status = "NOT BUILT" if scenario_id in ("S9", "S11") else "BLOCKED"
                detail = ("Safe automated backend restart/Playwright screenshot workflow is not implemented in this harness yet."
                          if status == "NOT BUILT" else "Live scenario withheld because the global preflight gate did not pass.")
                _record(results, scenario_id, status, SCENARIOS[scenario_id], detail,
                        {"blockers_count": len(hard_blockers)})
        doc["blockers"] = hard_blockers
        doc["stop_reason"] = stop_reason
    else:
        # Keep live effects fenced until the complete scenario driver is selected and evidence is observable.
        stop_reason = "Preflight passed. Scenario automation must be explicitly reviewed before enabling provider-side steps."
        blockers.append("Full scenario driver (reply/send, poll launch/vote, moderation review, session end and UI captures) is not yet implemented.")
        doc["blockers"] = blockers
        doc["stop_reason"] = stop_reason
        for scenario_id in ("S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10", "S11"):
            status = "NOT BUILT" if scenario_id in ("S9", "S11") else "BLOCKED"
            _record(results, scenario_id, status, SCENARIOS[scenario_id], stop_reason)

    # Estimate cost from app metadata emitted since the run began, plus the direct preflight call.
    current_usage = []
    if usage_path.exists():
        try:
            current_usage = [json.loads(line) for index, line in enumerate(usage_path.open(encoding="utf-8"))
                             if index >= initial_usage_lines and line.strip()]
        except (OSError, json.JSONDecodeError):
            current_usage = []
    for entry in current_usage:
        totals["gemini_calls"] += 1
        totals["tokens_in"] += int(entry.get("tokens_in") or 0)
        totals["tokens_out"] += int(entry.get("tokens_out") or 0)
    try:
        pricing = json.loads((ROOT / "config/gemini_pricing.json").read_text(encoding="utf-8"))
        rates = pricing["model_rates"].get(settings.gemini_model, {}) if settings else {}
        if rates:
            cost = (totals["tokens_in"] * rates["input_usd_per_million_tokens"]
                    + totals["tokens_out"] * rates["output_usd_per_million_tokens"]) / 1_000_000
            totals["estimated_cost_usd"] = round(cost, 8)
    except Exception:  # noqa: BLE001 - pricing is optional and failure has no provider impact
        totals["estimated_cost_usd"] = None
    doc["finished_at"] = datetime.now(UTC).isoformat()
    _write_report(doc)
    print(f"Preflight report written: {REPORT_MD.relative_to(ROOT)}")
    print(f"Structured results written: {REPORT_JSON.relative_to(ROOT)}")
    print(f"Messages sent: {totals['messages_sent']}; Gemini calls: {totals['gemini_calls']}; stop reason: {stop_reason}")
    return 0 if not blockers else 2


if __name__ == "__main__":
    raise SystemExit(_main())
