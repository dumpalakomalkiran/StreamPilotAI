from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class StreamSession(Base):
    __tablename__ = "stream_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    broadcaster_id: Mapped[str] = mapped_column(String(80), default="demo")
    mode: Mapped[str] = mapped_column(String(10), default="demo")
    overlay_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    featured_interaction_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    challenges: Mapped[list["Challenge"]] = relationship(back_populates="session")


class Challenge(Base):
    __tablename__ = "challenges"
    __table_args__ = (Index(
        "uq_single_active_challenge", "status", unique=True,
        sqlite_where=text("status = 'active'"), postgresql_where=text("status = 'active'"),
    ),)
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("stream_sessions.id"))
    title: Mapped[str] = mapped_column(String(80))
    question: Mapped[str] = mapped_column(String(180))
    reason: Mapped[str] = mapped_column(String(240))
    duration_seconds: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16), default="demo")
    status: Mapped[str] = mapped_column(String(16), default="draft")
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Kept as a scalar pointer to avoid a circular FK between challenges and options.
    winning_option_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session: Mapped[StreamSession] = relationship(back_populates="challenges")
    options: Mapped[list["ChallengeOption"]] = relationship(
        back_populates="challenge", cascade="all, delete-orphan", foreign_keys="ChallengeOption.challenge_id"
    )
    votes: Mapped[list["Vote"]] = relationship(back_populates="challenge", cascade="all, delete-orphan")


class ChallengeOption(Base):
    __tablename__ = "challenge_options"
    id: Mapped[int] = mapped_column(primary_key=True)
    challenge_id: Mapped[int] = mapped_column(ForeignKey("challenges.id"))
    label: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(140), default="")
    position: Mapped[int] = mapped_column(Integer)
    challenge: Mapped[Challenge] = relationship(back_populates="options", foreign_keys=[challenge_id])


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (UniqueConstraint("challenge_id", "twitch_user_id", name="uq_vote_user_challenge"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    challenge_id: Mapped[int] = mapped_column(ForeignKey("challenges.id"))
    option_id: Mapped[int] = mapped_column(ForeignKey("challenge_options.id"))
    twitch_user_id: Mapped[str] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    challenge: Mapped[Challenge] = relationship(back_populates="votes")


class ProcessedEvent(Base):
    __tablename__ = "processed_events"
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("stream_sessions.id"), index=True)
    chatter_id: Mapped[str] = mapped_column(String(80))
    display_name: Mapped[str] = mapped_column(String(80), default="Viewer")
    text: Mapped[str] = mapped_column(String(500))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InteractionGroup(Base):
    __tablename__ = "interaction_groups"
    __table_args__ = (Index("ix_interaction_group_scope_status", "broadcaster_id", "session_id", "status"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    broadcaster_id: Mapped[str] = mapped_column(String(80), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("stream_sessions.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="question")
    canonical_text: Mapped[str] = mapped_column(String(500))
    normalized_text: Mapped[str] = mapped_column(String(500), default="")
    summary: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    priority_score: Mapped[float] = mapped_column(Float, default=0)
    priority_asker_component: Mapped[float] = mapped_column(Float, default=0)
    priority_recency_component: Mapped[float] = mapped_column(Float, default=0)
    priority_repetition_component: Mapped[float] = mapped_column(Float, default=0)
    asker_count: Mapped[int] = mapped_column(Integer, default=0)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    closed_asker_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    members: Mapped[list["Interaction"]] = relationship(back_populates="group", cascade="all, delete-orphan")


class Interaction(Base):
    __tablename__ = "interactions"
    __table_args__ = (
        Index("ix_interaction_pending", "broadcaster_id", "group_id"),
        UniqueConstraint("chat_message_id", name="uq_interaction_chat_message"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    broadcaster_id: Mapped[str] = mapped_column(String(80), index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("interaction_groups.id"), nullable=True)
    chat_message_id: Mapped[str] = mapped_column(ForeignKey("chat_messages.event_id", ondelete="CASCADE"))
    user_login_hash: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    group: Mapped[InteractionGroup | None] = relationship(back_populates="members")


class BroadcasterSettings(Base):
    __tablename__ = "broadcaster_settings"
    broadcaster_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    stream_facts: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AIResponseSuggestion(Base):
    __tablename__ = "ai_response_suggestions"
    __table_args__ = (Index("ix_ai_response_scope", "broadcaster_id", "group_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("interaction_groups.id", ondelete="CASCADE"), index=True)
    broadcaster_id: Mapped[str] = mapped_column(String(80), index=True)
    text: Mapped[str] = mapped_column(String(500))
    model: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    twitch_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class AIResponseAudit(Base):
    __tablename__ = "ai_response_audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    response_id: Mapped[int] = mapped_column(ForeignKey("ai_response_suggestions.id"), index=True)
    broadcaster_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_broadcaster_id: Mapped[str] = mapped_column(String(80))
    sent_text: Mapped[str] = mapped_column(String(500))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    twitch_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class DeletedChatMessage(Base):
    """Short-lived message ID tombstone to prevent a replay restoring deleted chat."""
    __tablename__ = "deleted_chat_messages"
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("stream_sessions.id"), index=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TwitchCredential(Base):
    __tablename__ = "twitch_credentials"
    broadcaster_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str] = mapped_column(Text)
    scopes: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    auth_session_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    auth_session_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
