from collections.abc import Generator
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


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
