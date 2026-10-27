from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Challenge, ChallengeOption, ChatMessage, StreamSession, Vote
from app.services import eventsub


@pytest.mark.asyncio
async def test_eventsub_persists_recent_chat_and_processes_vote_once(monkeypatch) -> None:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with test_sessions() as db:
        stream = StreamSession(broadcaster_id="broadcaster-1", mode="live")
        challenge = Challenge(
            session=stream,
            title="Route choice",
            question="Which route?",
            reason="Test proposal",
            duration_seconds=60,
            status="active",
            approved=True,
            started_at=datetime.now(UTC),
            ends_at=datetime.now(UTC) + timedelta(seconds=60),
            options=[
                ChallengeOption(label="Left", position=1),
                ChallengeOption(label="Right", position=2),
            ],
        )
        db.add(challenge)
        db.commit()

    monkeypatch.setattr(eventsub, "SessionLocal", test_sessions)
    event = {
        "broadcaster_user_id": "broadcaster-1",
        "chatter_user_id": "viewer-1",
        "chatter_user_name": "Viewer One",
        "message": {"text": "!vote 2"},
    }
    notification = {"event": event}
    await eventsub._process_notification("broadcaster-1", {"message_id": "event-1"}, notification)
    await eventsub._process_notification("broadcaster-1", {"message_id": "event-1"}, notification)

    with test_sessions() as db:
        messages = db.scalars(select(ChatMessage)).all()
        votes = db.scalars(select(Vote)).all()
        assert len(messages) == 1
        assert messages[0].text == "!vote 2"
        assert messages[0].display_name == "Viewer One"
        assert len(votes) == 1
        assert votes[0].option_id == db.scalar(
            select(ChallengeOption.id).where(ChallengeOption.position == 2)
        )

    Base.metadata.drop_all(engine)
    engine.dispose()
