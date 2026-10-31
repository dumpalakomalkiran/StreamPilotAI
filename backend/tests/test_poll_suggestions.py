from collections.abc import Generator
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app, require_streamer
from app.models import PollSuggestion, StreamSession


@pytest.fixture
def poll_client(monkeypatch) -> Generator[tuple[TestClient, sessionmaker], None, None]:
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
    with TestClient(app) as client:
        yield client, sessions
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def add_suggestion(sessions, broadcaster="broadcaster-1") -> int:
    with sessions() as db:
        stream = db.scalar(select(StreamSession).where(StreamSession.broadcaster_id == broadcaster))
        if stream is None:
            stream = StreamSession(broadcaster_id=broadcaster, mode="live")
            db.add(stream)
            db.flush()
        suggestion = PollSuggestion(
            broadcaster_id=broadcaster, session_id=stream.id, title="Ranked or casual?",
            options=["Ranked", "Casual"], duration_seconds=30, source_group_ids=[],
            supporting_message_ids=[], status="suggested",
        )
        db.add(suggestion)
        db.commit()
        return suggestion.id


def test_poll_list_dismiss_and_tenant_scope(poll_client):
    client, sessions = poll_client
    suggestion_id = add_suggestion(sessions)
    foreign_id = add_suggestion(sessions, "broadcaster-2")
    listed = client.get("/api/polls/suggestions")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [suggestion_id]
    assert client.post(f"/api/polls/suggestions/{foreign_id}/dismiss").status_code == 404
    dismissed = client.post(f"/api/polls/suggestions/{suggestion_id}/dismiss")
    assert dismissed.status_code == 200
    assert dismissed.json()["status"] == "dismissed"
    assert client.get("/api/polls/suggestions").json() == []


def test_edited_poll_uses_existing_approval_launch_and_five_option_vote_flow(poll_client):
    client, sessions = poll_client
    suggestion_id = add_suggestion(sessions)
    edited = {
        "title": "What should we queue?",
        "options": ["Ranked", "Casual", "Arcade", "Co-op", "Practice"],
        "duration_seconds": 45,
    }
    converted = client.post(f"/api/polls/suggestions/{suggestion_id}/approve", json=edited)
    assert converted.status_code == 200
    draft = converted.json()
    assert draft["status"] == "draft"
    assert draft["approved"] is False
    assert [option["label"] for option in draft["options"]] == edited["options"]

    challenge_id = draft["id"]
    assert client.post(f"/api/challenges/{challenge_id}/approve").json()["approved"] is True
    launched = client.post(f"/api/challenges/{challenge_id}/launch")
    assert launched.status_code == 200
    assert launched.json()["status"] == "active"
    vote = client.post(f"/api/challenges/{challenge_id}/votes", json={
        "twitch_user_id": "viewer-5", "option_number": 5, "event_id": "poll-vote-5",
    })
    assert vote.status_code == 200
    assert vote.json()["options"][4]["votes"] == 1
    with sessions() as db:
        suggestion = db.get(PollSuggestion, suggestion_id)
        assert suggestion.status == "approved"
        assert suggestion.title == edited["title"]


def test_poll_request_bounds_options_to_five_short_unique_labels(poll_client):
    client, sessions = poll_client
    suggestion_id = add_suggestion(sessions)
    base = {"title": "Poll", "duration_seconds": 30}
    too_many = client.post(f"/api/polls/suggestions/{suggestion_id}/approve", json={
        **base, "options": ["A", "B", "C", "D", "E", "F"],
    })
    too_long = client.post(f"/api/polls/suggestions/{suggestion_id}/approve", json={
        **base, "options": ["This option label is far too long", "B"],
    })
    assert too_many.status_code == 422
    assert too_long.status_code == 422
