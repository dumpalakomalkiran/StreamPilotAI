import time

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

    engine = create_engine("sqlite://", poolclass=StaticPool)
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
    engine = create_engine("sqlite://", poolclass=StaticPool)
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
