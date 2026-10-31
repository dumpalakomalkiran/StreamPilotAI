import asyncio
import hashlib
import logging
import secrets
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Path,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.database import Base, SessionLocal, engine, get_db, upgrade_schema
from app.models import (
    AIResponseAudit,
    AIResponseSuggestion,
    AuditLog,
    BroadcasterSettings,
    Challenge,
    ChallengeOption,
    ChatMessage,
    Interaction,
    InteractionGroup,
    ModerationIncident,
    PollSuggestion,
    SessionSummary,
    StreamSession,
    TwitchCredential,
)
from app.schemas import (
    AnalyticsResponse,
    ChallengeProposal,
    ChallengeResponse,
    ChatMessageResponse,
    EditResponseSuggestionRequest,
    HealthResponse,
    InteractionDetailResponse,
    InteractionGroupResponse,
    ModerationIncidentResponse,
    NativePollSettingsRequest,
    NativePollSettingsResponse,
    NativeTwitchPollCreateRequest,
    NativeTwitchPollResponse,
    OverlayTokenResponse,
    PollSuggestionApproval,
    PollSuggestionResponse,
    ResponseSuggestionResponse,
    SessionSummaryRequest,
    SessionSummaryResponse,
    SnoozeInteractionRequest,
    StatusResponse,
    StreamFactsRequest,
    StreamFactsResponse,
)
from app.services import twitch_polls
from app.services.analytics import (
    current_analytics,
    generate_summary_async,
    generate_summary_background,
)
from app.services.challenge_clock import run_challenge_clock
from app.services.challenges import generate_challenge
from app.services.dashboard_socket import broadcast as dashboard_broadcast
from app.services.dashboard_socket import connect as dashboard_connect
from app.services.dashboard_socket import disconnect as dashboard_disconnect
from app.services.dashboard_socket import interaction_payload as dashboard_interaction_payload
from app.services.eventsub import run_eventsub
from app.services.interactions import refresh_snoozed_groups, run_grouping_worker, score_group
from app.services.overlay import broadcast as overlay_broadcast
from app.services.overlay import connect as overlay_connect
from app.services.overlay import disconnect as overlay_disconnect
from app.services.overlay import revoke as overlay_revoke
from app.services.question_overlay import broadcast as question_overlay_broadcast
from app.services.question_overlay import connect as question_overlay_connect
from app.services.question_overlay import disconnect as question_overlay_disconnect
from app.services.response_suggestions import generate_reply
from app.services.twitch_auth import create_authorization_url, exchange_callback
from app.services.twitch_chat import send_chat_message, send_chat_message_checked
from app.services.voting import (
    cast_vote,
    challenge_payload,
    challenge_result_message,
    close_challenge,
    create_challenge,
    loaded_challenge,
)

settings = get_settings()
logger = logging.getLogger(__name__)
Base.metadata.create_all(bind=engine)
upgrade_schema()
app = FastAPI(
    title="StreamPilot AI API",
    version="0.1.0",
    description=(
        "Streamer-controlled Twitch chat challenges. All `/api` endpoints in live mode "
        "require the HttpOnly `stream_pilot_session` cookie created by Twitch OAuth. "
        "Gemini suggestions are generated only on explicit request; every proposal requires approval. "
        "Secrets and raw provider diagnostics are never returned by these APIs."
    ),
    contact={"name": "StreamPilot AI"},
    openapi_tags=[
        {"name": "System", "description": "Health and safe integration status."},
        {"name": "Twitch OAuth", "description": "Broadcaster authorization redirects and callback."},
        {"name": "Challenges", "description": "Suggest, review, launch, and end streamer-approved challenges."},
        {"name": "Chat", "description": "Recent live chat signals after Twitch moderation deletions are applied."},
        {"name": "Interactions", "description": "Prioritized, grouped viewer questions and requests."},
        {"name": "Moderation", "description": "Conservative streamer-review flags; StreamPilot never takes moderation actions on Twitch."},
        {"name": "Overlay", "description": "Create a private browser overlay credential."},
    ],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
)

class SuggestRequest(BaseModel):
    model_config = {"json_schema_extra": {"examples": [{"chat_messages": [
        "Take the left path!", "Maybe save the healing item for the boss."
    ]}]}}
    chat_messages: list[Annotated[str, Field(max_length=500)]] = Field(
        default_factory=list, max_length=50,
        description="Recent chat context. Each message is treated as untrusted data; maximum 50 messages, 500 characters each.",
    )


class VoteRequest(BaseModel):
    model_config = {"json_schema_extra": {"examples": [{
        "twitch_user_id": "123456789", "option_number": 2, "event_id": "demo-event-001"
    }]}}
    twitch_user_id: str = Field(min_length=1, max_length=80, description="Viewer Twitch user ID; demo mode only.")
    option_number: int = Field(ge=1, le=5, description="One-based option position.")
    event_id: str | None = Field(default=None, max_length=128, description="Optional idempotency key.")


_suggest_requests: dict[str, list[float]] = {}


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def require_streamer(request: Request, db: Session = Depends(get_db)) -> str:
    if settings.demo_mode:
        return "demo"
    session_token = request.cookies.get("stream_pilot_session")
    if session_token:
        credential = db.scalar(select(TwitchCredential).where(
            TwitchCredential.auth_session_hash == _hash_token(session_token),
            TwitchCredential.auth_session_expires_at > datetime.now(UTC),
        ))
        if credential:
            return credential.broadcaster_id
    raise HTTPException(status_code=401, detail="Connect Twitch to manage challenges")

@app.get(
    "/api/health", response_model=HealthResponse, tags=["System"],
    summary="Check backend health",
    description="Unauthenticated liveness check. Does not verify Gemini or Twitch connectivity.",
    responses={200: {"content": {"application/json": {"example": {
        "status": "ok", "app": "StreamPilot AI", "mode": "live"
    }}}}},
)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", app="StreamPilot AI", mode="demo" if settings.demo_mode else "live")

@app.get(
    "/api/status", response_model=StatusResponse, tags=["System"],
    summary="Read safe integration status",
    description="Returns configuration and worker booleans only. `integrations_verified` stays false until real-service checks pass.",
    responses={200: {"content": {"application/json": {"example": {
        "demo_mode": False, "gemini_configured": True, "twitch_configured": True,
        "twitch_authorized": True, "eventsub_worker": "running",
        "integrations_verified": False,
        "message": "Live service integrations remain unverified until authorized and tested against Twitch and Gemini.",
    }}}}},
)
def status(db: Session = Depends(get_db)) -> dict[str, object]:
    eventsub_task = getattr(app.state, "eventsub_task", None)
    eventsub_worker = "not_started" if eventsub_task is None else (
        "running" if not eventsub_task.done() else "stopped"
    )
    twitch_configured = bool(settings.twitch_client_id and settings.twitch_client_secret)
    twitch_authorized = (
        not settings.demo_mode
        and twitch_configured
        and db.scalar(select(TwitchCredential.broadcaster_id).where(
            TwitchCredential.auth_session_hash.is_not(None),
            TwitchCredential.auth_session_expires_at > datetime.now(UTC),
        ).limit(1)) is not None
    )
    return {
        "demo_mode": settings.demo_mode,
        "gemini_configured": bool(settings.gemini_api_key and settings.gemini_model),
        "twitch_configured": twitch_configured,
        "twitch_authorized": twitch_authorized,
        "eventsub_worker": eventsub_worker,
        "integrations_verified": False,
        "message": "Live service integrations remain unverified until authorized and tested against Twitch and Gemini."
    }


def _authorized_broadcaster() -> str | None:
    db = next(get_db())
    try:
        return db.scalar(select(TwitchCredential.broadcaster_id).where(
            TwitchCredential.auth_session_hash.is_not(None),
            TwitchCredential.auth_session_expires_at > datetime.now(UTC),
        ).limit(1))
    finally:
        db.close()


