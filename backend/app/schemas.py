from pydantic import BaseModel, ConfigDict, Field


class ChallengeOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=140)

class ChallengeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
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
