import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.database import Base
from app.models import (
    Challenge,
    ChatMessage,
    Interaction,
    InteractionGroup,
    PollSuggestion,
    StreamSession,
)
from app.services import interactions


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(interactions, "get_settings", lambda: Settings(
        _env_file=None, gemini_api_key="", interaction_fuzzy_threshold=0.78,
    ))
    with sessions() as session:
        stream = StreamSession(broadcaster_id="broadcaster-1", mode="live")
        session.add(stream)
        session.commit()
        session.refresh(stream)
        yield session, stream
    Base.metadata.drop_all(engine)
    engine.dispose()


def add_message(db, stream, event_id: str, user_id: str, text: str) -> ChatMessage:
    message = ChatMessage(
        event_id=event_id, session_id=stream.id, chatter_id=user_id,
        display_name=f"Viewer {user_id}", text=text, received_at=datetime.now(UTC),
    )
    db.add(message)
    db.flush()
    return message


async def _run_poll_batch(session, stream, monkeypatch, *, viewers=4, blocker=None, poll_suggestions=True):
    messages = []
    prefix = len(session.scalars(select(ChatMessage)).all())
    for index in range(viewers):
        preference = "ranked competitive" if index < viewers // 2 else "casual relaxed"
        message = add_message(session, stream, f"poll-{prefix}-{index}", f"poll-viewer-{prefix}-{index}",
                              f"Should we play {preference} matches tonight?")
        interaction = Interaction(
            broadcaster_id=stream.broadcaster_id, chat_message_id=message.event_id,
            user_login_hash=f"hashed-viewer-{prefix}-{index}", text=message.text,
        )
        session.add(interaction)
        session.flush()
        messages.append((interaction, message))
    if blocker == "active":
        session.add(Challenge(session_id=stream.id, title="Already active", question="Vote?",
                              reason="test", duration_seconds=30, status="active"))
    elif blocker == "cooldown":
        session.add(PollSuggestion(broadcaster_id=stream.broadcaster_id, session_id=stream.id,
                                   title="Recent poll", options=["A", "B"], duration_seconds=30,
                                   source_group_ids=[], supporting_message_ids=[], status="dismissed"))
    elif blocker == "pending":
        session.add(PollSuggestion(broadcaster_id=stream.broadcaster_id, session_id=stream.id,
                                   title="Waiting poll", options=["A", "B"], duration_seconds=30,
                                   source_group_ids=[], supporting_message_ids=[], status="suggested"))
    session.commit()
    monkeypatch.setattr(interactions, "get_settings", lambda: Settings(
        _env_file=None, gemini_api_key="mock-key", ai_request_timeout_seconds=1,
        poll_suggestion_cooldown_seconds=300,
    ))
    ids = [message.event_id for _, message in messages]
    midpoint = max(1, viewers // 2)
    output = {
        "decisions": [
            {"candidate_id": interaction.id, "group_id": None,
             "canonical_text": ("Should we queue ranked competitive matches tonight?" if index < midpoint
                                else "Should we choose casual relaxed matches tonight?"),
             "kind": "question"}
            for index, (interaction, _) in enumerate(messages)
        ],
        "poll_suggestions": ([{
            "title": "Ranked or casual tonight?",
            "options": [
                {"label": "Ranked", "supporting_message_ids": ids[:midpoint]},
                {"label": "Casual", "supporting_message_ids": ids[midpoint:]},
            ],
            "duration_seconds": 30,
        }] if poll_suggestions else []),
    }

    class FakeModels:
        calls = 0

        def generate_content(self, **_kwargs):
            self.calls += 1
            return SimpleNamespace(text=json.dumps(output))

    fake_models = FakeModels()
    monkeypatch.setattr(interactions.genai, "Client", lambda **_kwargs: SimpleNamespace(models=fake_models))
    await interactions.resolve_pending(session, broadcaster_id=stream.broadcaster_id, session_id=stream.id)
    return fake_models.calls


@pytest.mark.asyncio
async def test_batched_gemini_detects_competing_preferences_and_saves_poll(db, monkeypatch):
    session, stream = db
    calls = await _run_poll_batch(session, stream, monkeypatch)
    poll = session.scalar(select(PollSuggestion).where(PollSuggestion.broadcaster_id == stream.broadcaster_id))
    assert calls == 1
    assert poll is not None
    assert poll.title == "Ranked or casual tonight?"
    assert poll.options == ["Ranked", "Casual"]
    assert len(poll.source_group_ids) == 2
    assert len(poll.supporting_message_ids) == 4


@pytest.mark.asyncio
async def test_agreement_and_too_few_distinct_viewers_do_not_save_polls(db, monkeypatch):
    session, stream = db
    await _run_poll_batch(session, stream, monkeypatch, poll_suggestions=False)
    assert session.scalar(select(PollSuggestion)) is None

    # The model's suggestion is rejected server-side when the evidence has only two viewers.
    await _run_poll_batch(session, stream, monkeypatch, viewers=2)
    assert session.scalar(select(PollSuggestion)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["active", "cooldown"])
async def test_poll_safeguards_block_active_challenge_and_cooldown(db, monkeypatch, blocker):
    session, stream = db
    await _run_poll_batch(session, stream, monkeypatch, blocker=blocker)
    polls = session.scalars(select(PollSuggestion).where(
        PollSuggestion.broadcaster_id == stream.broadcaster_id,
        PollSuggestion.status == "suggested",
    )).all()
    assert polls == []


@pytest.mark.asyncio
async def test_pending_suggestion_blocks_another_and_new_suggestion_is_broadcast(db, monkeypatch):
    session, stream = db
    await _run_poll_batch(session, stream, monkeypatch, blocker="pending")
    assert len(session.scalars(select(PollSuggestion)).all()) == 1

    session.query(PollSuggestion).delete()
    session.commit()
    events = []

    async def capture_event(broadcaster_id, event):
        events.append((broadcaster_id, event["type"]))

    from app.services import dashboard_socket
    monkeypatch.setattr(dashboard_socket, "broadcast", capture_event)
    await _run_poll_batch(session, stream, monkeypatch, viewers=4)
    assert (stream.broadcaster_id, "poll.suggestion.created") in events


def test_question_detection_english_hinglish_commands_emotes_and_ignored_users() -> None:
    assert interactions.detect_candidate("How do I get the blue key?")[0] == "request"
    assert interactions.detect_candidate("kya ye GPU par chalega")[0] == "question"
    assert interactions.detect_candidate("Can you show the map please?")[0] == "request"
    assert interactions.detect_candidate("!ask what is the next level") is not None
    assert interactions.detect_candidate("Kappa PogChamp LUL") is None
    assert interactions.detect_candidate("custom_emote custom_emote", is_emote_only=True) is None
    assert interactions.event_is_emote_only([
        {"type": "emote", "text": "CUSTOM"}, {"type": "text", "text": " "},
    ])
    assert not interactions.event_is_emote_only([
        {"type": "emote", "text": "CUSTOM"}, {"type": "text", "text": " please help"},
    ])
    assert interactions.detect_candidate("!vote 2") is None
    assert interactions.detect_candidate("https://example.com/?query=value") is None
    assert interactions.detect_candidate("why?",) is None
    assert interactions.detect_candidate("How do I aim?", is_broadcaster=True) is None
    assert interactions.detect_candidate("How do I aim?", display_name="Nightbot") is None


@pytest.mark.asyncio
async def test_six_gpu_questions_fuzzy_group_with_distinct_asker_and_message_counts(db) -> None:
    session, stream = db
    questions = [
        "Can my GPU run this game?",
        "Can this GPU run the game?",
        "Will my GPU run this game?",
        "Can my GPU run the game?",
        "Will this GPU run my game?",
        "Can this game run on my GPU?",
    ]
    for index, question in enumerate(questions):
        message = add_message(session, stream, f"gpu-{index}", f"viewer-{index}", question)
        interactions.ingest_message(session, broadcaster_id=stream.broadcaster_id,
                                    session=stream, chat_message=message)
        session.commit()
        if index == 0:
            await interactions.resolve_pending(session, broadcaster_id=stream.broadcaster_id,
                                               session_id=stream.id)

    groups = session.scalars(select(InteractionGroup)).all()
    members = session.scalars(select(Interaction)).all()
    assert len(groups) == 1
    assert groups[0].asker_count == 6
    assert groups[0].message_count == 6
    assert len(members) == 6
    assert all(item.group_id == groups[0].id for item in members)


@pytest.mark.asyncio
async def test_batched_gemini_valid_response_attaches_candidate_to_existing_group(db, monkeypatch) -> None:
    session, stream = db
    group = InteractionGroup(
        broadcaster_id=stream.broadcaster_id, session_id=stream.id, kind="question",
        canonical_text="Can my GPU run this game?", normalized_text="can my gpu run this game",
        summary="Can my GPU run this game?", status="open",
    )
    session.add(group)
    message = add_message(session, stream, "candidate-1", "viewer-1", "Will this game run on my graphics card?")
    session.flush()
    session.add(Interaction(broadcaster_id=stream.broadcaster_id, chat_message_id=message.event_id,
                            user_login_hash="hashed-viewer", text=message.text))
    second_message = add_message(session, stream, "candidate-2", "viewer-2", "Can my GPU play this game?")
    session.flush()
    session.add(Interaction(broadcaster_id=stream.broadcaster_id, chat_message_id=second_message.event_id,
                            user_login_hash="hashed-viewer-2", text=second_message.text))
    session.commit()
    monkeypatch.setattr(interactions, "get_settings", lambda: Settings(
        _env_file=None, gemini_api_key="mock-key", ai_request_timeout_seconds=1,
    ))

    class FakeModels:
        calls = 0

        def generate_content(self, **_kwargs):
            self.calls += 1
            return SimpleNamespace(text=json.dumps({"decisions": [
                {"candidate_id": 1, "group_id": group.id, "canonical_text": None, "kind": "question"},
                {"candidate_id": 2, "group_id": group.id, "canonical_text": None, "kind": "question"},
            ]}))

    fake_models = FakeModels()
    monkeypatch.setattr(interactions.genai, "Client", lambda **_kwargs: SimpleNamespace(models=fake_models))
    assert await interactions.resolve_pending(session, broadcaster_id=stream.broadcaster_id,
                                              session_id=stream.id) == 2
    session.refresh(group)
    member = session.scalar(select(Interaction).where(Interaction.chat_message_id == message.event_id))
    assert member.group_id == group.id
    assert group.message_count == 2
    assert fake_models.calls == 1


@pytest.mark.asyncio
async def test_invalid_gemini_response_falls_back_to_deterministic_group(db, monkeypatch) -> None:
    session, stream = db
    message = add_message(session, stream, "candidate-invalid", "viewer-1", "How do I equip the shield?")
    session.add(Interaction(broadcaster_id=stream.broadcaster_id, chat_message_id=message.event_id,
                            user_login_hash="hashed-viewer", text=message.text))
    session.commit()
    monkeypatch.setattr(interactions, "get_settings", lambda: Settings(
        _env_file=None, gemini_api_key="mock-key", ai_request_timeout_seconds=1,
    ))

    class FakeModels:
        def generate_content(self, **_kwargs):
            return SimpleNamespace(text='{"decisions":[{}]}')

    monkeypatch.setattr(interactions.genai, "Client", lambda **_kwargs: SimpleNamespace(models=FakeModels()))
    assert await interactions.resolve_pending(session, broadcaster_id=stream.broadcaster_id,
                                              session_id=stream.id) == 1
    group = session.scalar(select(InteractionGroup))
    assert group is not None
    assert group.canonical_text == message.text
    assert group.status == "open"


@pytest.mark.asyncio
async def test_gemini_timeout_falls_back_and_keeps_queue_available(db, monkeypatch) -> None:
    session, stream = db
    message = add_message(session, stream, "candidate-timeout", "viewer-1", "Where is the hidden door?")
    session.add(Interaction(broadcaster_id=stream.broadcaster_id, chat_message_id=message.event_id,
                            user_login_hash="hashed-viewer", text=message.text))
    session.commit()
    monkeypatch.setattr(interactions, "get_settings", lambda: Settings(
        _env_file=None, gemini_api_key="mock-key", ai_request_timeout_seconds=1,
    ))

    def timeout_call(*_args):
        time.sleep(2)

    monkeypatch.setattr(interactions, "_generate_grouping", timeout_call)
    assert await interactions.resolve_pending(session, broadcaster_id=stream.broadcaster_id,
                                              session_id=stream.id) == 1
    assert session.scalar(select(InteractionGroup)) is not None


@pytest.mark.asyncio
async def test_prompt_injection_stays_untrusted_schema_valid_and_triggers_no_action(db, monkeypatch) -> None:
    session, stream = db
    injection_set = Path(__file__).resolve().parents[2] / "data" / "prompt_injection_messages.jsonl"
    samples = [json.loads(line) for line in injection_set.read_text(encoding="utf-8").splitlines()]
    injected_text = samples[0]["text"]
    message = add_message(session, stream, "prompt-injection-1", "viewer-inject", injected_text)
    item = Interaction(broadcaster_id=stream.broadcaster_id, chat_message_id=message.event_id,
                      user_login_hash="hashed-injector", text=message.text)
    session.add(item)
    session.commit()
    monkeypatch.setattr(interactions, "get_settings", lambda: Settings(
        _env_file=None, gemini_api_key="mock-key", ai_request_timeout_seconds=1,
    ))
    captured = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            captured["response_text"] = json.dumps({"decisions": [{
                "candidate_id": item.id, "group_id": None,
                "canonical_text": "How do I unlock the map?", "kind": "question",
            }]})
            return SimpleNamespace(text=captured["response_text"])

    monkeypatch.setattr(interactions.genai, "Client", lambda **_kwargs: SimpleNamespace(models=FakeModels()))
    send_calls = []

    async def forbidden_send(*_args, **_kwargs):
        send_calls.append(True)

    from app.services import twitch_chat
    monkeypatch.setattr(twitch_chat, "send_chat_message", forbidden_send)
    assert await interactions.resolve_pending(session, broadcaster_id=stream.broadcaster_id,
                                              session_id=stream.id) == 1
    output = interactions.GroupingOutput.model_validate_json(captured["response_text"])
    assert output.decisions[0].candidate_id == item.id
    assert "UNTRUSTED_JSON_START" in captured["contents"]
    assert injected_text in captured["contents"]
    assert "tools" not in captured["config"]
    assert send_calls == []
    assert session.scalar(select(Challenge)) is None
    assert session.scalar(select(InteractionGroup)).canonical_text == "How do I unlock the map?"


def test_priority_components_explain_rank_and_recent_repetition(db) -> None:
    _, stream = db
    now = datetime.now(UTC)
    low = InteractionGroup(
        broadcaster_id=stream.broadcaster_id, session_id=stream.id,
        canonical_text="one viewer", normalized_text="one viewer", status="open",
        asker_count=1, message_count=1, last_seen_at=now - timedelta(hours=12),
    )
    high = InteractionGroup(
        broadcaster_id=stream.broadcaster_id, session_id=stream.id,
        canonical_text="many viewers", normalized_text="many viewers", status="open",
        asker_count=6, message_count=10, last_seen_at=now,
    )
    low_components = interactions.score_group(low, now)
    high_components = interactions.score_group(high, now)
    assert high.priority_score > low.priority_score
    assert high_components == {
        "askers": high.priority_asker_component,
        "recency": high.priority_recency_component,
        "repetition": high.priority_repetition_component,
    }
    assert high_components["askers"] > low_components["askers"]
    assert high_components["repetition"] > low_components["repetition"]


def test_moderated_message_removes_its_interaction_and_recalculates_group(db) -> None:
    session, stream = db
    message = add_message(session, stream, "moderated", "viewer-1", "Where is the key?")
    group = InteractionGroup(
        broadcaster_id=stream.broadcaster_id, session_id=stream.id, canonical_text="Where is the key?",
        normalized_text="where is the key", summary="Where is the key?", status="open",
    )
    session.add(group)
    session.flush()
    session.add(Interaction(broadcaster_id=stream.broadcaster_id, group_id=group.id,
                            chat_message_id=message.event_id, user_login_hash="hashed-viewer",
                            text=message.text))
    session.commit()
    events = interactions.delete_interactions_for_chat_messages(session, [message.event_id])
    session.commit()
    assert session.scalar(select(Interaction)) is None
    assert session.scalar(select(InteractionGroup)) is None
    assert events == [{"type": "interaction.updated", "interaction": {"id": group.id, "deleted": True}}]


def test_moderation_replaces_canonical_text_with_retained_member(db) -> None:
    session, stream = db
    first = add_message(session, stream, "moderated-first", "viewer-1", "Where is the secret code?")
    remaining = add_message(session, stream, "retained-second", "viewer-2", "Where is the game map?")
    group = InteractionGroup(
        broadcaster_id=stream.broadcaster_id, session_id=stream.id,
        canonical_text=first.text, normalized_text=interactions.normalize_text(first.text),
        summary=first.text, status="open",
    )
    session.add(group)
    session.flush()
    session.add_all([
        Interaction(broadcaster_id=stream.broadcaster_id, group_id=group.id,
                    chat_message_id=first.event_id, user_login_hash="hash-1", text=first.text),
        Interaction(broadcaster_id=stream.broadcaster_id, group_id=group.id,
                    chat_message_id=remaining.event_id, user_login_hash="hash-2", text=remaining.text),
    ])
    session.commit()
    interactions.delete_interactions_for_chat_messages(session, [first.event_id])
    session.commit()
    assert group.canonical_text == remaining.text
    assert "secret code" not in group.canonical_text


def test_snooze_expiry_reopens_group(db) -> None:
    session, stream = db
    group = InteractionGroup(
        broadcaster_id=stream.broadcaster_id, session_id=stream.id,
        canonical_text="Where is the key?", normalized_text="where is the key",
        summary="Where is the key?", status="snoozed",
        snoozed_until=datetime.now(UTC) - timedelta(seconds=1),
    )
    session.add(group)
    session.commit()
    interactions.refresh_snoozed_groups(session, stream.broadcaster_id)
    assert group.status == "open"
    assert group.snoozed_until is None


def test_closed_group_reopens_after_configured_new_askers_join(db) -> None:
    session, stream = db
    group = InteractionGroup(
        broadcaster_id=stream.broadcaster_id, session_id=stream.id,
        canonical_text="Can my GPU run this game?", normalized_text="can my gpu run this game",
        summary="Can my GPU run this game?", status="dismissed", asker_count=1,
        closed_asker_count=1,
    )
    session.add(group)
    session.flush()
    original = add_message(session, stream, "closed-original", "old-viewer", "Can my GPU run this game?")
    session.add(Interaction(broadcaster_id=stream.broadcaster_id, group_id=group.id,
                            chat_message_id=original.event_id, user_login_hash="old-hash",
                            text=original.text))
    session.commit()
    for index in range(2):
        msg = add_message(session, stream, f"reopen-{index}", f"new-viewer-{index}",
                          "Can this GPU run the game?")
        interactions.ingest_message(session, broadcaster_id=stream.broadcaster_id,
                                    session=stream, chat_message=msg)
        session.commit()
        assert group.status == "dismissed"
    third = add_message(session, stream, "reopen-2", "new-viewer-2", "Can this GPU run the game?")
    interactions.ingest_message(session, broadcaster_id=stream.broadcaster_id,
                                session=stream, chat_message=third)
    session.commit()
    assert group.status == "open"
    assert group.asker_count == 4

