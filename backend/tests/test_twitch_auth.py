import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database import Base
from app.models import TwitchCredential
from app.services import twitch_auth


@pytest.mark.asyncio
async def test_oauth_state_mismatch_is_rejected_before_network(monkeypatch) -> None:
    state = "state-for-test"
    twitch_auth._pending_states[state] = time.monotonic()
    monkeypatch.setattr(twitch_auth, "get_settings", lambda: Settings(_env_file=None))

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db, pytest.raises(HTTPException) as exc:
            await twitch_auth.exchange_callback("code", state, "different-state", db)
        assert exc.value.status_code == 400
    finally:
        twitch_auth._pending_states.pop(state, None)
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.asyncio
async def test_oauth_callback_encrypts_tokens_and_records_identity(monkeypatch) -> None:
    state = "valid-state-for-test"
    twitch_auth._pending_states[state] = time.monotonic()
    settings = Settings(
        _env_file=None,
        app_secret_key="test-secret-that-is-not-used-outside-tests-1234",
        twitch_client_id="mock-client-id",
        twitch_client_secret="mock-client-secret",
        twitch_redirect_uri="http://localhost/callback",
        twitch_scopes="user:read:chat user:write:chat",
    )
    monkeypatch.setattr(twitch_auth, "get_settings", lambda: settings)

    class FakeResponse:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self.data

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResponse({
                "access_token": "mock-access-token",
                "refresh_token": "mock-refresh-token",
                "expires_in": 3600,
                "scope": ["user:read:chat", "user:write:chat"],
            })

        async def get(self, *_args, **_kwargs):
            return FakeResponse({"data": [{"id": "123", "login": "test_streamer"}]})

    monkeypatch.setattr(twitch_auth.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            user = await twitch_auth.exchange_callback("one-time-code", state, state, db)
            credential = db.get(TwitchCredential, "123")
            assert user == {"broadcaster_id": "123", "login": "test_streamer"}
            assert credential is not None
            assert credential.access_token != "mock-access-token"
            assert credential.refresh_token != "mock-refresh-token"
            assert twitch_auth.decrypt_token(credential.access_token) == "mock-access-token"
    finally:
        twitch_auth._pending_states.pop(state, None)
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_unreadable_stored_token_requires_reauthorization(monkeypatch) -> None:
    old_settings = Settings(_env_file=None, app_secret_key="previous-secret-key-for-test-123456789")
    monkeypatch.setattr(twitch_auth, "get_settings", lambda: old_settings)
    encrypted = twitch_auth.encrypt_token("old-access-token")

    new_settings = Settings(_env_file=None, app_secret_key="rotated-secret-key-for-test-123456789")
    monkeypatch.setattr(twitch_auth, "get_settings", lambda: new_settings)
    with pytest.raises(HTTPException) as exc:
        twitch_auth.decrypt_token(encrypted)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_concurrent_refreshes_share_one_rotated_token(monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        app_secret_key="refresh-test-secret-that-is-not-used-outside-tests-1234",
        twitch_client_id="client-id",
        twitch_client_secret="client-secret",
    )
    monkeypatch.setattr(twitch_auth, "get_settings", lambda: settings)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    calls = 0

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return FakeResponse()

    monkeypatch.setattr(twitch_auth.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    try:
        with Session(engine) as setup_db:
            credential = TwitchCredential(
                broadcaster_id="refresh-race-user",
                access_token=twitch_auth.encrypt_token("expired-access"),
                refresh_token=twitch_auth.encrypt_token("old-refresh"),
                scopes="user:read:chat user:write:chat",
                expires_at=datetime.now(UTC) - timedelta(seconds=5),
            )
            setup_db.add(credential)
            setup_db.commit()
        with Session(engine) as db_one, Session(engine) as db_two:
            credential_one = db_one.get(TwitchCredential, "refresh-race-user")
            credential_two = db_two.get(TwitchCredential, "refresh-race-user")
            assert credential_one is not None and credential_two is not None
            tokens = await asyncio.gather(
                twitch_auth.refresh_access_token_if_needed(credential_one, db_one),
                twitch_auth.refresh_access_token_if_needed(credential_two, db_two),
            )
            assert tokens == ["new-access", "new-access"]
            assert calls == 1
            db_one.refresh(credential_one)
            assert twitch_auth.decrypt_token(credential_one.refresh_token) == "new-refresh"
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.asyncio
async def test_forced_refresh_rotates_a_still_valid_token_for_health_check(monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        app_secret_key="forced-refresh-test-secret-not-used-outside-tests-1234",
        twitch_client_id="client-id",
        twitch_client_secret="client-secret",
    )
    monkeypatch.setattr(twitch_auth, "get_settings", lambda: settings)
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"access_token": "rotated-access", "refresh_token": "rotated-refresh", "expires_in": 3600}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, **kwargs):
            calls.append(kwargs)
            return FakeResponse()

    monkeypatch.setattr(twitch_auth.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            credential = TwitchCredential(
                broadcaster_id="force-refresh-user",
                access_token=twitch_auth.encrypt_token("old-access"),
                refresh_token=twitch_auth.encrypt_token("old-refresh"),
                scopes="user:read:chat user:write:chat",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            db.add(credential)
            db.commit()
            token = await twitch_auth.refresh_access_token_if_needed(credential, db, force=True)
            assert token == "rotated-access"
            assert len(calls) == 1
            assert twitch_auth.decrypt_token(credential.refresh_token) == "rotated-refresh"
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
