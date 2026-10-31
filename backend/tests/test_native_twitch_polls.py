from collections.abc import Generator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.config import Settings
from app.database import Base, get_db
from app.models import (
    BroadcasterSettings,
    Challenge,
    ChallengeOption,
    NativeTwitchPoll,
    PollSuggestion,
    StreamSession,
    TwitchCredential,
)
from app.services import twitch_auth, twitch_polls


@pytest.fixture
def native_client(monkeypatch) -> Generator[tuple[TestClient, sessionmaker], None, None]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_db():
        db: Session = sessions()
        try:
            yield db
        finally:
            db.close()

    app = main.app
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[main.require_streamer] = lambda: "broadcaster-1"

    @asynccontextmanager
    async def isolated_lifespan(_application):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", isolated_lifespan)
    monkeypatch.setattr(main, "settings", Settings(_env_file=None, demo_mode=False))
    with TestClient(app) as client:
        yield client, sessions
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def approved_suggestion(sessions, *, scope=True, enabled=True, active_challenge=False):
    with sessions() as db:
        stream = StreamSession(broadcaster_id="broadcaster-1", mode="live")
        db.add(stream)
        db.flush()
        challenge = Challenge(
            session_id=stream.id, title="Ranked or casual?", question="Ranked or casual?", reason="test",
            duration_seconds=30, source="gemini", status="active" if active_challenge else "draft",
            approved=active_challenge,
            options=[ChallengeOption(label="Ranked", position=1), ChallengeOption(label="Casual", position=2)],
        )
        db.add(challenge)
        db.flush()
        suggestion = PollSuggestion(
            broadcaster_id="broadcaster-1", session_id=stream.id, title=challenge.title,
            options=["Ranked", "Casual"], duration_seconds=30, source_group_ids=[], supporting_message_ids=[],
            status="approved", approved_challenge_id=challenge.id,
        )
        db.add(suggestion)
        db.add(BroadcasterSettings(broadcaster_id="broadcaster-1", native_twitch_polls_enabled=enabled))
        db.add(TwitchCredential(
            broadcaster_id="broadcaster-1", access_token="encrypted", refresh_token="encrypted",
            scopes=f"user:read:chat user:write:chat{' channel:manage:polls' if scope else ''}",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ))
        db.commit()
        return suggestion.id