@asynccontextmanager
async def lifespan(application: FastAPI):
    app.state.challenge_clock_task = asyncio.create_task(run_challenge_clock())
    app.state.interaction_grouping_task = asyncio.create_task(run_grouping_worker())
    if not settings.demo_mode:
        broadcaster_id = await run_in_threadpool(_authorized_broadcaster)
        if broadcaster_id:
            app.state.eventsub_broadcaster_id = broadcaster_id
            app.state.eventsub_task = asyncio.create_task(run_eventsub(broadcaster_id))
    try:
        yield
    finally:
        for task_name in ("eventsub_task", "challenge_clock_task", "interaction_grouping_task"):
            task = getattr(app.state, task_name, None)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass


app.router.lifespan_context = lifespan

@app.post(
    "/api/challenges/suggest", response_model=ChallengeProposal, tags=["Challenges"],
    summary="Generate a challenge suggestion",
    description="Calls configured Gemini only when explicitly requested. Chat context is untrusted and bounded. The response `source` is `gemini` or a labeled `fallback`; this endpoint never approves or launches the proposal.",
    responses={
        200: {"description": "Validated suggestion or labeled fallback."},
        401: {"description": "Broadcaster session is missing or expired.", "content": {"application/json": {"example": {"detail": "Connect Twitch to manage challenges"}}}},
        429: {"description": "Per-client suggestion rate limit reached.", "content": {"application/json": {"example": {"detail": "Suggestion limit reached. Try again shortly."}}}},
    },
)
async def suggest_challenge(
    payload: SuggestRequest, request: Request, _streamer: str = Depends(require_streamer)
) -> ChallengeProposal:
    now = time.monotonic()
    client = request.client.host if request.client else "unknown"
    recent = [stamp for stamp in _suggest_requests.get(client, []) if now - stamp < 60]
    if len(recent) >= 5:
        raise HTTPException(status_code=429, detail="Suggestion limit reached. Try again shortly.")
    recent.append(now)
    if len(_suggest_requests) >= 10_000 and client not in _suggest_requests:
        for address, stamps in list(_suggest_requests.items()):
            if not stamps or now - stamps[-1] >= 60:
                _suggest_requests.pop(address, None)
        if len(_suggest_requests) >= 10_000:
            raise HTTPException(status_code=429, detail="Suggestion service is busy. Try again shortly.")
    _suggest_requests[client] = recent
    safe_messages = [message[:500] for message in payload.chat_messages]
    return await generate_challenge(safe_messages)


@app.get(
    "/auth/twitch", tags=["Twitch OAuth"], summary="Start Twitch broadcaster authorization",
    description="Creates an OAuth state cookie and redirects the browser to Twitch. The callback returns to the configured `TWITCH_REDIRECT_URI`.",
    responses={302: {"description": "Redirect to Twitch authorization."}},
)
async def twitch_authorize(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    include_native_polls = False
    session_token = request.cookies.get("stream_pilot_session")
    if session_token:
        credential = db.scalar(select(TwitchCredential).where(
            TwitchCredential.auth_session_hash == _hash_token(session_token),
            TwitchCredential.auth_session_expires_at > datetime.now(UTC),
        ))
        if credential:
            preference = db.get(BroadcasterSettings, credential.broadcaster_id)
            include_native_polls = bool(preference and preference.native_twitch_polls_enabled)
    url, state = create_authorization_url(include_native_polls=include_native_polls)
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        "twitch_oauth_state", state, max_age=600, httponly=True,
        secure=settings.app_env == "production", samesite="lax", path="/auth/twitch/callback",
    )
    return response


@app.get(
    "/auth/twitch/callback", tags=["Twitch OAuth"], summary="Complete Twitch authorization",
    description="Validates OAuth state, exchanges the code server-to-server, stores encrypted tokens, and sets an HttpOnly broadcaster session cookie.",
    responses={
        303: {"description": "Redirect back to the dashboard; sets the session cookie."},
        400: {"description": "OAuth was denied or state/code is incomplete."},
        401: {"description": "OAuth state is invalid or Twitch authorization expired."},
        502: {"description": "Twitch authorization service is temporarily unavailable."},
    },
)
async def twitch_callback(
    request: Request,
    code: str | None = Query(default=None, description="One-time authorization code returned by Twitch."),
    state: str | None = Query(default=None, description="OAuth state returned by Twitch; must match the HttpOnly state cookie."),
    error: str | None = Query(default=None, description="OAuth denial code, if the broadcaster declined authorization."),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if error:
        raise HTTPException(status_code=400, detail="Twitch authorization was not granted")
    if not code or not state:
        raise HTTPException(status_code=400, detail="OAuth callback is incomplete")
    cookie_state = request.cookies.get("twitch_oauth_state")
    user = await exchange_callback(code, state, cookie_state, db)
    session_token = secrets.token_urlsafe(32)

    def save_auth_session_and_ensure_stream() -> None:
        credential = db.get(TwitchCredential, user["broadcaster_id"])
        credential.auth_session_hash = _hash_token(session_token)
        credential.auth_session_expires_at = datetime.now(UTC) + timedelta(days=7)
        db.commit()
        session = db.scalar(select(StreamSession).where(
            StreamSession.broadcaster_id == user["broadcaster_id"], StreamSession.ended_at.is_(None)
        ).order_by(StreamSession.id.desc()))
        if session is None:
            db.add(StreamSession(broadcaster_id=user["broadcaster_id"], mode="live"))
            db.commit()

    await run_in_threadpool(save_auth_session_and_ensure_stream)
    redirect = RedirectResponse(settings.frontend_url.rstrip("/"), status_code=303)
    redirect.delete_cookie("twitch_oauth_state", path="/auth/twitch/callback")
    redirect.set_cookie(
        "stream_pilot_session", session_token, max_age=604800, httponly=True,
        secure=settings.app_env == "production", samesite="strict", path="/",
    )
    task = getattr(app.state, "eventsub_task", None)
    if task is None or task.done():
        app.state.eventsub_task = asyncio.create_task(run_eventsub(user["broadcaster_id"]))
        app.state.eventsub_broadcaster_id = user["broadcaster_id"]
    return redirect


@app.get(
    "/api/challenges", response_model=list[ChallengeResponse], tags=["Challenges"],
    summary="List challenge history",
    description="Returns up to the latest 100 challenges belonging to the authenticated broadcaster, newest first.",
)
def list_challenges(db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)) -> list[dict[str, object]]:
    statement = select(Challenge).join(StreamSession).options(
        selectinload(Challenge.options), selectinload(Challenge.votes)
    ).order_by(Challenge.id.desc()).limit(100)
    if broadcaster_id != "demo":
        statement = statement.where(StreamSession.broadcaster_id == broadcaster_id)
    challenges = db.scalars(statement).all()
    return [challenge_payload(db, challenge) for challenge in challenges]


@app.get("/api/analytics/current", response_model=AnalyticsResponse, tags=["Analytics"],
         summary="Read live community analytics",
         description="Returns counts for the authenticated broadcaster's current session and a 30-minute message histogram. Counts reflect persisted, moderation-filtered chat.")
def analytics_current(db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)):
    return current_analytics(db, broadcaster_id)


@app.post("/api/analytics/current/end", response_model=SessionSummaryResponse, tags=["Analytics"],
          summary="End the current stream session",
          description="Closes the broadcaster's active session and schedules its cached recap after returning. Recap generation never delays this response.")
