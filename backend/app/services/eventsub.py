import asyncio
import json
import logging
import re
from datetime import UTC, datetime

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from app.config import get_settings
from app.database import SessionLocal
from app.models import (
    Challenge,
    ChatMessage,
    DeletedChatMessage,
    Interaction,
    ProcessedEvent,
    StreamSession,
    TwitchCredential,
)
from app.services.dashboard_socket import broadcast as broadcast_dashboard
from app.services.dashboard_socket import interaction_payload
from app.services.interactions import (
    delete_interactions_for_chat_messages,
    event_is_emote_only,
    ingest_message,
)
from app.services.overlay import broadcast
from app.services.question_overlay import broadcast as broadcast_question_overlay
from app.services.twitch_auth import refresh_access_token_if_needed
from app.services.voting import cast_vote

EVENTSUB_URL = "wss://eventsub.wss.twitch.tv/ws"
VOTE_PATTERN = re.compile(r"^\s*!vote\s+([1-4])\s*$", re.IGNORECASE)
CHAT_SUBSCRIPTION_TYPES = (
    "channel.chat.message",
    "channel.chat.message_delete",
    "channel.chat.clear_user_messages",
    "channel.chat.clear",
)
STREAM_SUBSCRIPTION_TYPES = ("stream.online", "stream.offline")
SUPPORTED_SUBSCRIPTION_TYPES = CHAT_SUBSCRIPTION_TYPES + STREAM_SUBSCRIPTION_TYPES
logger = logging.getLogger(__name__)


async def _subscribe(
    session_id: str, broadcaster_id: str, token: str, subscription_type: str
) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5)) as client:
        response = await client.post(
            "https://api.twitch.tv/helix/eventsub/subscriptions",
            headers={"Authorization": f"Bearer {token}", "Client-Id": settings.twitch_client_id,
                     "Content-Type": "application/json"},
            json={"type": subscription_type, "version": "1",
                  "condition": ({"broadcaster_user_id": broadcaster_id}
                                if subscription_type in STREAM_SUBSCRIPTION_TYPES
                                else {"broadcaster_user_id": broadcaster_id, "user_id": broadcaster_id}),
                  "transport": {"method": "websocket", "session_id": session_id}},
        )
        response.raise_for_status()
        logger.info("Twitch EventSub subscription accepted (type=%s)", subscription_type)


async def _subscribe_chat_events(session_id: str, broadcaster_id: str, token: str) -> None:
    for subscription_type in SUPPORTED_SUBSCRIPTION_TYPES:
        await _subscribe(session_id, broadcaster_id, token, subscription_type)


def _set_stream_live(broadcaster_id: str, is_live: bool, event_id: str) -> bool:
    db = SessionLocal()
    try:
        inserted = db.execute(sqlite_insert(ProcessedEvent).values(event_id=event_id[:128])
                              .on_conflict_do_nothing(index_elements=[ProcessedEvent.event_id]))
        if inserted.rowcount != 1:
            db.rollback()
            return False
        session = db.scalar(select(StreamSession).where(
            StreamSession.broadcaster_id == broadcaster_id,
            StreamSession.ended_at.is_(None),
        ).order_by(StreamSession.id.desc()))
        changed = False
        if is_live and session is None:
            db.add(StreamSession(broadcaster_id=broadcaster_id, mode="live"))
            changed = True
        elif not is_live and session is not None:
            session.ended_at = datetime.now(UTC)
            changed = True
            logger.info("StreamPilot session ended from Twitch stream.offline (broadcaster=%s)", broadcaster_id)
        db.commit()
        return changed
    finally:
        db.close()


def _invalidate_broadcaster_session(broadcaster_id: str) -> None:
    db = SessionLocal()
    try:
        current = db.get(TwitchCredential, broadcaster_id)
        if current:
            current.auth_session_hash = None
            db.commit()
    finally:
        db.close()


