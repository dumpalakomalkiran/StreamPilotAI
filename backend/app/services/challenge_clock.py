import asyncio
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import SessionLocal
from app.models import Challenge, StreamSession, TwitchCredential
from app.services.overlay import broadcast
from app.services.twitch_chat import send_chat_message
from app.services.voting import challenge_payload, challenge_result_message, close_challenge


async def run_challenge_clock() -> None:
    while True:
        db = SessionLocal()
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
                payload = challenge_payload(db, challenge)
                if session and session.overlay_token_hash:
                    await broadcast(session.overlay_token_hash, {"type": "challenge.completed", "challenge": payload})
                if session and session.mode == "live":
                    credential = db.get(TwitchCredential, session.broadcaster_id)
                    if credential:
                        await send_chat_message(credential, challenge_result_message(challenge), db=db)
        finally:
            db.close()
        await asyncio.sleep(1)
