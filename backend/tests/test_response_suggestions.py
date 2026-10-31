import json
from collections.abc import Generator
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.config import Settings
from app.database import Base, get_db
from app.models import (
    AIResponseAudit,
    AIResponseSuggestion,
    BroadcasterSettings,
    ChatMessage,
    Interaction,
    InteractionGroup,
    StreamSession,
    TwitchCredential,
)
from app.services import response_suggestions


@pytest.fixture
def client(monkeypatch) -> Generator[TestClient, None, None]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_db():
        db: Session = test_sessions()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[get_db] = override_db

    @asynccontextmanager
    async def isolated_lifespan(_application):
        yield

    monkeypatch.setattr(main.app.router, "lifespan_context", isolated_lifespan)
    with TestClient(main.app) as test_client:
        yield test_client
    main.app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def add_group(client: TestClient, *, facts: str = "") -> int:
    db = next(main.app.dependency_overrides[get_db]())
    stream = StreamSession(broadcaster_id="demo", mode="demo")
    db.add(stream)
    db.flush()
    group = InteractionGroup(broadcaster_id="demo", session_id=stream.id, kind="question",
                            canonical_text="What GPU is in your PC?", normalized_text="what gpu",
                            summary="GPU specs", status="open", asker_count=1, message_count=1)
    db.add(group)
    db.flush()
    chat = ChatMessage(event_id=f"reply-chat-{group.id}", session_id=stream.id, chatter_id="viewer",
                       display_name="Viewer", text="What GPU is in your PC?")
    db.add(chat)
    db.flush()
    db.add(Interaction(broadcaster_id="demo", group_id=group.id, chat_message_id=chat.event_id,
                       user_login_hash="hashed-viewer", text=chat.text))
    if facts:
        db.add(BroadcasterSettings(broadcaster_id="demo", stream_facts=facts))
    db.commit()
    group_id = group.id
    db.close()
    return group_id


@pytest.mark.asyncio
async def test_gemini_reply_is_structured_bounded_and_uses_untrusted_json(monkeypatch):
    class Models:
        def generate_content(self, **kwargs):
            assert kwargs["config"]["response_schema"] is response_suggestions.GeminiReplyOutput
            assert "UNTRUSTED JSON DATA" in kwargs["contents"]
            assert '"stream_facts": "RTX 4070"' in kwargs["contents"]
            return SimpleNamespace(text=json.dumps({"answer_supported_by_facts": True, "text": "I use an RTX 4070!"}),
                                   usage_metadata=SimpleNamespace(prompt_token_count=20, candidates_token_count=8))

    monkeypatch.setattr(response_suggestions, "get_settings", lambda: Settings(
        _env_file=None, gemini_api_key="mock-key", gemini_model="mock-model"))
    monkeypatch.setattr(response_suggestions.genai, "Client", lambda **_: SimpleNamespace(models=Models()))
    text, model = await response_suggestions.generate_reply("What GPU?", ["What GPU?"], "RTX 4070")
    assert text == "I use an RTX 4070!"
    assert model == "mock-model"


@pytest.mark.asyncio
async def test_missing_stream_facts_uses_holding_reply_without_gemini(monkeypatch):
    monkeypatch.setattr(response_suggestions, "get_settings", lambda: Settings(
        _env_file=None, gemini_api_key="mock-key", gemini_model="mock-model"))
    monkeypatch.setattr(response_suggestions.genai, "Client", lambda **_: pytest.fail("must not invent facts"))
    text, _ = await response_suggestions.generate_reply("What GPU?", ["What GPU?"], "")
    assert text == response_suggestions.HOLDING_REPLY
    assert response_suggestions.holding_reply("kya GPU use karte ho?").startswith("Achha sawaal")


def test_gemini_invalid_output_falls_back_to_safe_reply(monkeypatch):
    class Models:
        def generate_content(self, **_kwargs):
            return SimpleNamespace(text='{"text":"x"}')

    monkeypatch.setattr(response_suggestions, "get_settings", lambda: Settings(
        _env_file=None, gemini_api_key="mock-key", gemini_model="mock-model"))
    monkeypatch.setattr(response_suggestions.genai, "Client", lambda **_: SimpleNamespace(models=Models()))
    from asyncio import run

    text, _ = run(response_suggestions.generate_reply("question", [], "RTX 4070"))
    assert text == response_suggestions.HOLDING_REPLY


def test_suggest_edit_discard_never_sends_unless_send_endpoint_called(client: TestClient, monkeypatch):
    group_id = add_group(client, facts="I use an RTX 4070.")
    async def generated(*_args):
        return "I use an RTX 4070!", "mock-model"
    calls = []
    async def send(*_args):
        calls.append(True)
        return {"message_id": "twitch-message-1"}
    monkeypatch.setattr(main, "generate_reply", generated)
    monkeypatch.setattr(main, "send_chat_message_checked", send)

    suggestion = client.post(f"/api/interactions/{group_id}/response/suggest")
    assert suggestion.status_code == 200
    response_id = suggestion.json()["id"]
    edited = client.patch(f"/api/responses/{response_id}", json={"text": "Edited reply!"})
    assert edited.status_code == 200 and edited.json()["status"] == "edited"
    assert not calls
    assert client.post(f"/api/responses/{response_id}/discard").json()["status"] == "discarded"
    assert client.post(f"/api/responses/{response_id}/send").status_code == 409
    assert not calls