def _remember_deleted_messages(db, session_id: int, message_ids: list[str]) -> None:
    unique_ids = list(dict.fromkeys(
        message_id[:128] for message_id in message_ids
        if isinstance(message_id, str) and message_id
    ))
    if not unique_ids:
        return
    db.execute(sqlite_insert(DeletedChatMessage).values([
        {"event_id": message_id, "session_id": session_id} for message_id in unique_ids
    ]).on_conflict_do_nothing(index_elements=[DeletedChatMessage.event_id]))
    expired = db.scalars(select(DeletedChatMessage.event_id).where(
        DeletedChatMessage.session_id == session_id
    ).order_by(DeletedChatMessage.deleted_at.desc(),
               DeletedChatMessage.event_id.desc()).offset(500)).all()
    if expired:
        db.execute(delete(DeletedChatMessage).where(DeletedChatMessage.event_id.in_(expired)))


def _process_chat_removal(
    broadcaster_id: str, subscription_type: str, payload: dict
) -> tuple[str | None, list[dict[str, object]]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("event"), dict):
        logger.warning("Malformed Twitch chat removal event skipped (type=%s)", subscription_type)
        return None, []
    event = payload["event"]
    if event.get("broadcaster_user_id") != broadcaster_id:
        return None, []

    db = SessionLocal()
    try:
        session = db.scalar(select(StreamSession).where(
            StreamSession.broadcaster_id == broadcaster_id,
            StreamSession.ended_at.is_(None),
        ).order_by(StreamSession.id.desc()))
        if session is None:
            return None, []

        queue_events: list[dict[str, object]] = []

        if subscription_type == "channel.chat.message_delete":
            message_id = event.get("message_id")
            if not isinstance(message_id, str) or not message_id or len(message_id) > 128:
                logger.warning("Malformed Twitch chat deletion skipped (missing or invalid message ID)")
                return None, []
            _remember_deleted_messages(db, session.id, [message_id])
            queue_events.extend(delete_interactions_for_chat_messages(db, [message_id]))
            result = db.execute(delete(ChatMessage).where(
                ChatMessage.session_id == session.id,
                ChatMessage.event_id == message_id,
            ))
        elif subscription_type == "channel.chat.clear_user_messages":
            target_user_id = event.get("target_user_id")
            if not isinstance(target_user_id, str) or not target_user_id:
                logger.warning("Malformed Twitch user-clear event skipped (missing target user ID)")
                return None, []
            affected_ids = db.scalars(select(ChatMessage.event_id).where(
                ChatMessage.session_id == session.id,
                ChatMessage.chatter_id == target_user_id[:80],
            )).all()
            _remember_deleted_messages(db, session.id, affected_ids)
            queue_events.extend(delete_interactions_for_chat_messages(db, affected_ids))
            result = db.execute(delete(ChatMessage).where(
                ChatMessage.session_id == session.id,
                ChatMessage.chatter_id == target_user_id[:80],
            ))
        elif subscription_type == "channel.chat.clear":
            affected_ids = db.scalars(select(ChatMessage.event_id).where(
                ChatMessage.session_id == session.id
            )).all()
            _remember_deleted_messages(db, session.id, affected_ids)
            queue_events.extend(delete_interactions_for_chat_messages(db, affected_ids))
            result = db.execute(delete(ChatMessage).where(ChatMessage.session_id == session.id))
        else:
            return None, []

        overlay_hash = session.overlay_token_hash if session.featured_interaction_group_id is not None else None
        session.featured_interaction_group_id = None
        db.commit()
        logger.info("Twitch chat removal applied (type=%s, removed_count=%d)",
                    subscription_type, result.rowcount or 0)
        return overlay_hash, queue_events
    finally:
        db.close()


