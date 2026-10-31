import asyncio
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.concurrency import run_in_threadpool

from app.database import SessionLocal
from app.models import Challenge, StreamSession, TwitchCredential
from app.services.overlay import broadcast
from app.services.twitch_chat import send_chat_message
from app.services.voting import challenge_payload, challenge_result_message, close_challenge


def _complete_expired_challenges() -> list[tuple[str | None, dict[str, object], str | None, str | None]]:
    db = SessionLocal()
    completed = []
    try:
        active_challenges = db.scalars(
            select(Challenge)
            .options(selectinload(Challenge.options), selectinload(Challenge.votes))
            .where(Challenge.status == "active")
        ).all()
        for challenge in active_challenges:
            if not challenge.ends_at:
                continue
            ends_at = challenge.ends_at
            if ends_at.tzinfo is None:
                ends_at = ends_at.replace(tzinfo=UTC)
            if datetime.now(UTC) < ends_at or not close_challenge(db, challenge):
                continue
            db.refresh(challenge)
            session = db.get(StreamSession, challenge.session_id)
            completed.append((
                session.overlay_token_hash if session else None,
                challenge_payload(db, challenge),
                session.broadcaster_id if session and session.mode == "live" else None,
                challenge_result_message(challenge) if session and session.mode == "live" else None,
            ))
        return completed
    finally:
        db.close()


async def run_challenge_clock() -> None:
    while True:
        completed = await run_in_threadpool(_complete_expired_challenges)
        for overlay_hash, payload, broadcaster_id, announcement in completed:
            if overlay_hash:
                await broadcast(overlay_hash, {"type": "challenge.completed", "challenge": payload})
            if broadcaster_id and announcement:
                await send_chat_message(TwitchCredential(broadcaster_id=broadcaster_id), announcement)
        await asyncio.sleep(1)
