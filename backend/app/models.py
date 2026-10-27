from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
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


class TwitchCredential(Base):
    __tablename__ = "twitch_credentials"
    broadcaster_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str] = mapped_column(Text)
    scopes: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    auth_session_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    auth_session_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
