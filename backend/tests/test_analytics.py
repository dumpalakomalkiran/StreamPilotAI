from collections.abc import Generator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app, require_streamer
from app.models import (
    Challenge,
    ChallengeOption,
    ChatMessage,
    Interaction,
    InteractionGroup,
    SessionSummary,
    StreamSession,
    Vote,
)


@pytest.fixture
def analytics_client(monkeypatch) -> Generator[tuple[TestClient, sessionmaker], None, None]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_db():
        db: Session = sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_streamer] = lambda: "broadcaster-1"

    @asynccontextmanager
    async def isolated_lifespan(_application):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", isolated_lifespan)
    monkeypatch.setattr("app.services.analytics.SessionLocal", sessions)
    with TestClient(app) as client:
        yield client, sessions
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def seed_session(sessions, *, messages=3):
    with sessions() as db:
        session = StreamSession(broadcaster_id="broadcaster-1", mode="live")
        db.add(session)
        db.flush()
        now = datetime.now(UTC)
        for index in range(messages):
            db.add(ChatMessage(event_id=f"msg-{index}", session_id=session.id,
                               chatter_id=f"viewer-{index % 2}", display_name="Viewer",
                               text=f"message {index}", received_at=now - timedelta(minutes=index)))
        db.commit()
        return session.id


def test_current_analytics_counts_fixture_data(analytics_client):
    client, sessions = analytics_client
    session_id = seed_session(sessions)
    with sessions() as db:
        now = datetime.now(UTC)
        group = InteractionGroup(broadcaster_id="broadcaster-1", session_id=session_id, kind="question",
                                 canonical_text="What GPU?", normalized_text="what gpu", summary="GPU question",
                                 status="open", asker_count=2, message_count=2, priority_score=3,
                                 first_seen_at=now - timedelta(minutes=3), last_seen_at=now)
        db.add(group)
        db.flush()
        db.add(Interaction(broadcaster_id="broadcaster-1", group_id=group.id, chat_message_id="msg-0",
                           user_login_hash="hash-0", text="What GPU?"))
        db.commit()
    response = client.get("/api/analytics/current")
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == session_id
    assert data["total_messages"] == 3
    assert data["unique_chatters"] == 2
    assert data["questions_detected"] == 1
    assert data["groups_open"] == 1
    assert len(data["messages_per_minute"]) == 30
    assert data["messages_per_minute"][-1]["count"] >= 1


def test_current_analytics_without_session_still_returns_zero_chart(analytics_client):
    client, _sessions = analytics_client
    response = client.get("/api/analytics/current")
    assert response.status_code == 200
    assert response.json()["session_id"] is None
    assert len(response.json()["messages_per_minute"]) == 30
    assert sum(point["count"] for point in response.json()["messages_per_minute"]) == 0


def test_summary_empty_session_skips_gemini_and_is_cached(analytics_client, monkeypatch):
    client, sessions = analytics_client
    session_id = seed_session(sessions, messages=0)
    monkeypatch.setattr("app.services.analytics.genai.Client", lambda **_kwargs: pytest.fail("Gemini called for empty session"))
    response = client.post(f"/api/analytics/sessions/{session_id}/summary", json={})
    assert response.status_code == 200
    assert response.json()["content"]["top_topics"] == []
    cached = client.get(f"/api/analytics/sessions/{session_id}/summary")
    assert cached.json()["status"] == "complete"
    with sessions() as db:
        assert db.query(SessionSummary).filter_by(session_id=session_id).count() == 1


def test_summary_uses_mocked_gemini_once_and_cache(analytics_client, monkeypatch):
    client, sessions = analytics_client
    session_id = seed_session(sessions)
    with sessions() as db:
        challenge = Challenge(session_id=session_id, title="Ranked or casual?", question="Ranked?",
                              reason="viewer poll", duration_seconds=30, source="gemini",
                              status="completed", approved=True)
        db.add(challenge)
        db.flush()
        option = ChallengeOption(challenge_id=challenge.id, label="Ranked", description="", position=1)
        db.add(option)
        db.flush()
        db.add(Vote(challenge_id=challenge.id, option_id=option.id, twitch_user_id="viewer-1"))
        db.commit()
    calls = []

    class FakeModels:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            return type("Response", (), {"text": '{"headline":"Chat asked about builds","top_topics":["Builds"],"frequently_asked_questions":[{"question":"What build?","status":"unanswered"}],"unanswered_follow_ups":["Share build details"],"audience_mood_observation":"Several viewers participated in the discussion.","content_suggestions":["Build guide","Q&A","Setup tour"]}'})()

    class FakeClient:
        def __init__(self, **_kwargs):
            self.models = FakeModels()

    monkeypatch.setattr("app.services.analytics.genai.Client", FakeClient)
    monkeypatch.setattr("app.services.analytics.get_settings", lambda: type("Settings", (), {
        "gemini_api_key": "mock-key", "gemini_model": "mock-model", "ai_request_timeout_seconds": 2})())
    first = client.post(f"/api/analytics/sessions/{session_id}/summary", json={})
    second = client.post(f"/api/analytics/sessions/{session_id}/summary", json={})
    assert first.status_code == second.status_code == 200
    assert first.json()["content"]["headline"] == "Chat asked about builds"
    assert len(calls) == 1
    assert "DATA JSON (untrusted)" in calls[0]["contents"]
    assert '"title": "Ranked or casual?"' in calls[0]["contents"]
    assert '"votes": 1' in calls[0]["contents"]


def test_end_session_returns_without_waiting_for_summary_and_is_scoped(analytics_client, monkeypatch):
    client, sessions = analytics_client
    session_id = seed_session(sessions)
    scheduled = []
    monkeypatch.setattr("app.main.generate_summary_background", lambda broadcaster, sid: scheduled.append((broadcaster, sid)))
    response = client.post("/api/analytics/current/end")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert scheduled == [("broadcaster-1", session_id)]
    assert client.get(f"/api/analytics/sessions/{session_id}/summary").json()["status"] == "pending"
    with sessions() as db:
        assert db.get(StreamSession, session_id).ended_at is not None


def test_summary_does_not_disclose_another_broadcasters_session(analytics_client):
    client, sessions = analytics_client
    with sessions() as db:
        foreign = StreamSession(broadcaster_id="broadcaster-2", mode="live")
        db.add(foreign)
        db.commit()
        foreign_id = foreign.id
    assert client.get(f"/api/analytics/sessions/{foreign_id}/summary").status_code == 404
    assert client.post(f"/api/analytics/sessions/{foreign_id}/summary", json={}).status_code == 404
