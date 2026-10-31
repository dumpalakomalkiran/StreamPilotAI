import asyncio
import base64
import hashlib
import secrets
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from weakref import WeakKeyDictionary

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import TwitchCredential

_pending_states: dict[str, tuple[float, frozenset[str]]] = {}
_refresh_locks: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = WeakKeyDictionary()


def _cipher() -> Fernet:
    secret = get_settings().app_secret_key
    if secret == "local-only-change-me" or "replace-with" in secret.lower() or len(secret) < 32:
        raise HTTPException(status_code=503, detail="Set a unique APP_SECRET_KEY of at least 32 characters")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_token(token: str) -> str:
    return _cipher().encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_token(token: str) -> str:
    try:
        return _cipher().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=401, detail="Stored Twitch authorization must be renewed") from exc


NATIVE_POLL_SCOPE = "channel:manage:polls"


def create_authorization_url(include_native_polls: bool = False) -> tuple[str, str]:
    settings = get_settings()
    if not settings.twitch_client_id or not settings.twitch_client_secret:
        raise HTTPException(status_code=503, detail="Twitch authorization is not configured")
    now = time.monotonic()
    for state, entry in list(_pending_states.items()):
        created = entry[0] if isinstance(entry, tuple) else entry
        if now - created > 600:
            _pending_states.pop(state, None)
    if len(_pending_states) >= 5_000:
        raise HTTPException(status_code=429, detail="Twitch authorization is busy. Try again shortly.")
    state = secrets.token_urlsafe(32)
    scopes = set(settings.twitch_scopes.split())
    if include_native_polls:
        scopes.add(NATIVE_POLL_SCOPE)
    else:
        scopes.discard(NATIVE_POLL_SCOPE)
    requested_scopes = frozenset(scopes)
    _pending_states[state] = (now, requested_scopes)
    query = urlencode({
        "client_id": settings.twitch_client_id,
        "redirect_uri": settings.twitch_redirect_uri,
        "response_type": "code",
        "scope": " ".join(sorted(requested_scopes)),
        "state": state,
        "force_verify": "true",
    })
    return f"https://id.twitch.tv/oauth2/authorize?{query}", state


async def exchange_callback(code: str, state: str, cookie_state: str | None, db: Session) -> dict[str, str]:
    settings = get_settings()
    state_entry = _pending_states.pop(state, None)
    created = state_entry[0] if isinstance(state_entry, tuple) else state_entry
    if not cookie_state or not secrets.compare_digest(state, cookie_state) or created is None or time.monotonic() - created > 600:
        raise HTTPException(status_code=400, detail="OAuth state is invalid or expired")
    if not settings.twitch_client_id or not settings.twitch_client_secret:
        raise HTTPException(status_code=503, detail="Twitch authorization is not configured")
    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post("https://id.twitch.tv/oauth2/token", data={
                "client_id": settings.twitch_client_id,
                "client_secret": settings.twitch_client_secret,
                "code": code[:2048],
                "grant_type": "authorization_code",
                "redirect_uri": settings.twitch_redirect_uri,
            })
            response.raise_for_status()
            token = response.json()
            access_token = token["access_token"]
            identity = await client.get(
                "https://api.twitch.tv/helix/users",
                headers={"Authorization": f"Bearer {access_token}", "Client-Id": settings.twitch_client_id},
            )
            identity.raise_for_status()
            user = identity.json()["data"][0]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise HTTPException(status_code=502, detail="Twitch authorization could not be completed") from exc

    broadcaster_id = str(user["id"])
    required_scopes = set(state_entry[1]) if isinstance(state_entry, tuple) else set(settings.twitch_scopes.split())
    if not required_scopes.issubset(set(token.get("scope", []))):
        raise HTTPException(status_code=403, detail="Twitch did not grant the required chat permissions")
    expires_at = datetime.now(UTC) + timedelta(seconds=max(0, int(token.get("expires_in", 0))))
    def store_credential() -> None:
        credential = db.get(TwitchCredential, broadcaster_id)
        if credential is None:
            credential = TwitchCredential(broadcaster_id=broadcaster_id)
            db.add(credential)
        credential.access_token = encrypt_token(access_token)
        credential.refresh_token = encrypt_token(str(token.get("refresh_token", "")))
        credential.scopes = " ".join(token.get("scope", []))
        credential.expires_at = expires_at
        db.commit()

    await asyncio.to_thread(store_credential)
    return {"broadcaster_id": broadcaster_id, "login": str(user.get("login", ""))}


async def refresh_access_token_if_needed(credential: TwitchCredential, db: Session) -> str:
    settings = get_settings()
    loop = asyncio.get_running_loop()
    lock = _refresh_locks.setdefault(loop, {}).setdefault(credential.broadcaster_id, asyncio.Lock())
    async with lock:
        # Another task may have refreshed this row while we were waiting.
        await asyncio.to_thread(db.refresh, credential)
        expires_at = credential.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at > datetime.now(UTC) + timedelta(seconds=90):
            return decrypt_token(credential.access_token)
        refresh_token = decrypt_token(credential.refresh_token)
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5)) as client:
            try:
                response = await client.post("https://id.twitch.tv/oauth2/token", data={
                    "client_id": settings.twitch_client_id,
                    "client_secret": settings.twitch_client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                })
                response.raise_for_status()
                data = response.json()
                access_token = str(data["access_token"])

                def store_rotated_tokens() -> None:
                    credential.access_token = encrypt_token(access_token)
                    credential.refresh_token = encrypt_token(data.get("refresh_token", refresh_token))
                    credential.expires_at = datetime.now(UTC) + timedelta(seconds=int(data["expires_in"]))
                    db.commit()

                await asyncio.to_thread(store_rotated_tokens)
                return access_token
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (400, 401, 403):
                    raise HTTPException(status_code=502, detail="Twitch is temporarily unavailable") from exc
                await asyncio.to_thread(_clear_auth_session, credential, db)
                raise HTTPException(status_code=401, detail="Twitch authorization expired; reconnect required") from exc
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=502, detail="Twitch is temporarily unavailable") from exc
            except (KeyError, ValueError) as exc:
                await asyncio.to_thread(_clear_auth_session, credential, db)
                raise HTTPException(status_code=401, detail="Twitch authorization expired; reconnect required") from exc


def _clear_auth_session(credential: TwitchCredential, db: Session) -> None:
    credential.auth_session_hash = None
    db.commit()