def end_current_session(background_tasks: BackgroundTasks, db: Session = Depends(get_db),
                        broadcaster_id: str = Depends(require_streamer)):
    session = db.scalar(select(StreamSession).where(StreamSession.broadcaster_id == broadcaster_id,
                                                      StreamSession.ended_at.is_(None)).order_by(StreamSession.id.desc()))
    if session is None:
        raise HTTPException(status_code=404, detail="No active session")
    session.ended_at = datetime.now(UTC)
    summary_row = db.scalar(select(SessionSummary).where(SessionSummary.session_id == session.id,
                                                          SessionSummary.broadcaster_id == broadcaster_id))
    if summary_row is None:
        summary_row = SessionSummary(broadcaster_id=broadcaster_id, session_id=session.id,
                                     content=None, status="pending", model="pending")
        db.add(summary_row)
    else:
        summary_row.status = "pending"
    db.commit()
    background_tasks.add_task(generate_summary_background, broadcaster_id, session.id)
    background_tasks.add_task(dashboard_broadcast, broadcaster_id, {"type": "session.ended"})
    cached = summary_row
    return {"session_id": session.id, "status": cached.status if cached else "pending",
            "generated_at": cached.generated_at if cached else None, "content": cached.content if cached else None}


@app.get("/api/analytics/sessions/{session_id}/summary", response_model=SessionSummaryResponse,
         tags=["Analytics"], summary="Read a cached stream recap",
         description="Returns the generated recap, or a not_generated status while no recap is cached. This read never calls Gemini.")
def get_session_summary(session_id: int, db: Session = Depends(get_db),
                        broadcaster_id: str = Depends(require_streamer)):
    session = db.scalar(select(StreamSession).where(StreamSession.id == session_id,
                                                     StreamSession.broadcaster_id == broadcaster_id))
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    cached = db.scalar(select(SessionSummary).where(SessionSummary.session_id == session_id,
                                                     SessionSummary.broadcaster_id == broadcaster_id))
    if cached is None:
        return {"session_id": session_id, "status": "not_generated", "content": None}
    return {"session_id": session_id, "status": cached.status,
            "generated_at": cached.generated_at, "content": cached.content}


@app.get("/api/analytics/latest-summary", response_model=SessionSummaryResponse,
         tags=["Analytics"], summary="Read the latest stream recap",
         description="Returns the authenticated broadcaster's latest cached recap, including one generated after Twitch reports the stream offline.")
def get_latest_session_summary(db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)):
    cached = db.scalar(select(SessionSummary).where(SessionSummary.broadcaster_id == broadcaster_id)
                       .order_by(SessionSummary.generated_at.desc()).limit(1))
    if cached is None:
        raise HTTPException(status_code=404, detail="No recap is available yet")
    return {"session_id": cached.session_id, "status": cached.status,
            "generated_at": cached.generated_at, "content": cached.content}


@app.post("/api/analytics/sessions/{session_id}/summary", response_model=SessionSummaryResponse,
          tags=["Analytics"], summary="Generate or retrieve a stream recap",
          description="Uses only stored aggregate analytics, poll results, and the top queue groups; never sends full raw chat. Cached unless regenerate=true.")
async def post_session_summary(session_id: int, payload: SessionSummaryRequest = SessionSummaryRequest(),
                               db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)):
    session = db.scalar(select(StreamSession).where(StreamSession.id == session_id,
                                                     StreamSession.broadcaster_id == broadcaster_id))
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    cached = db.scalar(select(SessionSummary).where(SessionSummary.session_id == session_id,
                                                     SessionSummary.broadcaster_id == broadcaster_id))
    if cached is not None and (not payload.regenerate or cached.status == "pending"):
        return {"session_id": session_id, "status": cached.status,
                "generated_at": cached.generated_at, "content": cached.content}
    try:
        return await generate_summary_async(broadcaster_id, session_id, payload.regenerate)
    except Exception as exc:
        logger.warning("Explicit session summary generation failed (session=%s, error=%s)", session_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Session recap could not be generated. Try again later.") from exc


@app.get(
    "/api/chat/messages", response_model=list[ChatMessageResponse], tags=["Chat"],
    summary="List recent chat signals",
    description="Returns recent messages for the current live session. Twitch message-delete and clear notifications remove matching rows, so removed messages are excluded from both this response and subsequent Gemini context.",
)
def list_chat_messages(
    limit: int = Query(default=50, ge=1, le=100, description="Maximum messages to return, newest messages selected."),
    db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)
) -> list[dict[str, object]]:
    limit = max(1, min(limit, 100))
    session = db.scalar(select(StreamSession).where(
        StreamSession.broadcaster_id == broadcaster_id,
        StreamSession.ended_at.is_(None),
    ).order_by(StreamSession.id.desc()))
    if session is None:
        return []
    messages = db.scalars(select(ChatMessage).where(
        ChatMessage.session_id == session.id
    ).order_by(ChatMessage.received_at.desc()).limit(limit)).all()
    return [
        {"display_name": message.display_name, "text": message.text, "received_at": message.received_at}
        for message in reversed(messages)
    ]


def _interaction_payload(db: Session, group: InteractionGroup, *, include_messages: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": group.id, "kind": group.kind, "canonical_text": group.canonical_text,
        "summary": group.summary, "status": group.status, "snoozed_until": group.snoozed_until,
        "priority_score": group.priority_score,
        "priority_components": {
            "askers": group.priority_asker_component,
            "recency": group.priority_recency_component,
            "repetition": group.priority_repetition_component,
        },
        "asker_count": group.asker_count, "message_count": group.message_count,
        "first_seen_at": group.first_seen_at, "last_seen_at": group.last_seen_at,
        "answered_at": group.answered_at,
    }
    if include_messages:
        rows = db.execute(select(Interaction, ChatMessage.display_name).join(
            ChatMessage, ChatMessage.event_id == Interaction.chat_message_id
        ).where(
            Interaction.broadcaster_id == group.broadcaster_id,
            Interaction.group_id == group.id,
        ).order_by(Interaction.created_at)).all()
        payload["messages"] = [
            {"display_name": display_name, "text": interaction.text, "created_at": interaction.created_at}
            for interaction, display_name in rows
        ]
    return payload


def _scoped_interaction_group(db: Session, group_id: int, broadcaster_id: str) -> InteractionGroup:
    group = db.scalar(select(InteractionGroup).join(
        StreamSession, StreamSession.id == InteractionGroup.session_id
    ).where(
        InteractionGroup.id == group_id,
        InteractionGroup.broadcaster_id == broadcaster_id,
        StreamSession.broadcaster_id == broadcaster_id,
    ))
    if group is None:
        raise HTTPException(status_code=404, detail="Interaction not found")
    return group


def _scoped_response(db: Session, response_id: int, broadcaster_id: str) -> AIResponseSuggestion:
    suggestion = db.scalar(select(AIResponseSuggestion).where(
        AIResponseSuggestion.id == response_id,
        AIResponseSuggestion.broadcaster_id == broadcaster_id,
    ))
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Response suggestion not found")
    return suggestion


def _scoped_poll_suggestion(db: Session, suggestion_id: int, broadcaster_id: str) -> PollSuggestion:
    suggestion = db.scalar(select(PollSuggestion).join(
        StreamSession, StreamSession.id == PollSuggestion.session_id
    ).where(
        PollSuggestion.id == suggestion_id,
        PollSuggestion.broadcaster_id == broadcaster_id,
        StreamSession.broadcaster_id == broadcaster_id,
    ))
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Poll suggestion not found")
    return suggestion


def _poll_suggestion_payload(db: Session, suggestion: PollSuggestion) -> dict[str, object]:
    rows = db.execute(select(ChatMessage.display_name, ChatMessage.text, ChatMessage.received_at).join(
        Interaction, Interaction.chat_message_id == ChatMessage.event_id
    ).where(
        Interaction.broadcaster_id == suggestion.broadcaster_id,
        Interaction.chat_message_id.in_(suggestion.supporting_message_ids),
        ChatMessage.session_id == suggestion.session_id,
    ).order_by(ChatMessage.received_at)).all() if suggestion.supporting_message_ids else []
    return {
        "id": suggestion.id, "session_id": suggestion.session_id, "title": suggestion.title,
        "options": suggestion.options, "duration_seconds": suggestion.duration_seconds,
        "source_group_ids": suggestion.source_group_ids, "status": suggestion.status,
        "created_at": suggestion.created_at,
        "supporting_messages": [
            {"display_name": name, "text": text, "created_at": received_at}
            for name, text, received_at in rows
        ],
    }