def _process_chat_message(
    broadcaster_id: str, metadata: dict, event: dict, message: str
) -> tuple[str | None, dict[str, object] | None]:
    chatter_id = str(event.get("chatter_user_id", ""))
    if not chatter_id:
        logger.warning("Twitch chat message without a chatter ID skipped")
        return None, None
    event_id = metadata.get("message_id")
    if not isinstance(event_id, str) or not event_id:
        logger.warning("Twitch chat message without an event ID skipped")
        return None, None

    db = SessionLocal()
    try:
        session = db.scalar(select(StreamSession).where(
            StreamSession.broadcaster_id == broadcaster_id,
            StreamSession.ended_at.is_(None),
        ).order_by(StreamSession.id.desc()))
        if session and not db.get(DeletedChatMessage, event_id[:128]):
            inserted = db.execute(sqlite_insert(ChatMessage).values(
                event_id=event_id[:128], session_id=session.id,
                chatter_id=chatter_id[:80],
                display_name=str(event.get("chatter_user_name", "Viewer"))[:80],
                text=message[:500].replace("\x00", ""),
            ).on_conflict_do_nothing(index_elements=[ChatMessage.event_id]))
            if inserted.rowcount:
                chat_row = db.get(ChatMessage, event_id[:128])
                if chat_row:
                    message_payload = event.get("message", {})
                    ingest_message(
                        db, broadcaster_id=broadcaster_id, session=session,
                        chat_message=chat_row,
                        is_bot=bool(event.get("chatter_is_verified_bot"))
                        or str(event.get("chatter_type", "")).casefold() == "bot"
                        or str(event.get("chatter_user_login", "")).casefold().endswith("bot"),
                        is_emote_only=event_is_emote_only(
                            message_payload.get("fragments") if isinstance(message_payload, dict) else None
                        ),
                    )
                oldest = db.scalars(select(ChatMessage.event_id).where(
                    ChatMessage.session_id == session.id
                ).order_by(ChatMessage.received_at.desc(), ChatMessage.event_id.desc()).offset(500)).all()
                if oldest:
                    delete_interactions_for_chat_messages(db, oldest)
                    db.execute(delete(ChatMessage).where(ChatMessage.event_id.in_(oldest)))
                db.commit()
    finally:
        db.close()

    if chatter_id == broadcaster_id:
        return None, None
    match = VOTE_PATTERN.fullmatch(message[:40])
    if not match:
        return None, None
    db = SessionLocal()
    try:
        active = db.scalar(select(Challenge).where(
            Challenge.status == "active"
        ).order_by(Challenge.id.desc()))
        if not active:
            return None, None
        result = cast_vote(
            db, challenge_id=active.id, twitch_user_id=chatter_id,
            option_number=int(match.group(1)), event_id=event_id,
        )
        session = db.get(StreamSession, active.session_id) if result is not None else None
        return (session.overlay_token_hash if session else None), result
    finally:
        db.close()


async def _process_notification(broadcaster_id: str, metadata: dict, payload: dict) -> None:
    if not isinstance(metadata, dict) or not isinstance(payload, dict):
        logger.warning("Malformed Twitch EventSub notification skipped")
        return
    subscription_type = metadata.get("subscription_type")
    if subscription_type not in SUPPORTED_SUBSCRIPTION_TYPES:
        logger.warning("Unknown Twitch EventSub notification skipped (type=%s)", subscription_type)
        return
    if subscription_type in STREAM_SUBSCRIPTION_TYPES:
        event = payload.get("event")
        event_id = metadata.get("message_id")
        if (not isinstance(event, dict) or event.get("broadcaster_user_id") != broadcaster_id
                or not isinstance(event_id, str) or not event_id):
            logger.warning("Malformed Twitch stream event skipped (type=%s)", subscription_type)
            return
        changed = await asyncio.to_thread(
            _set_stream_live, broadcaster_id, subscription_type == "stream.online", event_id
        )
        if changed:
            await broadcast_dashboard(broadcaster_id, {
                "type": "session.started" if subscription_type == "stream.online" else "session.ended"
            })
        return
    if subscription_type != "channel.chat.message":
        overlay_hash, queue_events = await asyncio.to_thread(_process_chat_removal, broadcaster_id, subscription_type, payload)
        for queue_event in queue_events:
            await broadcast_dashboard(broadcaster_id, queue_event)
        if overlay_hash:
            await broadcast_question_overlay(overlay_hash, {"type": "question.hidden"})
        return

    event = payload.get("event")
    if not isinstance(event, dict):
        logger.warning("Malformed Twitch chat message event skipped")
        return
    message_data = event.get("message")
    if not isinstance(message_data, dict) or not isinstance(message_data.get("text"), str):
        logger.warning("Malformed Twitch chat message body skipped")
        return
    message = message_data["text"]
    if event.get("broadcaster_user_id") != broadcaster_id:
        return
    overlay_hash, result = await asyncio.to_thread(
        _process_chat_message, broadcaster_id, metadata, event, message
    )
    if overlay_hash and result is not None:
        await broadcast(overlay_hash, {"type": "vote.updated", "challenge": result})
    queue_event = await asyncio.to_thread(_queue_event_for_message, broadcaster_id, metadata.get("message_id"))
    if queue_event:
        await broadcast_dashboard(broadcaster_id, queue_event)


