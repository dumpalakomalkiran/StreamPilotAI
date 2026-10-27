import httpx
from fastapi import HTTPException

from app.config import get_settings
from app.database import SessionLocal
from app.models import TwitchCredential
from app.services.twitch_auth import refresh_access_token_if_needed


async def send_chat_message(credential: TwitchCredential, message: str, db=None) -> bool:
    settings = get_settings()
    if not settings.twitch_client_id or not credential.broadcaster_id or not message:
        return False
    payload = {
        "broadcaster_id": credential.broadcaster_id,
        "sender_id": credential.broadcaster_id,
        "message": message[:500],
    }
    own_session = db is None
    db = db or SessionLocal()
    try:
        current = db.get(TwitchCredential, credential.broadcaster_id)
        if current is None:
            return False
        token = await refresh_access_token_if_needed(current, db)
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5)) as client:
            response = await client.post(
                "https://api.twitch.tv/helix/chat/messages",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Client-Id": settings.twitch_client_id,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            return bool(response.json().get("data", [{}])[0].get("is_sent"))
    except (httpx.HTTPError, HTTPException, ValueError, IndexError, KeyError):
        return False
    finally:
        if own_session:
            db.close()
