"""Conservative flag-only chat moderation signals; this module has no Twitch API dependency."""

import re
from datetime import UTC, datetime, timedelta
from itertools import groupby

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ChatMessage, ModerationIncident, StreamSession

REPEAT_COUNT = 4
REPEAT_WINDOW_SECONDS = 20
URL_PATTERN = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
WORD_PATTERN = re.compile(r"\S+")


class ModerationClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: str = Field(min_length=1, max_length=128)
    flag: bool
    category: str = Field(pattern="^(spam|repeated|link|harassment|other)$")
    severity: str = Field(pattern="^(low|medium|high)$")
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=240)


def incident_payload(incident: ModerationIncident) -> dict[str, object]:
    return {
        "id": incident.id,
        "session_id": incident.session_id,
        "message_id": incident.chat_message_id,
        "user_display_name": incident.user_display_name,
        "category": incident.category,
        "severity": incident.severity,
        "confidence": incident.confidence,
        "reason": incident.reason,
        "status": incident.status,
        "created_at": incident.created_at,
    }


def _normalise_message(text: str) -> str:
    return " ".join(text.casefold().split())


def _prior_chat_exists(db: Session, session_id: int, chatter_id: str, current_id: str) -> bool:
    return db.scalar(select(ChatMessage.event_id).where(
        ChatMessage.session_id == session_id,
        ChatMessage.chatter_id == chatter_id,
        ChatMessage.event_id != current_id,
    ).limit(1)) is not None


def detect_deterministic_incident(
    db: Session, *, broadcaster_id: str, session: StreamSession,
    message: ChatMessage, fragments: object = None,
) -> ModerationIncident | None:
    """Apply bounded same-session rules, creating at most one flag for a chat message."""
    if message.chatter_id == broadcaster_id or db.scalar(select(ModerationIncident.id).where(
        ModerationIncident.chat_message_id == message.event_id
    )):
        return None

    text = message.text.strip()
    category: str | None = None
    severity = "medium"
    confidence = 0.82
    reason = ""

    if text and URL_PATTERN.search(text) and not _prior_chat_exists(
        db, session.id, message.chatter_id, message.event_id
    ):
        category, severity, confidence = "link", "medium", 0.9
        reason = "Contains a link from an account with no earlier chat in this session."

    if category is None:
        cutoff = message.received_at - timedelta(seconds=REPEAT_WINDOW_SECONDS)
        prior = db.scalars(select(ChatMessage.text).where(
            ChatMessage.session_id == session.id,
            ChatMessage.chatter_id == message.chatter_id,
            ChatMessage.event_id != message.event_id,
            ChatMessage.received_at >= cutoff,
            ChatMessage.received_at <= message.received_at,
        ).order_by(ChatMessage.received_at.desc()).limit(500)).all()
        repeated = sum(_normalise_message(value) == _normalise_message(text) for value in prior)
        if repeated >= REPEAT_COUNT - 1:
            category, severity, confidence = "repeated", "medium", 0.9
            reason = f"The same message was posted {REPEAT_COUNT} or more times within {REPEAT_WINDOW_SECONDS} seconds."

    if category is None:
        letters = [char for char in text if char.isalpha()]
        caps_ratio = sum(char.isupper() for char in letters) / len(letters) if letters else 0
        emote_fragments = sum(
            isinstance(fragment, dict) and fragment.get("type") == "emote"
            for fragment in (fragments if isinstance(fragments, list) else [])
        )
        repeated_tokens = max((len(list(group)) for _, group in groupby(
            word.casefold() for word in WORD_PATTERN.findall(text)
        )), default=0)
        if emote_fragments >= 8 or repeated_tokens >= 6:
            category, severity, confidence = "spam", "low", 0.8
            reason = "Message contains an unusually large burst of repeated emotes."
        elif len(letters) >= 12 and caps_ratio >= 0.8:
            category, severity, confidence = "spam", "medium", 0.86
            reason = "Message uses unusually high capitalisation."

    if category is None:
        return None
    incident = ModerationIncident(
        broadcaster_id=broadcaster_id, session_id=session.id,
        chat_message_id=message.event_id, user_display_name=message.display_name[:80],
        category=category, severity=severity, confidence=confidence, reason=reason,
        status="open",
    )
    db.add(incident)
    db.flush()
    return incident


def is_borderline_message(text: str, fragments: object = None) -> bool:
    """Cheaply select only plausible edge cases for the already batched Gemini call."""
    letters = [char for char in text if char.isalpha()]
    caps_ratio = sum(char.isupper() for char in letters) / len(letters) if letters else 0
    if len(letters) >= 10 and 0.55 <= caps_ratio < 0.8:
        return True
    if isinstance(fragments, list):
        emotes = sum(isinstance(fragment, dict) and fragment.get("type") == "emote" for fragment in fragments)
        if 3 <= emotes < 8:
            return True
    tokens = [word.casefold() for word in WORD_PATTERN.findall(text)]
    return max((len(list(group)) for _, group in groupby(tokens)), default=0) in range(3, 6)


def create_ai_incidents(
    db: Session, *, broadcaster_id: str, session_id: int,
    classifications: list[ModerationClassification], allowed_message_ids: set[str],
) -> list[ModerationIncident]:
    """Accept only validated, in-batch IDs. AI can add a flag; it cannot cause an action."""
    created: list[ModerationIncident] = []
    for item in classifications[:20]:
        if item.message_id not in allowed_message_ids:
            continue
        message = db.scalar(select(ChatMessage).join(
            StreamSession, StreamSession.id == ChatMessage.session_id
        ).where(
            ChatMessage.event_id == item.message_id,
            ChatMessage.session_id == session_id,
            StreamSession.broadcaster_id == broadcaster_id,
        ))
        if message is None:
            continue
        message.moderation_ai_checked = True
        if not item.flag or item.confidence < 0.65 or db.scalar(select(ModerationIncident.id).where(
            ModerationIncident.chat_message_id == message.event_id
        )) is not None:
            continue
        incident = ModerationIncident(
            broadcaster_id=broadcaster_id, session_id=session_id,
            chat_message_id=message.event_id, user_display_name=message.display_name[:80],
            category=item.category, severity=item.severity, confidence=item.confidence,
            reason=item.reason[:240], status="open",
        )
        db.add(incident)
        db.flush()
        created.append(incident)
    return created


def borderline_messages(db: Session, *, broadcaster_id: str, session_id: int, limit: int = 20):
    cutoff = datetime.now(UTC) - timedelta(minutes=20)
    rows = db.execute(select(ChatMessage).join(
        StreamSession, StreamSession.id == ChatMessage.session_id
    ).where(
        ChatMessage.session_id == session_id,
        StreamSession.broadcaster_id == broadcaster_id,
        ChatMessage.received_at >= cutoff,
        ChatMessage.moderation_ai_checked.is_(False),
        ChatMessage.chatter_id != broadcaster_id,
        ~ChatMessage.event_id.in_(select(ModerationIncident.chat_message_id)),
    ).order_by(ChatMessage.received_at.desc()).limit(200)).scalars().all()
    result = []
    for row in rows:
        if is_borderline_message(row.text):
            result.append({"message_id": row.event_id, "text": row.text[:240]})
            if len(result) >= limit:
                break
    return result
