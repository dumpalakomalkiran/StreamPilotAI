from collections.abc import Generator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Challenge, ChatMessage, Interaction, InteractionGroup, StreamSession
from app.services.voting import challenge_start_message


@pytest.fixture
def client(monkeypatch) -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_db():
        db: Session = test_sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db

    @asynccontextmanager
    async def isolated_lifespan(_application):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", isolated_lifespan)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_approval_launch_vote_change_and_close(client: TestClient) -> None:
    proposal = {
        "title": "Route choice",
        "question": "Which way?",
        "reason": "Demo proposal",
        "duration_seconds": 60,
        "source": "demo",
        "options": [{"label": "Left"}, {"label": "Right"}],
    }
    created = client.post("/api/challenges", json=proposal)
    assert created.status_code == 201
    challenge_id = created.json()["id"]
    assert created.json()["status"] == "draft"
    assert client.post(f"/api/challenges/{challenge_id}/launch").status_code == 409

    approved = client.post(f"/api/challenges/{challenge_id}/approve")
    assert approved.status_code == 200
    launched = client.post(f"/api/challenges/{challenge_id}/launch")
    assert launched.status_code == 200
    assert launched.json()["status"] == "active"

    first = client.post(
        f"/api/challenges/{challenge_id}/votes",
        json={"twitch_user_id": "viewer-1", "option_number": 1, "event_id": "event-1"},
    )
    assert first.status_code == 200
    changed = client.post(
        f"/api/challenges/{challenge_id}/votes",
        json={"twitch_user_id": "viewer-1", "option_number": 2, "event_id": "event-2"},
    )
    assert changed.status_code == 200
    assert changed.json()["total_votes"] == 1
    assert [option["votes"] for option in changed.json()["options"]] == [0, 1]
    assert client.post(
        f"/api/challenges/{challenge_id}/votes",
        json={"twitch_user_id": "viewer-1", "option_number": 1, "event_id": "event-2"},
    ).status_code == 409

    ended = client.post(f"/api/challenges/{challenge_id}/end")
    assert ended.status_code == 200
    assert ended.json()["status"] == "completed"
    assert ended.json()["winning_option_id"] == ended.json()["options"][1]["id"]
    assert client.post(
        f"/api/challenges/{challenge_id}/votes",
        json={"twitch_user_id": "viewer-2", "option_number": 1},
    ).status_code == 409
    history = client.get("/api/challenges")
    assert history.status_code == 200
    assert history.json()[0]["id"] == challenge_id


def test_challenge_start_message_contains_vote_instructions(client: TestClient) -> None:
    proposal = {
        "title": "Route choice",
        "question": "Which way should we go?",
        "reason": "Demo proposal",
        "duration_seconds": 60,
        "source": "demo",
        "options": [{"label": "Left"}, {"label": "Right"}],
    }
    created = client.post("/api/challenges", json=proposal)
    assert created.status_code == 201
    db_generator = app.dependency_overrides[get_db]()
    db = next(db_generator)
    challenge = db.get(Challenge, created.json()["id"])
    assert challenge is not None
    message = challenge_start_message(challenge)
    assert "Which way should we go?" in message
    assert "!vote N" in message
    assert "1:Left" in message and "2:Right" in message
    assert len(message) <= 500
    for _ in db_generator:
        pass


