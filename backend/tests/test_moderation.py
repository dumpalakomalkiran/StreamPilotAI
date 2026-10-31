import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database import Base
from app.models import AuditLog, ChatMessage, ModerationIncident, StreamSession
from app.services import interactions
from app.services.moderation import detect_deterministic_incident


@pytest.fixture
def moderation_db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(interactions, "get_settings", lambda: Settings(_env_file=None, gemini_api_key=""))
    with sessions() as db:
        stream = StreamSession(broadcaster_id="mod-channel", mode="live")
        db.add(stream)
        db.flush()
        yield db, stream
    Base.metadata.drop_all(engine)
    engine.dispose()


def add_message(db, stream, event_id, user, text, at=None):
    message = ChatMessage(event_id=event_id, session_id=stream.id, chatter_id=user,
                          display_name=f"{user} viewer", text=text, received_at=at or datetime.now(UTC))
    db.add(message)
    db.flush()
    return message


def test_repeated_message_rule_flags_fourth_identical_post(moderation_db):
    db, stream = moderation_db
    now = datetime.now(UTC)
    for index in range(3):
        add_message(db, stream, f"repeat-{index}", "viewer-1", "hello chat", now - timedelta(seconds=3-index))
    current = add_message(db, stream, "repeat-3", "viewer-1", "hello chat", now)
    incident = detect_deterministic_incident(db, broadcaster_id=stream.broadcaster_id,
                                              session=stream, message=current)
    assert incident and incident.category == "repeated"
    assert incident.severity == "medium"


def test_caps_and_emote_spam_rules(moderation_db):
    db, stream = moderation_db
    caps = add_message(db, stream, "caps", "viewer-caps", "EVERYONE LOOK AT THIS RIGHT NOW")
    emotes = add_message(db, stream, "emotes", "viewer-emotes", "LUL LUL LUL LUL LUL LUL")
    assert detect_deterministic_incident(db, broadcaster_id=stream.broadcaster_id,
                                         session=stream, message=caps).category == "spam"
    flag = detect_deterministic_incident(db, broadcaster_id=stream.broadcaster_id,
                                         session=stream, message=emotes)
    assert flag and flag.category == "spam" and "emote" in flag.reason


def test_new_account_link_flags_but_returning_user_link_does_not(moderation_db):
    db, stream = moderation_db
    first_link = add_message(db, stream, "link-first", "new-user", "visit https://example.com")
    incident = detect_deterministic_incident(db, broadcaster_id=stream.broadcaster_id,
                                              session=stream, message=first_link)
    assert incident and incident.category == "link"

    add_message(db, stream, "old-chat", "returning", "hello there")
    link = add_message(db, stream, "link-return", "returning", "here https://example.com")
    assert detect_deterministic_incident(db, broadcaster_id=stream.broadcaster_id,
                                         session=stream, message=link) is None


@pytest.mark.asyncio
async def test_batched_gemini_adds_only_valid_flag_for_borderline_message(moderation_db, monkeypatch):
    db, stream = moderation_db
    message = add_message(db, stream, "borderline-1", "viewer-ai", "THIS IS BIG Really")
    second_message = add_message(db, stream, "borderline-2", "viewer-ai-2", "THIS IS quite LOUD")
    db.commit()
    monkeypatch.setattr(interactions, "get_settings", lambda: Settings(
        _env_file=None, gemini_api_key="mock", gemini_model="test-model", ai_request_timeout_seconds=1,
    ))

    class Models:
        calls = 0

        def generate_content(self, **kwargs):
            self.calls += 1
            assert "moderation_messages" in kwargs["contents"]
            return SimpleNamespace(text=json.dumps({"decisions": [], "moderation_classifications": [
                {"message_id": message.event_id, "flag": True, "category": "other",
                 "severity": "low", "confidence": 0.78, "reason": "Borderline shouting may merit a review."},
                {"message_id": second_message.event_id, "flag": False, "category": "other",
                 "severity": "low", "confidence": 0.2, "reason": "No clear signal."},
            ]}))

    models = Models()
    monkeypatch.setattr(interactions.genai, "Client", lambda **_kwargs: SimpleNamespace(models=models))
    assert await interactions.resolve_pending(db, broadcaster_id=stream.broadcaster_id,
                                              session_id=stream.id) == 0
    incident = db.scalar(select(ModerationIncident))
    assert incident and incident.chat_message_id == message.event_id
    assert db.get(ChatMessage, second_message.event_id).moderation_ai_checked is True
    assert models.calls == 1


@pytest.mark.asyncio
async def test_invalid_gemini_moderation_classification_is_ignored(moderation_db, monkeypatch):
    db, stream = moderation_db
    add_message(db, stream, "borderline-invalid", "viewer-ai", "THIS IS BIG Really")
    db.commit()
    monkeypatch.setattr(interactions, "get_settings", lambda: Settings(
        _env_file=None, gemini_api_key="mock", gemini_model="test-model", ai_request_timeout_seconds=1,
    ))

    class Models:
        def generate_content(self, **_kwargs):
            return SimpleNamespace(text='{"decisions":[],"moderation_classifications":[{"flag":true,"category":"ban","severity":"extreme"}]}')

    monkeypatch.setattr(interactions.genai, "Client", lambda **_kwargs: SimpleNamespace(models=Models()))
    assert await interactions.resolve_pending(db, broadcaster_id=stream.broadcaster_id,
                                              session_id=stream.id) == 0
    assert db.scalars(select(ModerationIncident)).all() == []


