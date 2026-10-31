import asyncio
import hashlib
import secrets
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import (
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
from app.database import Base, engine, get_db, upgrade_schema
from app.models import (
    Challenge,
    ChatMessage,
    Interaction,
    InteractionGroup,
    StreamSession,
    TwitchCredential,
)
from app.schemas import (
    ChallengeProposal,
    ChallengeResponse,
    ChatMessageResponse,
    HealthResponse,
    InteractionDetailResponse,
    InteractionGroupResponse,
    OverlayTokenResponse,
    SnoozeInteractionRequest,
    StatusResponse,
)
from app.services.challenge_clock import run_challenge_clock
from app.services.challenges import generate_challenge
from app.services.eventsub import run_eventsub
from app.services.interactions import refresh_snoozed_groups, run_grouping_worker, score_group
from app.services.overlay import broadcast as overlay_broadcast
from app.services.overlay import connect as overlay_connect
from app.services.overlay import disconnect as overlay_disconnect
from app.services.overlay import revoke as overlay_revoke
from app.services.twitch_auth import create_authorization_url, exchange_callback
from app.services.twitch_chat import send_chat_message
from app.services.voting import (
    cast_vote,
    challenge_payload,
    challenge_result_message,
    close_challenge,
    create_challenge,
    loaded_challenge,
)

settings = get_settings()
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
        {"name": "Overlay", "description": "Create a private browser overlay credential."},
    ],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
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
    option_number: int = Field(ge=1, le=4, description="One-based option position.")
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
async def twitch_authorize() -> RedirectResponse:
    url, state = create_authorization_url()
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
    return _interaction_payload(db, group, include_messages=True)


@app.post(
    "/api/interactions/{interaction_id}/answer", response_model=InteractionDetailResponse, tags=["Interactions"],
    summary="Mark an interaction answered", description="Marks an open interaction as answered and records the time. Only the owning broadcaster can update it.", responses={404: {"description": "Interaction not found for this broadcaster."}, 409: {"description": "Interaction is not open."}},
)
def answer_interaction(interaction_id: int = Path(ge=1), db: Session = Depends(get_db),
                       broadcaster_id: str = Depends(require_streamer)) -> dict[str, object]:
    return _transition_interaction(db, interaction_id, broadcaster_id, "answer")


@app.post(
    "/api/interactions/{interaction_id}/dismiss", response_model=InteractionDetailResponse, tags=["Interactions"],
    summary="Dismiss an interaction", description="Dismisses an open interaction for this broadcaster; enough distinct new askers can reopen it later.", responses={404: {"description": "Interaction not found for this broadcaster."}, 409: {"description": "Interaction is not open."}},
)
def dismiss_interaction(interaction_id: int = Path(ge=1), db: Session = Depends(get_db),
                        broadcaster_id: str = Depends(require_streamer)) -> dict[str, object]:
    return _transition_interaction(db, interaction_id, broadcaster_id, "dismiss")


@app.post(
    "/api/interactions/{interaction_id}/snooze", response_model=InteractionDetailResponse, tags=["Interactions"],
    summary="Snooze an interaction", description="Snoozes an open interaction for the requested number of minutes (1–1440); it reopens automatically at expiry.", responses={404: {"description": "Interaction not found for this broadcaster."}, 409: {"description": "Interaction is not open."}},
)
def snooze_interaction(payload: SnoozeInteractionRequest, interaction_id: int = Path(ge=1),
                       db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)) -> dict[str, object]:
    return _transition_interaction(db, interaction_id, broadcaster_id, "snooze", payload.minutes)


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