def _queue_event_for_message(broadcaster_id: str, event_id: object) -> dict[str, object] | None:
    if not isinstance(event_id, str):
        return None
    db = SessionLocal()
    try:
        interaction = db.scalar(select(Interaction).where(
            Interaction.chat_message_id == event_id, Interaction.broadcaster_id == broadcaster_id
        ))
        if not interaction or not interaction.group:
            return None
        return {"type": "interaction.updated", "interaction": interaction_payload(interaction.group)}
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
        credential = await run_in_threadpool(db.get, TwitchCredential, broadcaster_id)
        if not credential or not credential.auth_session_hash:
            await run_in_threadpool(db.close)
            return
        try:
            token = await refresh_access_token_if_needed(credential, db)
        except HTTPException as exc:
            await run_in_threadpool(db.close)
            if exc.status_code == 401:
                await run_in_threadpool(_invalidate_broadcaster_session, broadcaster_id)
                logger.warning("Twitch EventSub authorization expired or was revoked")
                return
            logger.warning("Twitch EventSub token refresh failed (HTTP %d); retrying", exc.status_code)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
            continue
        await run_in_threadpool(db.close)
        try:
            async with connect(uri, open_timeout=15, ping_interval=30, ping_timeout=20, max_size=1_000_000) as socket:
                delay = 1
                logger.info("Twitch EventSub WebSocket connected")
                while True:
                    try:
                        raw = await asyncio.wait_for(socket.recv(), timeout=1)
                    except TimeoutError:
                        continue
                    try:
                        message = json.loads(raw)
                        if not isinstance(message, dict):
                            raise TypeError("EventSub frame must be a JSON object")
                        metadata = message.get("metadata")
                        payload = message.get("payload")
                        if not isinstance(metadata, dict) or not isinstance(payload, dict):
                            raise TypeError("EventSub metadata and payload must be objects")
                        message_type = metadata.get("message_type")
                    except (json.JSONDecodeError, TypeError, ValueError) as exc:
                        logger.warning("Malformed Twitch EventSub frame skipped (%s)", type(exc).__name__)
                        continue
                    if message_type == "session_welcome":
                        logger.info("Twitch EventSub session welcomed")
                        if should_subscribe:
                            await _subscribe_chat_events(
                                payload["session"]["id"], broadcaster_id, token
                            )
                        should_subscribe = True
                    elif message_type == "session_reconnect":
                        uri = payload["session"]["reconnect_url"]
                        should_subscribe = False
                        break
                    elif message_type == "notification" and metadata.get("subscription_type") in SUPPORTED_SUBSCRIPTION_TYPES:
                        try:
                            await _process_notification(broadcaster_id, metadata, payload)
                        except (AttributeError, TypeError, KeyError, ValueError) as exc:
                            logger.warning("Malformed Twitch EventSub notification skipped (%s)", type(exc).__name__)
                    elif message_type == "notification":
                        logger.warning("Unknown Twitch EventSub notification skipped (type=%s)",
                                       metadata.get("subscription_type"))
                    elif message_type == "revocation":
                        await run_in_threadpool(_invalidate_broadcaster_session, broadcaster_id)
                        return
                    else:
                        logger.warning("Unknown Twitch EventSub message skipped (type=%s)", message_type)
        except asyncio.CancelledError:
            raise
        except (OSError, TimeoutError, httpx.HTTPError, ConnectionClosed, SQLAlchemyError, ValueError, KeyError, RuntimeError) as exc:
            logger.warning("Twitch EventSub disconnected or failed (%s); retrying", type(exc).__name__)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