def test_interaction_api_lifecycle_and_broadcaster_scoping(client: TestClient) -> None:
    db_generator = app.dependency_overrides[get_db]()
    db = next(db_generator)
    demo_session = StreamSession(broadcaster_id="demo", mode="demo")
    other_session = StreamSession(broadcaster_id="other-broadcaster", mode="live")
    db.add_all([demo_session, other_session])
    db.flush()
    rows = []
    for stream, text in (
        (demo_session, "How do I find the key?"),
        (demo_session, "Can you show the map?"),
        (demo_session, "Where is the exit?"),
        (other_session, "How do I find the key?"),
    ):
        group = InteractionGroup(
            broadcaster_id=stream.broadcaster_id, session_id=stream.id,
            kind="question", canonical_text=text, normalized_text=text.casefold(),
            summary=text, status="open", asker_count=1, message_count=1,
            priority_score=2.0,
        )
        db.add(group)
        db.flush()
        message = ChatMessage(
            event_id=f"interaction-{len(rows)}", session_id=stream.id,
            chatter_id=f"viewer-{len(rows)}", display_name=f"Viewer {len(rows)}", text=text,
        )
        db.add(message)
        db.flush()
        db.add(Interaction(
            broadcaster_id=stream.broadcaster_id, group_id=group.id,
            chat_message_id=message.event_id, user_login_hash=f"hash-{len(rows)}", text=text,
        ))
        rows.append(group)
    db.commit()
    for _ in db_generator:
        pass

    listed = client.get("/api/interactions").json()
    assert len(listed) == 3
    assert all(item["id"] != rows[3].id for item in listed)
    assert client.get(f"/api/interactions/{rows[3].id}").status_code == 404

    detail = client.get(f"/api/interactions/{rows[0].id}")
    assert detail.status_code == 200
    assert detail.json()["messages"][0]["text"] == "How do I find the key?"
    assert client.post(f"/api/interactions/{rows[0].id}/answer").json()["status"] == "answered"
    assert client.post(f"/api/interactions/{rows[1].id}/dismiss").json()["status"] == "dismissed"
    snoozed = client.post(f"/api/interactions/{rows[2].id}/snooze", json={"minutes": 15})
    assert snoozed.status_code == 200
    assert snoozed.json()["status"] == "snoozed"
    assert snoozed.json()["snoozed_until"] is not None
    assert client.post(f"/api/interactions/{rows[2].id}/snooze", json={"minutes": 0}).status_code == 422


def test_interaction_list_excludes_completed_sessions(client: TestClient) -> None:
    db_generator = app.dependency_overrides[get_db]()
    db = next(db_generator)
    active = StreamSession(broadcaster_id="demo", mode="live")
    completed = StreamSession(broadcaster_id="demo", mode="live")
    db.add_all([active, completed])
    db.flush()
    completed.ended_at = datetime.now(UTC)
    for session, text in ((active, "How do I change settings?"), (completed, "What was the old question?") ):
        group = InteractionGroup(
            broadcaster_id="demo", session_id=session.id, kind="question",
            canonical_text=text, normalized_text=text.casefold(), summary=text,
            status="open", asker_count=1, message_count=1, priority_score=1.0,
        )
        db.add(group)
    db.commit()
    for _ in db_generator:
        pass

    response = client.get("/api/interactions")
    assert response.status_code == 200
    assert [item["canonical_text"] for item in response.json()] == ["How do I change settings?"]


def test_interaction_list_repairs_stale_group_counts(client: TestClient) -> None:
    db_generator = app.dependency_overrides[get_db]()
    db = next(db_generator)
    session = StreamSession(broadcaster_id="demo", mode="live")
    db.add(session)
    db.flush()
    group = InteractionGroup(
        broadcaster_id="demo", session_id=session.id, kind="question",
        canonical_text="Can my GPU run this?", normalized_text="can my gpu run this",
        summary="Can my GPU run this?", status="open", asker_count=0,
        message_count=0, priority_score=0,
    )
    db.add(group)
    db.flush()
    db.add(ChatMessage(
        event_id="stale-count-message", session_id=session.id, chatter_id="viewer-1",
        display_name="Viewer", text="Can my GPU run this?",
    ))
    db.flush()
    db.add(Interaction(
        broadcaster_id="demo", group_id=group.id, chat_message_id="stale-count-message",
        user_login_hash="viewer-hash", text="Can my GPU run this?",
    ))
    db.commit()
    for _ in db_generator:
        pass

    response = client.get("/api/interactions")
    assert response.status_code == 200
    assert response.json()[0]["asker_count"] == 1
    assert response.json()[0]["message_count"] == 1