def test_moderation_payloads_and_module_have_no_twitch_action_dependency():
    import inspect

    from app.services import moderation

    source = inspect.getsource(moderation)
    assert "send_chat_message" not in source
    assert "ban_user" not in source
    assert "timeout_user" not in source
    assert "delete_chat_message" not in source


@pytest.fixture
def api_client(monkeypatch):
    from contextlib import asynccontextmanager

    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db

    @asynccontextmanager
    async def lifespan(_app):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", lifespan)
    with sessions() as db:
        stream = StreamSession(broadcaster_id="demo", mode="demo")
        other = StreamSession(broadcaster_id="another-channel", mode="live")
        db.add_all([stream, other])
        db.flush()
        own_message = add_message(db, stream, "own-message", "own-viewer", "LOUD MESSAGE")
        reviewed_message = add_message(db, stream, "reviewed-message", "another-own-viewer", "REPEATED MESSAGE")
        other_message = add_message(db, other, "other-message", "other-viewer", "link https://example.org")
        own = ModerationIncident(broadcaster_id="demo", session_id=stream.id,
                                 chat_message_id=own_message.event_id, user_display_name="Own",
                                 category="spam", severity="medium", confidence=0.8,
                                 reason="High caps", status="open")
        to_review = ModerationIncident(broadcaster_id="demo", session_id=stream.id,
                                       chat_message_id=reviewed_message.event_id, user_display_name="Another",
                                       category="spam", severity="low", confidence=0.7,
                                       reason="Repeated phrase", status="open")
        foreign = ModerationIncident(broadcaster_id="another-channel", session_id=other.id,
                                     chat_message_id=other_message.event_id, user_display_name="Other",
                                     category="link", severity="medium", confidence=0.9,
                                     reason="New chatter link", status="open")
        db.add_all([own, to_review, foreign])
        db.commit()
        own_id = own.id
        reviewed_id = to_review.id
        foreign_id = foreign.id
    with TestClient(app) as client:
        yield client, sessions, own_id, foreign_id, reviewed_id
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_api_tenant_scoping_status_audit_and_no_twitch_send(api_client, monkeypatch):
    from app import main

    client, sessions, own_id, foreign_id, reviewed_id = api_client
    spy = Mock()
    monkeypatch.setattr(main, "send_chat_message_checked", spy)
    listed = client.get("/api/moderation/incidents")
    assert listed.status_code == 200
    assert {row["id"] for row in listed.json()} == {own_id, reviewed_id}
    assert client.post(f"/api/moderation/incidents/{foreign_id}/dismiss").status_code == 404
    updated = client.post(f"/api/moderation/incidents/{own_id}/dismiss")
    assert updated.status_code == 200 and updated.json()["status"] == "dismissed"
    reviewed = client.post(f"/api/moderation/incidents/{reviewed_id}/reviewed")
    assert reviewed.status_code == 200 and reviewed.json()["status"] == "reviewed"
    with sessions() as db:
        audits = db.scalars(select(AuditLog)).all()
        assert len(audits) == 2
        assert {(item.from_status, item.to_status) for item in audits} == {
            ("open", "dismissed"), ("open", "reviewed"),
        }
    spy.assert_not_called()


@pytest.mark.asyncio
async def test_eventsub_moderation_incident_is_dashboard_only(api_client, monkeypatch):
    from app.services import eventsub

    client, sessions, _own_id, _foreign_id, _reviewed_id = api_client
    del client
    dashboard_events = []
    overlay_events = []

    async def dashboard(_broadcaster_id, event):
        dashboard_events.append(event)

    async def overlay(*args):
        overlay_events.append(args)

    monkeypatch.setattr(eventsub, "SessionLocal", sessions)
    monkeypatch.setattr(eventsub, "broadcast_dashboard", dashboard)
    monkeypatch.setattr(eventsub, "broadcast", overlay)
    monkeypatch.setattr(eventsub, "broadcast_question_overlay", overlay)
    metadata = {"subscription_type": "channel.chat.message", "message_id": "live-link-flag"}
    payload = {"event": {"broadcaster_user_id": "demo", "chatter_user_id": "brand-new",
                          "chatter_user_name": "New Viewer",
                          "message": {"text": "visit https://example.net"}}}
    await eventsub._process_notification("demo", metadata, payload)
    assert any(event["type"] == "moderation.incident.created" for event in dashboard_events)
    assert overlay_events == []
    with sessions() as db:
        assert db.scalar(select(ModerationIncident).where(
            ModerationIncident.chat_message_id == "live-link-flag"
        )) is not None
