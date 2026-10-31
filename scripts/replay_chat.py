"""Replay synthetic chat through the production EventSub notification handler (development only)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
os.chdir(BACKEND)  # Match the backend's .env and relative SQLite URL resolution.
sys.path.insert(0, str(BACKEND))

from sqlalchemy import func, select

from app.config import get_settings
from app.database import Base, SessionLocal, engine, upgrade_schema
from app.models import Interaction, StreamSession
from app.services.eventsub import _process_notification
from app.services.interactions import resolve_pending

SYNTHETIC_BROADCASTER_ID = "synthetic-replay"


def load_messages(path: Path) -> list[dict[str, str]]:
    messages = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at line {line_number}") from exc
        if not isinstance(item, dict) or not all(
            isinstance(item.get(key), str) and item[key].strip()
            for key in ("user_id", "display_name", "text")
        ):
            raise ValueError(f"Invalid replay record at line {line_number}")
        messages.append({key: item[key][:500] for key in ("user_id", "display_name", "text")})
    return messages


def create_replay_session() -> int:
    with SessionLocal() as db:
        prior = db.scalars(select(StreamSession).where(
            StreamSession.broadcaster_id == SYNTHETIC_BROADCASTER_ID,
            StreamSession.ended_at.is_(None),
        )).all()
        for session in prior:
            session.ended_at = datetime.now(UTC)
        current = StreamSession(broadcaster_id=SYNTHETIC_BROADCASTER_ID, mode="replay")
        db.add(current)
        db.commit()
        return current.id


async def replay(messages: list[dict[str, str]], speed: float, session_id: int) -> None:
    batch_size = max(1, get_settings().interaction_llm_batch_size)
    for index, item in enumerate(messages, 1):
        event_id = f"synthetic-replay-{session_id}-{index}"
        metadata = {"message_type": "notification", "message_id": event_id,
                    "subscription_type": "channel.chat.message"}
        payload = {"event": {
            "broadcaster_user_id": SYNTHETIC_BROADCASTER_ID,
            "chatter_user_id": item["user_id"],
            "chatter_user_name": item["display_name"],
            "chatter_user_login": item["display_name"].casefold().replace(" ", "_"),
            "message": {"text": item["text"], "fragments": [{"type": "text", "text": item["text"]}]},
        }}
        await _process_notification(SYNTHETIC_BROADCASTER_ID, metadata, payload, is_synthetic=True)
        with SessionLocal() as db:
            pending_count = db.scalar(select(func.count()).select_from(Interaction).where(
                Interaction.broadcaster_id == SYNTHETIC_BROADCASTER_ID,
                Interaction.group_id.is_(None),
            ))
        if pending_count >= batch_size:
            await resolve_pending(broadcaster_id=SYNTHETIC_BROADCASTER_ID, session_id=session_id)
        if index < len(messages):
            await asyncio.sleep(1 / speed)
    await resolve_pending(broadcaster_id=SYNTHETIC_BROADCASTER_ID, session_id=session_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=ROOT / "data" / "demo_chat.jsonl")
    parser.add_argument("--speed", type=float, default=10.0, metavar="MESSAGES_PER_SECOND")
    args = parser.parse_args()
    if get_settings().app_env.casefold() == "production":
        parser.error("chat replay is disabled when APP_ENV=production")
    if args.speed <= 0 or args.speed > 100:
        parser.error("--speed must be greater than 0 and at most 100 messages per second")
    try:
        replay_path = args.file if args.file.is_absolute() else ROOT / args.file
        messages = load_messages(replay_path.resolve())
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if not messages:
        parser.error("replay file contains no messages")
    Base.metadata.create_all(bind=engine)
    upgrade_schema()
    session_id = create_replay_session()
    asyncio.run(replay(messages, args.speed, session_id))
    print(f"Replayed {len(messages)} synthetic messages into isolated session {session_id}.")
    print("Every row is marked is_synthetic=true; broadcaster scope: synthetic-replay.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
