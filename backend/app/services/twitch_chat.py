import httpx
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.database import SessionLocal
from app.models import TwitchCredential
from app.services.twitch_auth import refresh_access_token_if_needed


async def send_chat_message_checked(credential: TwitchCredential, message: str, db=None) -> dict[str, str | None]:
    """Explicit send path that preserves safe Helix error categories and response message ID."""
    settings = get_settings()
    if not settings.twitch_client_id or not credential.broadcaster_id or not message:
        raise HTTPException(status_code=503, detail="Twitch chat is not configured for this broadcaster.")
    if len(message) > 500:
        raise HTTPException(status_code=422, detail="Twitch chat messages must be 500 characters or fewer.")
    own_session = db is None
    db = db or SessionLocal()
    try:
        current = await run_in_threadpool(db.get, TwitchCredential, credential.broadcaster_id)
        if current is None:
            raise HTTPException(status_code=401, detail="Reconnect Twitch before sending a chat reply.")
        token = await refresh_access_token_if_needed(current, db)
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5)) as client:
            response = await client.post(
                "https://api.twitch.tv/helix/chat/messages",
                headers={"Authorization": f"Bearer {token}", "Client-Id": settings.twitch_client_id,
                         "Content-Type": "application/json"},
                json={"broadcaster_id": credential.broadcaster_id, "sender_id": credential.broadcaster_id,
                      "message": message},
            )
            if response.status_code == 429:
                raise HTTPException(status_code=429, detail="Twitch is rate limiting chat. Wait briefly, then try again.")
            if response.status_code in (401, 403):
                raise HTTPException(status_code=502, detail="Twitch rejected chat authorization. Reconnect Twitch and verify chat permissions.")
            if response.status_code >= 500:
                raise HTTPException(status_code=502, detail="Twitch chat is temporarily unavailable. Try again shortly.")
            if response.is_error:
                raise HTTPException(status_code=502, detail="Twitch could not accept this chat reply.")
            data = response.json().get("data", [{}])[0]
            if not data.get("is_sent"):
                drop = data.get("drop_reason") or {}
                code = str(drop.get("code", ""))
                if "rate" in code.lower():
                    raise HTTPException(status_code=429, detail="Twitch is rate limiting chat. Wait briefly, then try again.")
                raise HTTPException(status_code=502, detail="Twitch did not send this message. Check channel chat restrictions and try again.")
            return {"message_id": data.get("message_id")}
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, TypeError, AttributeError, IndexError, KeyError) as exc:
        raise HTTPException(status_code=502, detail="Twitch chat send failed. Try again shortly.") from exc
    finally:
        if own_session:
            await run_in_threadpool(db.close)


async def send_chat_message(credential: TwitchCredential, message: str, db=None) -> bool:
    try:
        # Existing challenge announcements and explicit AI replies share one Helix implementation.
        await send_chat_message_checked(credential, message[:500], db)
        return True
    except (httpx.HTTPError, HTTPException, ValueError, IndexError, KeyError):
        return False
