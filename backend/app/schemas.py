from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChallengeOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=140)

class ChallengeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "title": "Choose the next route",
        "question": "Which route should the player take?",
        "options": [
            {"label": "Left", "description": "Faster, with more enemies"},
            {"label": "Right", "description": "Safer, but longer"},
        ],
        "duration_seconds": 30,
        "reason": "Viewers suggested two viable paths.",
        "source": "gemini",
    }]})
    title: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=180)
    options: list[ChallengeOption] = Field(min_length=2, max_length=4)
    duration_seconds: int = Field(ge=15, le=60)
    reason: str = Field(min_length=1, max_length=240)
    source: str = Field(default="demo", pattern="^(gemini|fallback|demo)$")


class GeminiChallengeOption(BaseModel):
    """Gemini-compatible option schema; app validation remains stricter downstream."""

    label: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=140)


class GeminiChallengeOutput(BaseModel):
    """Provider output schema; source is assigned by the backend, never by the model."""

    title: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=180)
    options: list[GeminiChallengeOption] = Field(min_length=2, max_length=4)
    duration_seconds: int = Field(ge=15, le=60)
    reason: str = Field(min_length=1, max_length=240)

class HealthResponse(BaseModel):
    status: str
    app: str
    mode: str


class StatusResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "demo_mode": False,
        "gemini_configured": True,
        "twitch_configured": True,
        "twitch_authorized": True,
        "eventsub_worker": "running",
        "integrations_verified": False,
        "message": "Live service integrations remain unverified until authorized and tested against Twitch and Gemini.",
    }]})
    demo_mode: bool
    gemini_configured: bool
    twitch_configured: bool
    twitch_authorized: bool
    eventsub_worker: str
    integrations_verified: bool
    message: str


class ChallengeOptionResponse(BaseModel):
    id: int
    label: str
    description: str
    position: int
    votes: int


class ChallengeResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": 42,
        "session_id": 7,
        "title": "Choose the next route",
        "question": "Which route should the player take?",
        "reason": "Viewers suggested two viable paths.",
        "duration_seconds": 30,
        "source": "gemini",
        "status": "draft",
        "approved": False,
        "started_at": None,
        "ends_at": None,
        "ended_at": None,
        "options": [
            {"id": 81, "label": "Left", "description": "Faster, with more enemies", "position": 1, "votes": 0},
            {"id": 82, "label": "Right", "description": "Safer, but longer", "position": 2, "votes": 0},
        ],
        "total_votes": 0,
        "winning_option_id": None,
    }]})
    id: int
    session_id: int
    title: str
    question: str
    reason: str
    duration_seconds: int
    source: str
    status: str
    approved: bool
    started_at: datetime | None
    ends_at: datetime | None
    ended_at: datetime | None
    options: list[ChallengeOptionResponse]
    total_votes: int
    winning_option_id: int | None


class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "display_name": "ViewerOne",
        "text": "Take the left path!",
        "received_at": "2026-10-28T10:15:30Z",
    }]})
    display_name: str
    text: str
    received_at: datetime


class OverlayTokenResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "token": "example-token-not-real",
        "url": "http://localhost:5173/overlay?token=example-token-not-real",
    }]})
    token: str
    url: str