def test_explicit_send_marks_answered_and_writes_audit(client: TestClient, monkeypatch):
    group_id = add_group(client, facts="I use an RTX 4070.")
    db = next(main.app.dependency_overrides[get_db]())
    db.add(TwitchCredential(broadcaster_id="demo", access_token="encrypted", refresh_token="encrypted",
                            scopes="user:write:chat", expires_at=main.datetime.now(main.UTC)))
    db.commit()
    db.close()
    async def generated(*_args):
        return "I use an RTX 4070!", "mock-model"
    calls = []
    async def send(credential, text, _db):
        calls.append((credential.broadcaster_id, text))
        return {"message_id": "twitch-message-1"}
    monkeypatch.setattr(main, "generate_reply", generated)
    monkeypatch.setattr(main, "send_chat_message_checked", send)
    response_id = client.post(f"/api/interactions/{group_id}/response/suggest").json()["id"]
    client.patch(f"/api/responses/{response_id}", json={"text": "Edited and approved by streamer."})
    sent = client.post(f"/api/responses/{response_id}/send")
    assert sent.status_code == 200
    assert sent.json()["status"] == "sent"
    assert sent.json()["twitch_message_id"] == "twitch-message-1"
    assert calls == [("demo", "Edited and approved by streamer.")]
    check = next(main.app.dependency_overrides[get_db]())
    assert check.get(InteractionGroup, group_id).status == "answered"
    audit = check.query(AIResponseAudit).one()
    assert audit.actor_broadcaster_id == "demo"
    assert audit.sent_text == "Edited and approved by streamer."
    assert audit.twitch_message_id == "twitch-message-1"
    check.close()


def test_send_failure_is_inline_safe_and_does_not_close_group(client: TestClient, monkeypatch):
    group_id = add_group(client, facts="I use an RTX 4070.")
    db = next(main.app.dependency_overrides[get_db]())
    db.add(TwitchCredential(broadcaster_id="demo", access_token="encrypted", refresh_token="encrypted",
                            scopes="user:write:chat", expires_at=main.datetime.now(main.UTC)))
    db.commit()
    db.close()
    async def generated(*_args):
        return "Draft reply", "mock-model"
    async def rate_limited(*_args):
        raise HTTPException(status_code=429, detail="Twitch is rate limiting chat.")
    monkeypatch.setattr(main, "generate_reply", generated)
    monkeypatch.setattr(main, "send_chat_message_checked", rate_limited)
    response_id = client.post(f"/api/interactions/{group_id}/response/suggest").json()["id"]
    sent = client.post(f"/api/responses/{response_id}/send")
    assert sent.status_code == 429
    check = next(main.app.dependency_overrides[get_db]())
    assert check.get(InteractionGroup, group_id).status == "open"
    assert check.get(AIResponseSuggestion, response_id).status == "draft"
    assert check.query(AIResponseAudit).count() == 0
    check.close()


def test_reply_text_is_limited_to_500_characters(client: TestClient):
    group_id = add_group(client, facts="I use an RTX 4070.")
    response = client.patch("/api/responses/1", json={"text": "x" * 501})
    assert response.status_code == 422
    assert client.get(f"/api/interactions/{group_id}").status_code == 200


def test_response_endpoints_require_live_auth(client: TestClient, monkeypatch):
    monkeypatch.setattr(main.settings, "demo_mode", False)
    assert client.get("/api/settings/stream-facts").status_code == 401
    assert client.put("/api/settings/stream-facts", json={"stream_facts": "PC details"}).status_code == 401
    assert client.post("/api/interactions/1/response/suggest").status_code == 401
    assert client.patch("/api/responses/1", json={"text": "hello"}).status_code == 401
    assert client.post("/api/responses/1/send").status_code == 401


def test_response_and_facts_are_broadcaster_scoped(client: TestClient):
    db = next(main.app.dependency_overrides[get_db]())
    stream = StreamSession(broadcaster_id="other", mode="live")
    db.add(stream)
    db.flush()
    group = InteractionGroup(broadcaster_id="other", session_id=stream.id, kind="question",
                             canonical_text="Question?", normalized_text="question", summary="Question",
                             status="open", asker_count=1, message_count=1)
    db.add(group)
    db.flush()
    db.add(AIResponseSuggestion(group_id=group.id, broadcaster_id="other", text="private draft",
                                model="mock-model", status="draft"))
    db.add(BroadcasterSettings(broadcaster_id="other", stream_facts="Private streamer fact"))
    db.commit()
    db.close()

    assert client.get("/api/settings/stream-facts").json() == {"stream_facts": ""}
    assert client.patch("/api/responses/1", json={"text": "cross-tenant"}).status_code == 404
    assert client.post("/api/responses/1/send").status_code == 404
