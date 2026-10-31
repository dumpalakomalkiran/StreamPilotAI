from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    Challenge,
    ChallengeOption,
    ChatMessage,
    DeletedChatMessage,
    StreamSession,
    Vote,
)
from app.services import eventsub


@pytest.mark.asyncio
async def test_eventsub_subscribes_to_chat_and_moderation_events(monkeypatch) -> None:
    subscribed = []

    async def record_subscription(session_id, broadcaster_id, token, subscription_type):
        subscribed.append((session_id, broadcaster_id, token, subscription_type))

    monkeypatch.setattr(eventsub, "_subscribe", record_subscription)
    await eventsub._subscribe_chat_events("session-1", "broadcaster-1", "token")
    assert subscribed == [
        ("session-1", "broadcaster-1", "token", "channel.chat.message"),
        ("session-1", "broadcaster-1", "token", "channel.chat.message_delete"),
        ("session-1", "broadcaster-1", "token", "channel.chat.clear_user_messages"),
        ("session-1", "broadcaster-1", "token", "channel.chat.clear"),
        ("session-1", "broadcaster-1", "token", "stream.online"),
        ("session-1", "broadcaster-1", "token", "stream.offline"),
    ]


@pytest.mark.asyncio
async def test_stream_subscription_uses_broadcaster_only_condition(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, **kwargs):
            captured.update(kwargs)
            return FakeResponse()

    monkeypatch.setattr(eventsub.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(eventsub, "get_settings", lambda: type("Settings", (), {"twitch_client_id": "client"})())
    await eventsub._subscribe("session-1", "broadcaster-1", "token", "stream.offline")

    assert captured["json"]["condition"] == {"broadcaster_user_id": "broadcaster-1"}


@pytest.mark.asyncio
async def test_eventsub_persists_recent_chat_and_processes_vote_once(monkeypatch) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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
    await eventsub._process_notification(
        "broadcaster-1", {"subscription_type": "channel.chat.message", "message_id": "event-1"},
        notification,
    )
    duplicate_notification = {"event": {**event, "message": {"text": "!vote 1"}}}
    await eventsub._process_notification(
        "broadcaster-1", {"subscription_type": "channel.chat.message", "message_id": "event-1"},
        duplicate_notification,
    )
    broadcaster_notification = {"event": {
        **event,
        "chatter_user_id": "broadcaster-1",
        "chatter_user_name": "Streamer",
        "message": {"text": "Testing chat feed"},
    }}
    await eventsub._process_notification(
        "broadcaster-1", {"subscription_type": "channel.chat.message", "message_id": "event-2"},
        broadcaster_notification,
    )

    with test_sessions() as db:
        messages = db.scalars(select(ChatMessage)).all()
        votes = db.scalars(select(Vote)).all()
        assert len(messages) == 2
        viewer_message = next(message for message in messages if message.display_name == "Viewer One")
        streamer_message = next(message for message in messages if message.display_name == "Streamer")
        assert viewer_message.text == "!vote 2"
        assert streamer_message.text == "Testing chat feed"
        assert len(votes) == 1
        assert votes[0].option_id == db.scalar(
            select(ChallengeOption.id).where(ChallengeOption.position == 2)
        )

    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.mark.asyncio
async def test_stream_offline_ends_session_and_next_online_starts_another(monkeypatch) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with test_sessions() as db:
        db.add(StreamSession(broadcaster_id="broadcaster-1", mode="live"))
        db.commit()
    monkeypatch.setattr(eventsub, "SessionLocal", test_sessions)

    await eventsub._process_notification(
        "broadcaster-1", {"subscription_type": "stream.offline", "message_id": "offline-1"},
        {"event": {"broadcaster_user_id": "broadcaster-1"}},
    )
    with test_sessions() as db:
        first_session = db.scalar(select(StreamSession))
        assert first_session.ended_at is not None

    await eventsub._process_notification(
        "broadcaster-1", {"subscription_type": "stream.online", "message_id": "online-2"},
        {"event": {"broadcaster_user_id": "broadcaster-1"}},
    )
    await eventsub._process_notification(
        "broadcaster-1", {"subscription_type": "stream.online", "message_id": "online-2"},
        {"event": {"broadcaster_user_id": "broadcaster-1"}},
    )
    with test_sessions() as db:
        sessions = db.scalars(select(StreamSession).order_by(StreamSession.id)).all()
        assert len(sessions) == 2
        assert sessions[1].ended_at is None

    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.mark.asyncio
async def test_deleted_chat_messages_are_removed_and_not_restored_by_replay(monkeypatch) -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with test_sessions() as db:
        stream = StreamSession(broadcaster_id="broadcaster-1", mode="live")
        db.add(stream)
        db.flush()
        db.add_all([
            ChatMessage(event_id="deleted-1", session_id=stream.id, chatter_id="viewer-1",
                        display_name="Viewer One", text="please remove this"),
            ChatMessage(event_id="user-clear-1", session_id=stream.id, chatter_id="viewer-1",
                        display_name="Viewer One", text="second viewer message"),
            ChatMessage(event_id="kept-1", session_id=stream.id, chatter_id="viewer-2",
                        display_name="Viewer Two", text="keep this message"),
        ])
        db.commit()
    monkeypatch.setattr(eventsub, "SessionLocal", test_sessions)

    await eventsub._process_notification(
        "broadcaster-1", {"subscription_type": "channel.chat.message_delete"},
        {"event": {"broadcaster_user_id": "broadcaster-1", "message_id": "deleted-1"}},
    )
    await eventsub._process_notification(
        "broadcaster-1", {"subscription_type": "channel.chat.clear_user_messages"},
        {"event": {"broadcaster_user_id": "broadcaster-1", "target_user_id": "viewer-1"}},
    )

    # A duplicate/replayed message notification must not reintroduce deleted text.
    await eventsub._process_notification(
        "broadcaster-1", {"subscription_type": "channel.chat.message", "message_id": "deleted-1"},
        {"event": {
            "broadcaster_user_id": "broadcaster-1", "chatter_user_id": "viewer-1",
            "chatter_user_name": "Viewer One", "message": {"text": "please remove this"},
        }},
    )
    with test_sessions() as db:
        messages = db.scalars(select(ChatMessage)).all()
        assert [(message.event_id, message.text) for message in messages] == [
            ("kept-1", "keep this message")
        ]
        assert db.get(DeletedChatMessage, "deleted-1") is not None

    await eventsub._process_notification(
        "broadcaster-1", {"subscription_type": "channel.chat.clear"},
        {"event": {"broadcaster_user_id": "broadcaster-1"}},
    )
    with test_sessions() as db:
        assert db.scalars(select(ChatMessage)).all() == []
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.mark.asyncio
async def test_malformed_and_unknown_eventsub_notifications_are_logged_and_skipped(caplog) -> None:
    await eventsub._process_notification(
        "broadcaster-1", {"subscription_type": "channel.chat.message", "message_id": "bad-1"},
        {"event": ["not", "an", "object"]},
    )
    await eventsub._process_notification(
        "broadcaster-1", {"subscription_type": "channel.chat.future_event", "message_id": "future-1"},
        {"event": {}},
    )

    assert "Malformed Twitch chat message event skipped" in caplog.text
    assert "Unknown Twitch EventSub notification skipped" in caplog.text
