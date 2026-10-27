import asyncio
import json
import logging
import re

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from app.config import get_settings
from app.database import SessionLocal
from app.models import Challenge, ChatMessage, StreamSession, TwitchCredential
from app.services.overlay import broadcast
from app.services.twitch_auth import refresh_access_token_if_needed
from app.services.voting import cast_vote

EVENTSUB_URL = "wss://eventsub.wss.twitch.tv/ws"
VOTE_PATTERN = re.compile(r"^\s*!vote\s+([1-4])\s*$", re.IGNORECASE)
logger = logging.getLogger(__name__)


async def _subscribe(session_id: str, broadcaster_id: str, token: str) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5)) as client:
        response = await client.post(
            "https://api.twitch.tv/helix/eventsub/subscriptions",
            headers={"Authorization": f"Bearer {token}", "Client-Id": settings.twitch_client_id,
                     "Content-Type": "application/json"},
            json={"type": "channel.chat.message", "version": "1",
                  "condition": {"broadcaster_user_id": broadcaster_id, "user_id": broadcaster_id},
                  "transport": {"method": "websocket", "session_id": session_id}},
        )
        response.raise_for_status()
        logger.info("Twitch EventSub chat subscription accepted")


async def _process_notification(broadcaster_id: str, metadata: dict, payload: dict) -> None:
    event = payload.get("event", {})
    message = event.get("message", {}).get("text", "")
    if not isinstance(message, str):
        return
    if event.get("broadcaster_user_id") != broadcaster_id:
        return
    chatter_id = str(event.get("chatter_user_id", ""))
    if not chatter_id:
        return

    event_id = metadata.get("message_id")
    db = SessionLocal()
    try:
        session = db.scalar(select(StreamSession).where(
            StreamSession.broadcaster_id == broadcaster_id,
            StreamSession.ended_at.is_(None),
        ).order_by(StreamSession.id.desc()))
        if session and event_id and isinstance(message, str):
            inserted = db.execute(sqlite_insert(ChatMessage).values(
                event_id=str(event_id)[:128], session_id=session.id,
                chatter_id=chatter_id[:80],
                display_name=str(event.get("chatter_user_name", "Viewer"))[:80],
                text=message[:500].replace("\x00", ""),
            ).on_conflict_do_nothing(index_elements=[ChatMessage.event_id]))
            if inserted.rowcount:
                oldest = db.scalars(select(ChatMessage.event_id).where(
                    ChatMessage.session_id == session.id
                ).order_by(ChatMessage.received_at.desc(), ChatMessage.event_id.desc()).offset(500)).all()
                if oldest:
                    db.execute(delete(ChatMessage).where(ChatMessage.event_id.in_(oldest)))
                db.commit()
    finally:
        db.close()

    # Persist broadcaster chat so the live signal panel can show streamer tests and
    # context, but never accept broadcaster vote commands.
    if chatter_id == broadcaster_id:
        return

    match = VOTE_PATTERN.fullmatch(message[:40])
    if not match:
        return
    db = SessionLocal()
    try:
        active = db.scalar(select(Challenge).where(
            Challenge.status == "active"
        ).order_by(Challenge.id.desc()))
        if not active:
            return
        result = cast_vote(
            db, challenge_id=active.id, twitch_user_id=chatter_id,
            option_number=int(match.group(1)), event_id=metadata.get("message_id"),
        )
        if result is not None:
            session = db.get(StreamSession, active.session_id)
            if session and session.overlay_token_hash:
                await broadcast(session.overlay_token_hash, {"type": "vote.updated", "challenge": result})
    finally:
        db.close()


async def run_eventsub(broadcaster_id: str) -> None:
    """Own the EventSub socket; messages are data only and votes are parsed narrowly."""
    logger.info("Twitch EventSub worker started")
    uri = EVENTSUB_URL
    should_subscribe = True
    delay = 1
    while True:
        db = SessionLocal()
        credential = db.get(TwitchCredential, broadcaster_id)
        if not credential or not credential.auth_session_hash:
            db.close()
            return
        try:
            token = await refresh_access_token_if_needed(credential, db)
        except HTTPException as exc:
            db.close()
            if exc.status_code == 401:
                logger.warning("Twitch EventSub authorization expired or was revoked")
                return
            logger.warning("Twitch EventSub token refresh failed (HTTP %d); retrying", exc.status_code)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
            continue
        db.close()
        try:
            async with connect(uri, open_timeout=15, ping_interval=30, ping_timeout=20, max_size=1_000_000) as socket:
                delay = 1
                logger.info("Twitch EventSub WebSocket connected")
                while True:
                    try:
                        raw = await asyncio.wait_for(socket.recv(), timeout=1)
                    except TimeoutError:
                        continue
                    message = json.loads(raw)
                    metadata = message.get("metadata", {})
                    message_type = metadata.get("message_type")
                    payload = message.get("payload", {})
                    if message_type == "session_welcome":
                        logger.info("Twitch EventSub session welcomed")
                        if should_subscribe:
                            await _subscribe(payload["session"]["id"], broadcaster_id, token)
                        should_subscribe = True
                    elif message_type == "session_reconnect":
                        uri = payload["session"]["reconnect_url"]
                        should_subscribe = False
                        break
                    elif message_type == "notification" and metadata.get("subscription_type") == "channel.chat.message":
                        await _process_notification(broadcaster_id, metadata, payload)
                    elif message_type == "revocation":
                        revoked = SessionLocal()
                        try:
                            current = revoked.get(TwitchCredential, broadcaster_id)
                            if current:
                                current.auth_session_hash = None
                                revoked.commit()
                        finally:
                            revoked.close()
                        return
        except asyncio.CancelledError:
            raise
        except (OSError, TimeoutError, httpx.HTTPError, ConnectionClosed, SQLAlchemyError, ValueError, KeyError, RuntimeError) as exc:
            logger.warning("Twitch EventSub disconnected or failed (%s); retrying", type(exc).__name__)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