@app.get("/api/settings/stream-facts", response_model=StreamFactsResponse, tags=["Settings"],
         summary="Read streamer-provided reply facts", description="Returns the authenticated broadcaster's optional facts used to ground reply drafts. Never contains credentials.")
def get_stream_facts(db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)):
    row = db.get(BroadcasterSettings, broadcaster_id)
    return {"stream_facts": row.stream_facts if row else ""}


@app.put("/api/settings/stream-facts", response_model=StreamFactsResponse, tags=["Settings"],
         summary="Save streamer-provided reply facts", description="Stores up to 3000 characters of optional streamer-provided facts for grounded reply suggestions. Do not enter secrets.")
def put_stream_facts(payload: StreamFactsRequest, db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)):
    row = db.get(BroadcasterSettings, broadcaster_id)
    if row is None:
        row = BroadcasterSettings(broadcaster_id=broadcaster_id, stream_facts=payload.stream_facts.strip())
        db.add(row)
    else:
        row.stream_facts = payload.stream_facts.strip()
    db.commit()
    return {"stream_facts": row.stream_facts}


def _native_poll_settings_payload(db: Session, broadcaster_id: str) -> dict[str, object]:
    preference = db.get(BroadcasterSettings, broadcaster_id)
    enabled = bool(preference and preference.native_twitch_polls_enabled)
    credential = db.get(TwitchCredential, broadcaster_id)
    has_scope = bool(credential and twitch_polls.NATIVE_POLL_SCOPE in set(credential.scopes.split()))
    required = enabled and not has_scope
    return {
        "enabled": enabled,
        "reauthorization_required": required,
        "message": "Reconnect Twitch to grant channel:manage:polls before creating native polls." if required
                   else "Native Twitch poll creation is enabled." if enabled
                   else "Native Twitch polls are off. Existing StreamPilot !vote N polls remain available.",
    }


@app.get("/api/settings/native-polls", response_model=NativePollSettingsResponse, tags=["Settings"],
         summary="Read optional native Twitch poll setting",
         description="Native polls are off by default. Enabling them never creates a poll; it prompts the streamer to reconnect and grant `channel:manage:polls`.")
def get_native_poll_settings(db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)):
    return _native_poll_settings_payload(db, broadcaster_id)


@app.put("/api/settings/native-polls", response_model=NativePollSettingsResponse, tags=["Settings"],
         summary="Enable or disable native Twitch polls",
         description="The `channel:manage:polls` OAuth scope is requested only after the streamer explicitly enables this setting. Existing scopes are unchanged when disabled.")
def put_native_poll_settings(payload: NativePollSettingsRequest, db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)):
    row = db.get(BroadcasterSettings, broadcaster_id)
    if row is None:
        row = BroadcasterSettings(broadcaster_id=broadcaster_id, stream_facts="", native_twitch_polls_enabled=payload.enabled)
        db.add(row)
    else:
        row.native_twitch_polls_enabled = payload.enabled
    db.commit()
    return _native_poll_settings_payload(db, broadcaster_id)


@app.get("/api/polls/twitch", response_model=list[NativeTwitchPollResponse], tags=["Native Twitch polls"],
         summary="List native Twitch polls created by StreamPilot",
         description="Returns this broadcaster's tracked Twitch poll IDs and statuses. When enabled and authorized, status is refreshed from Twitch Helix.")
async def list_native_twitch_polls(db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)):
    return await twitch_polls.list_native_polls(db, broadcaster_id)


@app.post("/api/polls/twitch/create", response_model=NativeTwitchPollResponse, status_code=201, tags=["Native Twitch polls"],
          summary="Create an approved suggestion as a Twitch poll",
          description="Creates a real poll immediately on Twitch only after explicit streamer action. Requires an enabled setting, granted scope, Affiliate/Partner eligibility, and no active Twitch or StreamPilot poll.",
          responses={201: {"description": "Twitch poll created and tracked."},
                     403: {"description": "Channel is not eligible for native Twitch polls; use the existing StreamPilot !vote N challenge."},
                     409: {"description": "Setting/scope missing, suggestion not approved, or another poll is active."},
                     429: {"description": "Twitch rate limit reached."}})
async def create_native_twitch_poll(payload: NativeTwitchPollCreateRequest, db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)):
    return await twitch_polls.create_native_poll(db, broadcaster_id, payload.suggestion_id, payload.duration_seconds)


@app.post("/api/polls/twitch/{poll_id}/end", response_model=NativeTwitchPollResponse, tags=["Native Twitch polls"],
          summary="End a tracked native Twitch poll",
          description="Ends the broadcaster-owned active poll through Twitch Helix using status `TERMINATED`, which keeps its public results visible.",
          responses={404: {"description": "Tracked poll not found for this broadcaster."}, 409: {"description": "Poll is no longer active or scope/authorization is missing."}})
async def end_native_twitch_poll(poll_id: int = Path(ge=1), db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)):
    return await twitch_polls.end_native_poll(db, broadcaster_id, poll_id)


@app.get(
    "/api/interactions", response_model=list[InteractionGroupResponse], tags=["Interactions"],
    summary="List prioritized chat interactions",
    description="Lists grouped viewer questions and requests for the authenticated broadcaster, sorted by deterministic priority. Moderated messages are removed from the queue.",
    responses={401: {"description": "Broadcaster session is missing or expired."}},
)
def list_interactions(
    status: str | None = Query(default=None, pattern="^(open|answered|snoozed|dismissed)$"),
    kind: str | None = Query(default=None, pattern="^(question|request|suggestion|other)$"),
    q: str | None = Query(default=None, max_length=120, description="Search canonical text and summary."),
    db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer),
) -> list[dict[str, object]]:
    refresh_snoozed_groups(db, broadcaster_id)
    db.commit()
    statement = select(InteractionGroup).join(
        StreamSession, StreamSession.id == InteractionGroup.session_id
    ).where(
        InteractionGroup.broadcaster_id == broadcaster_id,
        StreamSession.broadcaster_id == broadcaster_id,
    )
    if status:
        statement = statement.where(InteractionGroup.status == status)
    if kind:
        statement = statement.where(InteractionGroup.kind == kind)
    if q:
        search = f"%{q.strip()}%"
        statement = statement.where(
            InteractionGroup.canonical_text.ilike(search) | InteractionGroup.summary.ilike(search)
        )
    groups = db.scalars(statement).all()
    now = datetime.now(UTC)
    for group in groups:
        score_group(group, now)
    db.commit()
    groups.sort(key=lambda group: (
        group.priority_score,
        group.last_seen_at.replace(tzinfo=UTC) if group.last_seen_at.tzinfo is None else group.last_seen_at,
    ), reverse=True)
    groups = groups[:200]
    return [_interaction_payload(db, group) for group in groups]


def _moderation_payload(db: Session, incident: ModerationIncident) -> dict[str, object]:
    message = db.get(ChatMessage, incident.chat_message_id)
    return {
        "id": incident.id, "session_id": incident.session_id,
        "message_id": incident.chat_message_id,
        "user_display_name": incident.user_display_name,
        "message": message.text if message else "[message no longer retained]",
        "category": incident.category, "severity": incident.severity,
        "confidence": incident.confidence, "reason": incident.reason,
        "status": incident.status, "created_at": incident.created_at,
    }


@app.get("/api/moderation/incidents", response_model=list[ModerationIncidentResponse],
         tags=["Moderation"], summary="List chat moderation flags",
         description="Returns review suggestions for the authenticated broadcaster. Flags never cause a Twitch action.")
