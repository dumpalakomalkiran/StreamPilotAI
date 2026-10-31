"""Run read-only integration checks plus a Twitch refresh-token rotation check.

This intentionally performs a Twitch token refresh (rotating stored OAuth tokens),
Helix reads, and one tiny Gemini request. It never sends Twitch chat or launches polls.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

import httpx
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.database import SessionLocal, engine
from app.models import TwitchCredential
from app.services.twitch_auth import refresh_access_token_if_needed


def report(name: str, passed: bool, detail: str) -> bool:
    print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}")
    return passed


async def check_twitch(settings) -> list[bool]:
    results = []
    if not settings.twitch_client_id or not settings.twitch_client_secret:
        return [report("Twitch configuration", False,
                       "TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET must be set")]
    db = SessionLocal()
    try:
        credential = db.query(TwitchCredential).filter(
            TwitchCredential.auth_session_hash.is_not(None)
        ).order_by(TwitchCredential.broadcaster_id).first()
        if credential is None:
            return [report("Twitch credential", False,
                           "No connected broadcaster token is stored; connect Twitch first")]
        broadcaster_id = credential.broadcaster_id
        try:
            token = await refresh_access_token_if_needed(credential, db, force=True)
            results.append(report("Twitch refresh", True,
                                  "refresh endpoint accepted the encrypted refresh token; rotated tokens saved"))
        except Exception as exc:  # noqa: BLE001 - never print provider bodies or credential fields.
            return [report("Twitch refresh", False, f"refresh failed ({type(exc).__name__})")]
        timeout = httpx.Timeout(12, connect=5)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.get(
                    "https://id.twitch.tv/oauth2/validate",
                    headers={"Authorization": f"OAuth {token}"},
                )
                if response.is_error:
                    results.append(report("Twitch token validation", False,
                                          f"Twitch returned HTTP {response.status_code}"))
                    return results
                token_data = response.json()
                valid_identity = (str(token_data.get("user_id", "")) == broadcaster_id
                                  and token_data.get("client_id") == settings.twitch_client_id)
                grants = set(token_data.get("scopes", []))
                required = set(settings.twitch_scopes.split())
                scope_ok = required.issubset(grants)
                results.append(report("Twitch token validation", valid_identity and scope_ok,
                                      "token identity and configured chat scopes match" if valid_identity and scope_ok
                                      else "identity or configured chat scopes do not match"))
                response = await client.get(
                    "https://api.twitch.tv/helix/eventsub/subscriptions",
                    headers={"Authorization": f"Bearer {token}", "Client-Id": settings.twitch_client_id},
                    params={"type": "channel.chat.message", "first": 100},
                )
                if response.is_error:
                    results.append(report("EventSub subscription", False,
                                          f"Helix returned HTTP {response.status_code}"))
                else:
                    entries = response.json().get("data", [])
                    active = any(
                        item.get("status") == "enabled"
                        and item.get("type") == "channel.chat.message"
                        and item.get("condition", {}).get("broadcaster_user_id") == broadcaster_id
                        and item.get("transport", {}).get("method") == "websocket"
                        and not item.get("transport", {}).get("disconnected_at")
                        for item in entries
                    )
                    results.append(report("EventSub subscription", active,
                                          "enabled WebSocket chat subscription found for connected broadcaster"
                                          if active else "no active WebSocket chat subscription found"))
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                results.append(report("Twitch API checks", False, f"request failed ({type(exc).__name__})"))
        return results
    finally:
        db.close()


async def main() -> int:
    settings = get_settings()
    checks = []
    missing = []
    if not settings.app_secret_key or settings.app_secret_key == "local-only-change-me" or len(settings.app_secret_key) < 32:
        missing.append("APP_SECRET_KEY (unique value, at least 32 characters)")
    if not settings.database_url:
        missing.append("DATABASE_URL")
    if not settings.twitch_client_id:
        missing.append("TWITCH_CLIENT_ID")
    if not settings.twitch_client_secret:
        missing.append("TWITCH_CLIENT_SECRET")
    if not settings.gemini_api_key:
        missing.append("GEMINI_API_KEY")
    if not settings.gemini_model:
        missing.append("GEMINI_MODEL")
    checks.append(report("Required environment", not missing,
                         "all required settings are present" if not missing
                         else "missing/invalid: " + ", ".join(missing)))
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(text("CREATE TEMP TABLE streampilot_health_check (value INTEGER NOT NULL)"))
            connection.execute(text("INSERT INTO streampilot_health_check(value) VALUES (1)"))
            writable = connection.execute(text(
                "SELECT value FROM streampilot_health_check"
            )).scalar_one() == 1
            connection.execute(text("DROP TABLE streampilot_health_check"))
        checks.append(report("Database writable", writable, "temporary write/read/cleanup succeeded"))
    except (OSError, RuntimeError, ValueError, SQLAlchemyError) as exc:  # Do not surface connection strings or file paths.
        checks.append(report("Database writable", False, f"database operation failed ({type(exc).__name__})"))
    if settings.twitch_client_id and settings.twitch_client_secret:
        checks.extend(await check_twitch(settings))
    else:
        checks.append(report("Twitch API", False, "required Twitch settings are missing"))
    if settings.gemini_api_key and settings.gemini_model:
        try:
            from google import genai

            started = time.monotonic()
            client = genai.Client(api_key=settings.gemini_api_key,
                                  http_options={"timeout": 10_000})
            response = await asyncio.wait_for(asyncio.to_thread(
                client.models.generate_content,
                model=settings.gemini_model,
                contents='Return exactly the two letters "OK".',
                config={"max_output_tokens": 8, "temperature": 0},
            ), timeout=12)
            elapsed = time.monotonic() - started
            checks.append(report("Gemini tiny call", bool(response.text and response.text.strip()),
                                 f"configured model responded in {elapsed:.1f}s" if response.text
                                 else "model returned an empty response"))
        except Exception as exc:  # noqa: BLE001 - avoid printing SDK errors that may include request details.
            checks.append(report("Gemini tiny call", False, f"request failed ({type(exc).__name__})"))
    else:
        checks.append(report("Gemini tiny call", False, "GEMINI_API_KEY or GEMINI_MODEL is missing"))
    passed = all(checks)
    print("\nAll checks passed." if passed else "\nOne or more checks failed. Provider secrets and response bodies were not printed.")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