def fake_helix(monkeypatch, *, remote_status=200, create_status=200):
    calls = []

    class Response:
        def __init__(self, status, data):
            self.status_code = status
            self.is_error = status >= 400
            self._data = data

        def json(self):
            return self._data

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **kwargs):
            calls.append(("GET", url, kwargs))
            body = [{"id": "existing", "status": "ACTIVE"}] if remote_status == 200 and create_status == -1 else []
            return Response(remote_status, {"data": body})

        async def post(self, url, **kwargs):
            calls.append(("POST", url, kwargs))
            return Response(create_status, {"data": [{"id": "twitch-poll-1", "status": "ACTIVE"}]})

        async def patch(self, url, **kwargs):
            calls.append(("PATCH", url, kwargs))
            return Response(200, {"data": [{"id": "twitch-poll-1", "status": "TERMINATED"}]})

    monkeypatch.setattr(twitch_polls.httpx, "AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr(twitch_polls, "get_settings", lambda: Settings(_env_file=None, twitch_client_id="mock-client"))
    async def token(_credential, _db):
        return "mock-access-token"
    monkeypatch.setattr(twitch_polls, "refresh_access_token_if_needed", token)
    return calls


def test_native_poll_success_creates_and_tracks_helix_poll(native_client, monkeypatch):
    client, sessions = native_client
    suggestion_id = approved_suggestion(sessions)
    calls = fake_helix(monkeypatch)
    response = client.post("/api/polls/twitch/create", json={"suggestion_id": suggestion_id})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["twitch_poll_id"] == "twitch-poll-1"
    assert body["status"] == "ACTIVE"
    assert [call[0] for call in calls] == ["GET", "POST"]
    assert calls[1][2]["json"] == {
        "broadcaster_id": "broadcaster-1", "title": "Ranked or casual?",
        "choices": [{"title": "Ranked"}, {"title": "Casual"}], "duration": 30,
    }
    assert [item["twitch_poll_id"] for item in client.get("/api/polls/twitch").json()] == ["twitch-poll-1"]
    ended = client.post(f"/api/polls/twitch/{body['id']}/end")
    assert ended.status_code == 200
    assert ended.json()["status"] == "TERMINATED"
    assert [call[0] for call in calls] == ["GET", "POST", "GET", "PATCH"]


@pytest.mark.parametrize(("status", "expected"), [(403, "Affiliate or Partner"), (429, "rate limiting")])
def test_native_poll_helix_errors_are_safe_and_keep_vote_fallback(native_client, monkeypatch, status, expected):
    client, sessions = native_client
    suggestion_id = approved_suggestion(sessions)
    fake_helix(monkeypatch, create_status=status)
    response = client.post("/api/polls/twitch/create", json={"suggestion_id": suggestion_id})
    assert response.status_code == status
    assert expected in response.json()["detail"]
    with sessions() as db:
        suggestion = db.get(PollSuggestion, suggestion_id)
        challenge = db.get(Challenge, suggestion.approved_challenge_id)
        assert suggestion.status == "approved"
        assert challenge.status == "draft" and not challenge.approved


def test_native_poll_active_twitch_poll_conflicts_before_create(native_client, monkeypatch):
    client, sessions = native_client
    suggestion_id = approved_suggestion(sessions)
    calls = fake_helix(monkeypatch, create_status=-1)
    response = client.post("/api/polls/twitch/create", json={"suggestion_id": suggestion_id})
    assert response.status_code == 409
    assert "Twitch poll is already active" in response.json()["detail"]
    assert [call[0] for call in calls] == ["GET"]


def test_native_poll_active_stream_pilot_challenge_conflicts(native_client, monkeypatch):
    client, sessions = native_client
    suggestion_id = approved_suggestion(sessions, active_challenge=True)
    calls = fake_helix(monkeypatch)
    response = client.post("/api/polls/twitch/create", json={"suggestion_id": suggestion_id})
    assert response.status_code == 409
    assert "StreamPilot" in response.json()["detail"]
    assert calls == []


def test_native_poll_scope_missing_requires_reauthorization(native_client, monkeypatch):
    client, sessions = native_client
    suggestion_id = approved_suggestion(sessions, scope=False)
    calls = fake_helix(monkeypatch)
    response = client.post("/api/polls/twitch/create", json={"suggestion_id": suggestion_id})
    assert response.status_code == 409
    assert "channel:manage:polls" in response.json()["detail"]
    assert calls == []


def test_approved_suggestion_can_be_retried_after_native_failure(native_client):
    client, sessions = native_client
    suggestion_id = approved_suggestion(sessions)
    # Existing StreamPilot poll approval has already happened, but a Twitch 429/403 may require retry/fallback.
    response = client.post(f"/api/polls/suggestions/{suggestion_id}/approve", json={
        "title": "Ranked or casual?", "options": ["Ranked", "Casual"], "duration_seconds": 30,
    })
    assert response.status_code == 200
    assert response.json()["status"] == "draft"


def test_native_poll_end_and_list_are_broadcaster_scoped(native_client):
    client, sessions = native_client
    with sessions() as db:
        stream = StreamSession(broadcaster_id="broadcaster-2", mode="live")
        db.add(stream)
        db.flush()
        challenge = Challenge(session_id=stream.id, title="Title?", question="Title?", reason="test", duration_seconds=30)
        db.add(challenge)
        db.flush()
        suggestion = PollSuggestion(
            broadcaster_id="broadcaster-2", session_id=stream.id, title="Title?", options=["A", "B"],
            duration_seconds=30, source_group_ids=[], supporting_message_ids=[], status="approved",
            approved_challenge_id=challenge.id,
        )
        db.add(suggestion)
        db.flush()
        foreign_poll = NativeTwitchPoll(
            broadcaster_id="broadcaster-2", session_id=stream.id, suggestion_id=suggestion.id,
            challenge_id=challenge.id, twitch_poll_id="foreign-twitch-id", title="Title?", options=["A", "B"],
            duration_seconds=30, status="ACTIVE",
        )
        db.add(foreign_poll)
        db.commit()
        foreign_id = foreign_poll.id
    assert client.get("/api/polls/twitch").json() == []
    assert client.post(f"/api/polls/twitch/{foreign_id}/end").status_code == 404


def test_native_poll_setting_is_opt_in_and_returns_reauthorization_prompt(native_client):
    client, sessions = native_client
    with sessions() as db:
        db.add(TwitchCredential(
            broadcaster_id="broadcaster-1", access_token="x", refresh_token="y", scopes="user:read:chat",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ))
        db.commit()
    initial = client.get("/api/settings/native-polls").json()
    assert initial["enabled"] is False
    enabled = client.put("/api/settings/native-polls", json={"enabled": True}).json()
    assert enabled["enabled"] is True and enabled["reauthorization_required"] is True
    disabled = client.put("/api/settings/native-polls", json={"enabled": False}).json()
    assert disabled["enabled"] is False and disabled["reauthorization_required"] is False


def test_oauth_requests_poll_scope_only_after_opt_in(monkeypatch):
    base = Settings(_env_file=None, twitch_client_id="client", twitch_client_secret="secret",
                    twitch_scopes="user:read:chat user:write:chat")
    monkeypatch.setattr(twitch_auth, "get_settings", lambda: base)
    url, state = twitch_auth.create_authorization_url()
    assert "channel%3Amanage%3Apolls" not in url
    twitch_auth._pending_states.pop(state, None)
    url, state = twitch_auth.create_authorization_url(include_native_polls=True)
    scopes = parse_qs(urlparse(url).query)["scope"][0].split()
    assert scopes == ["channel:manage:polls", "user:read:chat", "user:write:chat"]
    twitch_auth._pending_states.pop(state, None)


@pytest.mark.parametrize(("enabled", "expected_scope"), [(False, False), (True, True)])
def test_oauth_start_route_uses_saved_opt_in_only(native_client, monkeypatch, enabled, expected_scope):
    client, sessions = native_client
    owner_cookie = "authenticated-owner-cookie"
    with sessions() as db:
        db.add(TwitchCredential(
            broadcaster_id="broadcaster-1", access_token="x", refresh_token="y", scopes="user:read:chat user:write:chat",
            expires_at=datetime.now(UTC) + timedelta(hours=1), auth_session_hash=main._hash_token(owner_cookie),
            auth_session_expires_at=datetime.now(UTC) + timedelta(days=1),
        ))
        db.add(BroadcasterSettings(broadcaster_id="broadcaster-1", native_twitch_polls_enabled=enabled))
        db.commit()
    monkeypatch.setattr(twitch_auth, "get_settings", lambda: Settings(
        _env_file=None, twitch_client_id="mock-client", twitch_client_secret="mock-secret",
        twitch_scopes="user:read:chat user:write:chat",
    ))
    response = client.get("/auth/twitch", cookies={"stream_pilot_session": owner_cookie}, follow_redirects=False)
    scope = parse_qs(urlparse(response.headers["location"]).query)["scope"][0].split()
    assert ("channel:manage:polls" in scope) is expected_scope