def list_moderation_incidents(
    status: str = Query(default="open", pattern="^(open|dismissed|reviewed|all)$"),
    db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer),
) -> list[dict[str, object]]:
    query = select(ModerationIncident).join(
        StreamSession, StreamSession.id == ModerationIncident.session_id
    ).where(
        ModerationIncident.broadcaster_id == broadcaster_id,
        StreamSession.broadcaster_id == broadcaster_id,
    )
    if status != "all":
        query = query.where(ModerationIncident.status == status)
    rows = db.scalars(query.order_by(ModerationIncident.created_at.desc()).limit(200)).all()
    return [_moderation_payload(db, incident) for incident in rows]


def _transition_moderation_incident(
    db: Session, incident_id: int, broadcaster_id: str, status: str,
) -> dict[str, object]:
    incident = db.scalar(select(ModerationIncident).join(
        StreamSession, StreamSession.id == ModerationIncident.session_id
    ).where(
        ModerationIncident.id == incident_id,
        ModerationIncident.broadcaster_id == broadcaster_id,
        StreamSession.broadcaster_id == broadcaster_id,
    ))
    if incident is None:
        raise HTTPException(status_code=404, detail="Moderation incident not found")
    if incident.status != "open":
        raise HTTPException(status_code=409, detail="Only open moderation flags can be updated")
    old_status = incident.status
    incident.status = status
    db.add(AuditLog(
        broadcaster_id=broadcaster_id, actor_broadcaster_id=broadcaster_id,
        entity_type="moderation_incident", entity_id=incident.id,
        action=f"moderation_incident.{status}", from_status=old_status,
        to_status=status,
    ))
    db.commit()
    db.refresh(incident)
    return _moderation_payload(db, incident)


@app.post("/api/moderation/incidents/{incident_id}/dismiss",
          response_model=ModerationIncidentResponse, tags=["Moderation"],
          summary="Dismiss a moderation flag", description="Records the broadcaster's review decision; no Twitch action is made.")
def dismiss_moderation_incident(
    incident_id: int = Path(ge=1), db: Session = Depends(get_db),
    broadcaster_id: str = Depends(require_streamer),
) -> dict[str, object]:
    return _transition_moderation_incident(db, incident_id, broadcaster_id, "dismissed")


@app.post("/api/moderation/incidents/{incident_id}/reviewed",
          response_model=ModerationIncidentResponse, tags=["Moderation"],
          summary="Mark a moderation flag reviewed", description="Records the broadcaster's review decision; no Twitch action is made.")
def review_moderation_incident(
    incident_id: int = Path(ge=1), db: Session = Depends(get_db),
    broadcaster_id: str = Depends(require_streamer),
) -> dict[str, object]:
    return _transition_moderation_incident(db, incident_id, broadcaster_id, "reviewed")


@app.get(
    "/api/interactions/{interaction_id}", response_model=InteractionDetailResponse, tags=["Interactions"],
    summary="Read an interaction and its member messages",
    description="Returns the interaction group and its retained, non-moderated member chat messages for the authenticated broadcaster.",
    responses={401: {"description": "Broadcaster session is missing or expired."}, 404: {"description": "Interaction not found for this broadcaster."}},
)
def get_interaction(
    interaction_id: int = Path(ge=1), db: Session = Depends(get_db),
    broadcaster_id: str = Depends(require_streamer),
) -> dict[str, object]:
    group = _scoped_interaction_group(db, interaction_id, broadcaster_id)
    return _interaction_payload(db, group, include_messages=True)


def _transition_interaction(db: Session, interaction_id: int, broadcaster_id: str, action: str,
                            minutes: int | None = None) -> dict[str, object]:
    group = _scoped_interaction_group(db, interaction_id, broadcaster_id)
    refresh_snoozed_groups(db, broadcaster_id)
    if group.status != "open":
        raise HTTPException(status_code=409, detail="Only open interactions can be updated")
    if action == "answer":
        group.status = "answered"
        group.answered_at = datetime.now(UTC)
        group.closed_asker_count = group.asker_count
    elif action == "dismiss":
        group.status = "dismissed"
        group.closed_asker_count = group.asker_count
    else:
        group.status = "snoozed"
        group.snoozed_until = datetime.now(UTC) + timedelta(minutes=minutes or 1)
    db.commit()
    db.refresh(group)
    session = db.get(StreamSession, group.session_id)
    was_featured = bool(session and session.featured_interaction_group_id == group.id)
    if was_featured:
        session.featured_interaction_group_id = None
        db.commit()
    result = _interaction_payload(db, group, include_messages=True)
    if was_featured:
        result["_featured_cleared"] = True
    return result


@app.post(
    "/api/interactions/{interaction_id}/answer", response_model=InteractionDetailResponse, tags=["Interactions"],
    summary="Mark an interaction answered", description="Marks an open interaction as answered and records the time. Only the owning broadcaster can update it.", responses={404: {"description": "Interaction not found for this broadcaster."}, 409: {"description": "Interaction is not open."}},
)
def answer_interaction(interaction_id: int = Path(ge=1), background: BackgroundTasks = None,
                       db: Session = Depends(get_db),
                       broadcaster_id: str = Depends(require_streamer)) -> dict[str, object]:
    result = _transition_interaction(db, interaction_id, broadcaster_id, "answer")
    featured_cleared = bool(result.pop("_featured_cleared", False))
    if background is not None:
        background.add_task(dashboard_broadcast, broadcaster_id, {"type": "interaction.updated", "interaction": _queue_event_payload(result)})
        session = _interaction_session(db, interaction_id, broadcaster_id)
        if featured_cleared and session and session.overlay_token_hash:
            background.add_task(question_overlay_broadcast, session.overlay_token_hash, {"type": "question.hidden"})
    return result


@app.post(
    "/api/interactions/{interaction_id}/dismiss", response_model=InteractionDetailResponse, tags=["Interactions"],
    summary="Dismiss an interaction", description="Dismisses an open interaction for this broadcaster; enough distinct new askers can reopen it later.", responses={404: {"description": "Interaction not found for this broadcaster."}, 409: {"description": "Interaction is not open."}},
)
def dismiss_interaction(interaction_id: int = Path(ge=1), background: BackgroundTasks = None,
                        db: Session = Depends(get_db),
                        broadcaster_id: str = Depends(require_streamer)) -> dict[str, object]:
    result = _transition_interaction(db, interaction_id, broadcaster_id, "dismiss")
    featured_cleared = bool(result.pop("_featured_cleared", False))
    if background is not None:
        background.add_task(dashboard_broadcast, broadcaster_id, {"type": "interaction.updated", "interaction": _queue_event_payload(result)})
        session = _interaction_session(db, interaction_id, broadcaster_id)
        if featured_cleared and session and session.overlay_token_hash:
            background.add_task(question_overlay_broadcast, session.overlay_token_hash, {"type": "question.hidden"})
    return result


def _interaction_session(db: Session, interaction_id: int, broadcaster_id: str) -> StreamSession | None:
    group = db.scalar(select(InteractionGroup).where(
        InteractionGroup.id == interaction_id, InteractionGroup.broadcaster_id == broadcaster_id
    ))
    return db.get(StreamSession, group.session_id) if group else None


def _queue_event_payload(payload: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "messages"}


@app.post(
    "/api/interactions/{interaction_id}/snooze", response_model=InteractionDetailResponse, tags=["Interactions"],
    summary="Snooze an interaction", description="Snoozes an open interaction for the requested number of minutes (1–1440); it reopens automatically at expiry.", responses={404: {"description": "Interaction not found for this broadcaster."}, 409: {"description": "Interaction is not open."}},
)
def snooze_interaction(payload: SnoozeInteractionRequest, interaction_id: int = Path(ge=1),
                       background: BackgroundTasks = None,
                       db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)) -> dict[str, object]:
    result = _transition_interaction(db, interaction_id, broadcaster_id, "snooze", payload.minutes)
    featured_cleared = bool(result.pop("_featured_cleared", False))
    if background is not None:
        background.add_task(dashboard_broadcast, broadcaster_id, {"type": "interaction.updated", "interaction": _queue_event_payload(result)})
        session = _interaction_session(db, interaction_id, broadcaster_id)
        if featured_cleared and session and session.overlay_token_hash:
            background.add_task(question_overlay_broadcast, session.overlay_token_hash, {"type": "question.hidden"})
    return result


@app.post("/api/interactions/{interaction_id}/feature", tags=["Interactions"],
          summary="Show a safe queue question on the overlay",
          description="Features only the canonical text and viewer count for an open queue item; member messages and identities are never sent to the overlay.")
def feature_interaction(interaction_id: int = Path(ge=1), background: BackgroundTasks = None,
                        db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)):
    group = _scoped_interaction_group(db, interaction_id, broadcaster_id)
    if group.status != "open":
        raise HTTPException(status_code=409, detail="Only open interactions can be featured")
    session = db.get(StreamSession, group.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Stream session not found")
    session.featured_interaction_group_id = group.id
    db.commit()
    if background is not None:
        background.add_task(dashboard_broadcast, broadcaster_id, {
            "type": "interaction.updated", "interaction": dashboard_interaction_payload(group), "featured": True,
        })
        if session.overlay_token_hash:
            background.add_task(question_overlay_broadcast, session.overlay_token_hash, {
                "type": "question.featured", "question": {
                    "canonical_text": group.canonical_text, "kind": group.kind,
                    "asker_count": group.asker_count,
                },
            })
    return {"status": "featured", "interaction_id": group.id}


@app.post("/api/interactions/{interaction_id}/response/suggest", response_model=ResponseSuggestionResponse,
          tags=["Interactions"], summary="Generate a grounded reply draft",
          description="Calls Gemini only when explicitly requested. The draft is persisted for review and is never sent automatically. Viewer text is bounded JSON-encoded untrusted data; only streamer-provided facts can support factual claims.",
          responses={404: {"description": "Interaction not found for this broadcaster."}})
async def suggest_interaction_response(interaction_id: int = Path(ge=1), db: Session = Depends(get_db),
                                      broadcaster_id: str = Depends(require_streamer)):
    group = _scoped_interaction_group(db, interaction_id, broadcaster_id)
    if group.status != "open":
        raise HTTPException(status_code=409, detail="Replies can only be drafted for open interactions")
    members = db.scalars(select(Interaction).where(
        Interaction.group_id == group.id,
        Interaction.broadcaster_id == broadcaster_id,
    ).order_by(Interaction.created_at.desc()).limit(5)).all()
    samples = [member.text[:300] for member in reversed(members)]
    facts_row = db.get(BroadcasterSettings, broadcaster_id)
    text, model = await generate_reply(group.canonical_text, samples, facts_row.stream_facts if facts_row else "")
    suggestion = AIResponseSuggestion(group_id=group.id, broadcaster_id=broadcaster_id,
                                      text=text[:300], model=model, status="draft")
    db.add(suggestion)
    db.commit()
    db.refresh(suggestion)
    return suggestion


@app.patch("/api/responses/{response_id}", response_model=ResponseSuggestionResponse, tags=["Responses"],
           summary="Edit a reply draft", description="Edits an unsent reply draft for the authenticated broadcaster. Maximum 500 characters.",
           responses={404: {"description": "Response suggestion not found."}, 409: {"description": "Response was already sent or discarded."}})
def edit_response_suggestion(payload: EditResponseSuggestionRequest, response_id: int = Path(ge=1),
                             db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)):
    suggestion = _scoped_response(db, response_id, broadcaster_id)
    if suggestion.status not in {"draft", "edited"}:
        raise HTTPException(status_code=409, detail="Only an unsent draft can be edited")
    suggestion.text = payload.text.strip()
    if not suggestion.text:
        raise HTTPException(status_code=422, detail="Reply text cannot be blank")
    suggestion.status = "edited"
    db.commit()
    db.refresh(suggestion)
    return suggestion


@app.post("/api/responses/{response_id}/discard", response_model=ResponseSuggestionResponse, tags=["Responses"],
          summary="Discard an unsent reply draft", description="Marks the review draft as discarded without sending it.")
def discard_response_suggestion(response_id: int = Path(ge=1), db: Session = Depends(get_db),
                                broadcaster_id: str = Depends(require_streamer)):
    suggestion = _scoped_response(db, response_id, broadcaster_id)
    if suggestion.status not in {"draft", "edited"}:
        raise HTTPException(status_code=409, detail="Only an unsent draft can be discarded")
    suggestion.status = "discarded"
    db.commit()
    db.refresh(suggestion)
    return suggestion


@app.post("/api/responses/{response_id}/send", response_model=ResponseSuggestionResponse, tags=["Responses"],
          summary="Explicitly send a reply to Twitch chat", description="This is the only reply-suggestion operation that calls Twitch Send Chat Message. Sends the streamer-reviewed text (max 500 chars); on success records who/when/what and marks its interaction answered.",
          responses={401: {"description": "Reconnect Twitch."}, 404: {"description": "Response suggestion not found."}, 409: {"description": "Draft is not sendable or queue item is closed."}, 429: {"description": "Twitch chat rate limit."}, 502: {"description": "Twitch rejected or could not send the message."}})
async def send_response_suggestion(response_id: int = Path(ge=1), background: BackgroundTasks = None,
                                   db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)):
    suggestion = _scoped_response(db, response_id, broadcaster_id)
    if suggestion.status not in {"draft", "edited"}:
        raise HTTPException(status_code=409, detail="Only an unsent draft can be sent")
    if not suggestion.text.strip() or len(suggestion.text) > 500:
        raise HTTPException(status_code=422, detail="Twitch chat messages must contain 1–500 characters")
    group = _scoped_interaction_group(db, suggestion.group_id, broadcaster_id)
    if group.status != "open":
        raise HTTPException(status_code=409, detail="This interaction is no longer open")
    credential = db.get(TwitchCredential, broadcaster_id)
    if credential is None:
        raise HTTPException(status_code=401, detail="Connect Twitch before sending a reply")
    result = await send_chat_message_checked(credential, suggestion.text, db)
    sent_at = datetime.now(UTC)
    suggestion.status = "sent"
    suggestion.sent_at = sent_at
    suggestion.twitch_message_id = result.get("message_id")
    group.status = "answered"
    group.answered_at = sent_at
    group.closed_asker_count = group.asker_count
    db.add(AIResponseAudit(response_id=suggestion.id, broadcaster_id=broadcaster_id,
                           actor_broadcaster_id=broadcaster_id, sent_text=suggestion.text,
                           sent_at=sent_at, twitch_message_id=suggestion.twitch_message_id))
    session = db.get(StreamSession, group.session_id)
    was_featured = bool(session and session.featured_interaction_group_id == group.id)
    if was_featured:
        session.featured_interaction_group_id = None
    db.commit()
    db.refresh(suggestion)
    payload = _interaction_payload(db, group, include_messages=True)
    if background is not None:
        background.add_task(dashboard_broadcast, broadcaster_id,
                            {"type": "interaction.updated", "interaction": _queue_event_payload(payload)})
        if was_featured and session and session.overlay_token_hash:
            background.add_task(question_overlay_broadcast, session.overlay_token_hash, {"type": "question.hidden"})
    return suggestion


@app.get(
    "/api/polls/suggestions", response_model=list[PollSuggestionResponse], tags=["Poll suggestions"],
    summary="List pending AI poll suggestions",
    description="Returns current-session, broadcaster-scoped audience polls inferred from competing chat preferences. Suggestions never start until the streamer approves them.",
)
def list_poll_suggestions(
    db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)
) -> list[dict[str, object]]:
    now = datetime.now(UTC)
    expired = db.scalars(select(PollSuggestion).join(
        StreamSession, StreamSession.id == PollSuggestion.session_id
    ).where(
        PollSuggestion.broadcaster_id == broadcaster_id,
        StreamSession.broadcaster_id == broadcaster_id,
        PollSuggestion.status == "suggested",
        (StreamSession.ended_at.is_not(None)) | (PollSuggestion.created_at < now - timedelta(hours=24)),
    )).all()
    for row in expired:
        row.status = "expired"
    if expired:
        db.commit()
    suggestions = db.scalars(select(PollSuggestion).join(
        StreamSession, StreamSession.id == PollSuggestion.session_id
    ).where(
        PollSuggestion.broadcaster_id == broadcaster_id,
        StreamSession.broadcaster_id == broadcaster_id,
        StreamSession.ended_at.is_(None),
        PollSuggestion.status == "suggested",
        PollSuggestion.created_at >= now - timedelta(hours=24),
    ).order_by(PollSuggestion.created_at.desc()).limit(20)).all()
    return [_poll_suggestion_payload(db, row) for row in suggestions]


@app.post(
    "/api/polls/suggestions/{suggestion_id}/approve", response_model=ChallengeResponse,
    tags=["Poll suggestions"], summary="Approve and convert a poll suggestion",
    description="Accepts the streamer's edited title/options/duration and creates an unapproved existing Challenge draft. The dashboard then uses the normal challenge approve and launch endpoints; AI never starts a poll.",
    responses={404: {"description": "Suggestion not found for this broadcaster."},
               409: {"description": "Suggestion is no longer pending or another challenge is active."}},
)
def approve_poll_suggestion(
    payload: PollSuggestionApproval,
    suggestion_id: int = Path(ge=1),
    db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer),
) -> dict[str, object]:
    suggestion = _scoped_poll_suggestion(db, suggestion_id, broadcaster_id)
    if suggestion.status != "suggested":
        if suggestion.status == "approved" and suggestion.approved_challenge_id is not None:
            existing = db.scalar(select(Challenge).where(
                Challenge.id == suggestion.approved_challenge_id,
                Challenge.status == "draft",
                Challenge.approved.is_(False),
            ))
            if existing is not None:
                return challenge_payload(db, loaded_challenge(db, existing.id))
        raise HTTPException(status_code=409, detail="Poll suggestion is no longer pending")
    if db.scalar(select(Challenge.id).where(Challenge.status == "active")):
        raise HTTPException(status_code=409, detail="Another challenge is active")
    session = db.get(StreamSession, suggestion.session_id)
    if session is None or session.ended_at is not None:
        suggestion.status = "expired"
        db.commit()
        raise HTTPException(status_code=409, detail="Poll suggestion's stream session has ended")
    challenge = Challenge(
        session_id=session.id, title=payload.title.strip(), question=payload.title.strip(),
        reason="Streamer-approved audience poll based on competing viewer preferences.",
        duration_seconds=payload.duration_seconds, source="gemini", status="draft", approved=False,
        options=[ChallengeOption(label=label, description="Audience poll option", position=index)
                 for index, label in enumerate(payload.options, start=1)],
    )
    db.add(challenge)
    db.flush()
    suggestion.title = payload.title.strip()
    suggestion.options = payload.options
    suggestion.duration_seconds = payload.duration_seconds
    suggestion.status = "approved"
    suggestion.approved_challenge_id = challenge.id
    db.commit()
    db.refresh(challenge)
    return challenge_payload(db, loaded_challenge(db, challenge.id))


@app.post(
    "/api/polls/suggestions/{suggestion_id}/dismiss", response_model=PollSuggestionResponse,
    tags=["Poll suggestions"], summary="Dismiss a poll suggestion",
    description="Marks a pending poll suggestion dismissed for the authenticated broadcaster without creating or launching a challenge.",
    responses={404: {"description": "Suggestion not found for this broadcaster."},
               409: {"description": "Suggestion is no longer pending."}},
)
def dismiss_poll_suggestion(
    suggestion_id: int = Path(ge=1), db: Session = Depends(get_db),
    broadcaster_id: str = Depends(require_streamer),
) -> dict[str, object]:
    suggestion = _scoped_poll_suggestion(db, suggestion_id, broadcaster_id)
    if suggestion.status != "suggested":
        raise HTTPException(status_code=409, detail="Poll suggestion is no longer pending")
    suggestion.status = "dismissed"
    db.commit()
    db.refresh(suggestion)
    return _poll_suggestion_payload(db, suggestion)


@app.post(
    "/api/challenges", status_code=201, response_model=ChallengeResponse, tags=["Challenges"],
    summary="Create a draft challenge",
    description="Persists the supplied proposal in the current broadcaster session. New challenges are unapproved drafts and cannot be launched until approved.",
    responses={201: {"description": "Draft challenge created."}, 401: {"description": "Broadcaster session is missing or expired."}},
)
def post_challenge(
    proposal: ChallengeProposal, db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)
) -> dict[str, object]:
    return challenge_payload(db, create_challenge(db, proposal, broadcaster_id))


@app.post(
    "/api/challenges/{challenge_id}/approve", response_model=ChallengeResponse, tags=["Challenges"],
    summary="Approve a draft challenge", description="Explicit streamer approval required before launch.",
    responses={404: {"description": "Draft not found for this broadcaster."}},
)
def approve_challenge(
    challenge_id: int = Path(ge=1, description="Challenge ID returned by the create or list endpoint."),
    db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)
) -> dict[str, object]:
    challenge = loaded_challenge(db, challenge_id)
    if not challenge or challenge.status != "draft":
        raise HTTPException(status_code=404, detail="Draft challenge not found")
    owner = db.get(StreamSession, challenge.session_id)
    if broadcaster_id != "demo" and (owner is None or owner.broadcaster_id != broadcaster_id):
        raise HTTPException(status_code=404, detail="Draft challenge not found")
    challenge.approved = True
    db.commit()
    return challenge_payload(db, loaded_challenge(db, challenge_id))


@app.post(
    "/api/challenges/{challenge_id}/launch", response_model=ChallengeResponse, tags=["Challenges"],
    summary="Launch an approved challenge", description="Starts its timer and publishes the state to the protected overlay.",
    responses={404: {"description": "Challenge not found for this broadcaster."}, 409: {"description": "Approval required, challenge is not a draft, or another challenge is active."}},
)
async def launch_challenge(
    challenge_id: int = Path(ge=1, description="Approved draft challenge ID."),
    db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)
) -> dict[str, object]:
    def launch_in_db():
        challenge = loaded_challenge(db, challenge_id)
        if not challenge:
            raise HTTPException(status_code=404, detail="Challenge not found")
        owner = db.get(StreamSession, challenge.session_id)
        if broadcaster_id != "demo" and (owner is None or owner.broadcaster_id != broadcaster_id):
            raise HTTPException(status_code=404, detail="Challenge not found")
        if not challenge.approved:
            raise HTTPException(status_code=409, detail="Challenge approval is required")
        if challenge.status != "draft":
            raise HTTPException(status_code=409, detail="Challenge is not a draft")
        if db.scalar(select(Challenge.id).where(Challenge.status == "active")):
            raise HTTPException(status_code=409, detail="Another challenge is active")
        now = datetime.now(UTC)
        challenge.status = "active"
        challenge.started_at = now
        challenge.ends_at = now + timedelta(seconds=challenge.duration_seconds)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="Another challenge is active") from exc
        active = loaded_challenge(db, challenge_id)
        session = db.get(StreamSession, active.session_id)
        return challenge_payload(db, active), session.overlay_token_hash if session else None

    payload, overlay_hash = await run_in_threadpool(launch_in_db)
    if overlay_hash:
        await overlay_broadcast(overlay_hash, {"type": "challenge.started", "challenge": payload})
    return payload


@app.post(
    "/api/challenges/{challenge_id}/end", response_model=ChallengeResponse, tags=["Challenges"],
    summary="End an active challenge", description="Closes the challenge once, calculates the result, updates the overlay, and announces the result in live Twitch chat.",
    responses={404: {"description": "Challenge not found for this broadcaster."}},
)
async def end_challenge(
    challenge_id: int = Path(ge=1, description="Challenge ID to close."),
    db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)
) -> dict[str, object]:
    def end_in_db():
        challenge = loaded_challenge(db, challenge_id)
        if not challenge:
            raise HTTPException(status_code=404, detail="Challenge not found")
        session = db.get(StreamSession, challenge.session_id)
        if broadcaster_id != "demo" and (session is None or session.broadcaster_id != broadcaster_id):
            raise HTTPException(status_code=404, detail="Challenge not found")
        did_close = close_challenge(db, challenge)
        ended = loaded_challenge(db, challenge_id)
        return (
            challenge_payload(db, ended),
            session.overlay_token_hash if session else None,
            session.broadcaster_id if did_close and session and session.mode == "live" else None,
            challenge_result_message(ended) if did_close and session and session.mode == "live" else None,
        )

    payload, overlay_hash, live_broadcaster_id, announcement = await run_in_threadpool(end_in_db)
    if overlay_hash:
        await overlay_broadcast(overlay_hash, {"type": "challenge.completed", "challenge": payload})
    if live_broadcaster_id and announcement:
        await send_chat_message(TwitchCredential(broadcaster_id=live_broadcaster_id), announcement)
    return payload


@app.post(
    "/api/challenges/{challenge_id}/votes", response_model=ChallengeResponse, tags=["Challenges"],
    summary="Cast or change a demo vote", description="Demo-only helper. Live votes are accepted only through Twitch EventSub `!vote N` messages.",
    responses={403: {"description": "Direct API voting is disabled in live mode."}, 409: {"description": "Vote was not accepted."}},
)
async def vote(
    challenge_id: int = Path(ge=1, description="Active demo challenge ID."),
    payload: VoteRequest = ...,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if not settings.demo_mode:
        raise HTTPException(status_code=403, detail="Live votes are accepted through Twitch EventSub")
    def vote_in_db():
        result = cast_vote(db, challenge_id=challenge_id, twitch_user_id=payload.twitch_user_id,
                           option_number=payload.option_number, event_id=payload.event_id)
        if result is None:
            raise HTTPException(status_code=409, detail="Vote was not accepted")
        challenge = loaded_challenge(db, challenge_id)
        session = db.get(StreamSession, challenge.session_id) if challenge else None
        return result, session.overlay_token_hash if session else None

    result, overlay_hash = await run_in_threadpool(vote_in_db)
    if overlay_hash:
        await overlay_broadcast(overlay_hash, {"type": "vote.updated", "challenge": result})
    return result


@app.post(
    "/api/overlay/token", response_model=OverlayTokenResponse, tags=["Overlay"],
    summary="Create a private overlay URL",
    description="Creates a random bearer token for the current session, stores only its hash, and revokes the prior token. Treat the response URL as a secret.",
    responses={409: {"description": "Start a session before creating an overlay token."}},
)
async def create_overlay_token(
    db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)
) -> dict[str, str]:
    token = secrets.token_urlsafe(32)

    def rotate_token_in_db():
        session = db.scalar(select(StreamSession).where(
            StreamSession.broadcaster_id == broadcaster_id, StreamSession.ended_at.is_(None)
        ).order_by(StreamSession.id.desc()))
        if session is None:
            raise HTTPException(status_code=409, detail="Start a session before setting up the overlay")
        prior_hash = session.overlay_token_hash
        session.overlay_token_hash = _hash_token(token)
        db.commit()
        return prior_hash

    prior_hash = await run_in_threadpool(rotate_token_in_db)
    if prior_hash:
        await overlay_revoke(prior_hash)
    return {"token": token, "url": f"{settings.frontend_url.rstrip('/')}/overlay?token={token}"}


@app.websocket("/ws/dashboard")
async def dashboard_websocket(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    if origin not in settings.cors_origin_list:
        await websocket.close(code=1008)
        return
    session_token = websocket.cookies.get("stream_pilot_session")
    def resolve_broadcaster() -> str | None:
        if settings.demo_mode:
            return "demo"
        if not session_token:
            return None
        db = SessionLocal()
        try:
            credential = db.scalar(select(TwitchCredential).where(
                TwitchCredential.auth_session_hash == _hash_token(session_token),
                TwitchCredential.auth_session_expires_at > datetime.now(UTC),
            ))
            return credential.broadcaster_id if credential else None
        finally:
            db.close()

    broadcaster_id = await run_in_threadpool(resolve_broadcaster)
    if not broadcaster_id:
        await websocket.close(code=1008)
        return
    await dashboard_connect(broadcaster_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        dashboard_disconnect(broadcaster_id, websocket)


@app.websocket("/ws/overlay")
async def overlay_websocket(websocket: WebSocket) -> None:
    protocols = websocket.scope.get("subprotocols", [])
    token = protocols[1] if len(protocols) >= 2 and protocols[0] == "streampilot-overlay" else ""
    if not token:
        await websocket.close(code=1008)
        return
    token_hash = _hash_token(token)
    def load_overlay_state():
        db = next(get_db())
        try:
            session = db.scalar(select(StreamSession).where(StreamSession.overlay_token_hash == token_hash))
            if not session:
                return None
            active = db.scalar(select(Challenge).options(
                selectinload(Challenge.options), selectinload(Challenge.votes)
            ).where(Challenge.session_id == session.id, Challenge.status.in_("active", "completed"))
             .order_by(Challenge.id.desc()).limit(1))
            if active:
                event = {
                    "type": "challenge.started" if active.status == "active" else "challenge.completed",
                    "challenge": challenge_payload(db, active),
                }
            else:
                event = {"type": "connection.status", "status": "connected", "challenge": None}
            return event
        finally:
            db.close()

    initial_event = await run_in_threadpool(load_overlay_state)
    if initial_event is None:
        await websocket.close(code=1008)
        return
    try:
        await overlay_connect(token_hash, websocket)
        await websocket.send_json(initial_event)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        overlay_disconnect(token_hash, websocket)


@app.websocket("/ws/overlay/question")
async def question_overlay_websocket(websocket: WebSocket) -> None:
    protocols = websocket.scope.get("subprotocols", [])
    token = protocols[1] if len(protocols) >= 2 and protocols[0] == "streampilot-overlay" else ""
    if not token:
        await websocket.close(code=1008)
        return
    token_hash = _hash_token(token)

    def load_question():
        db = SessionLocal()
        try:
            session = db.scalar(select(StreamSession).where(StreamSession.overlay_token_hash == token_hash))
            if session is None:
                return None
            group = db.scalar(select(InteractionGroup).where(
                InteractionGroup.id == session.featured_interaction_group_id,
                InteractionGroup.broadcaster_id == session.broadcaster_id,
                InteractionGroup.status == "open",
            )) if session.featured_interaction_group_id is not None else None
            question = ({"canonical_text": group.canonical_text, "kind": group.kind,
                         "asker_count": group.asker_count} if group else None)
            return {"question": question}
        finally:
            db.close()

    state = await run_in_threadpool(load_question)
    if state is None:
        await websocket.close(code=1008)
        return
    question = state["question"]
    await question_overlay_connect(token_hash, websocket)
    try:
        await websocket.send_json({"type": "question.featured", "question": question} if question else {"type": "question.hidden"})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        question_overlay_disconnect(token_hash, websocket)
