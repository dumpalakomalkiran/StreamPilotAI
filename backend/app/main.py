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

from app.config import get_settings
from app.database import Base, engine, get_db, upgrade_schema
from app.models import Challenge, ChatMessage, StreamSession, TwitchCredential
from app.schemas import ChallengeProposal, HealthResponse
from app.services.challenge_clock import run_challenge_clock
from app.services.challenges import generate_challenge
from app.services.eventsub import run_eventsub
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
app = FastAPI(title="StreamPilot AI API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
)

class SuggestRequest(BaseModel):
    chat_messages: list[Annotated[str, Field(max_length=500)]] = Field(default_factory=list, max_length=50)


class VoteRequest(BaseModel):
    twitch_user_id: str = Field(min_length=1, max_length=80)
    option_number: int = Field(ge=1, le=4)
    event_id: str | None = Field(default=None, max_length=128)


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

@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", app="StreamPilot AI", mode="demo" if settings.demo_mode else "live")

@app.get("/api/status")
async def status(db: Session = Depends(get_db)) -> dict[str, object]:
    return {
        "demo_mode": settings.demo_mode,
        "gemini_configured": bool(settings.gemini_api_key and settings.gemini_model),
        "twitch_configured": bool(settings.twitch_client_id and settings.twitch_client_secret),
        "twitch_authorized": db.scalar(select(TwitchCredential.broadcaster_id).where(
            TwitchCredential.auth_session_hash.is_not(None),
            TwitchCredential.auth_session_expires_at > datetime.now(UTC),
        ).limit(1)) is not None,
        "integrations_verified": False,
        "message": "Live service integrations remain unverified until authorized and tested against Twitch and Gemini."
    }


@asynccontextmanager
async def lifespan(application: FastAPI):
    app.state.challenge_clock_task = asyncio.create_task(run_challenge_clock())
    if not settings.demo_mode:
        db = next(get_db())
        try:
            credential = db.scalar(select(TwitchCredential).where(
                TwitchCredential.auth_session_hash.is_not(None),
                TwitchCredential.auth_session_expires_at > datetime.now(UTC),
            ).limit(1))
            if credential:
                app.state.eventsub_task = asyncio.create_task(run_eventsub(credential.broadcaster_id))
        finally:
            db.close()
    try:
        yield
    finally:
        for task_name in ("eventsub_task", "challenge_clock_task"):
            task = getattr(app.state, task_name, None)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass


app.router.lifespan_context = lifespan

@app.post("/api/challenges/suggest", response_model=ChallengeProposal)
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


@app.get("/auth/twitch")
async def twitch_authorize() -> RedirectResponse:
    url, state = create_authorization_url()
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        "twitch_oauth_state", state, max_age=600, httponly=True,
        secure=settings.app_env == "production", samesite="lax", path="/auth/twitch/callback",
    )
    return response


@app.get("/auth/twitch/callback")
async def twitch_callback(
    request: Request,
    code: str | None = None, state: str | None = None, error: str | None = None,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if error:
        raise HTTPException(status_code=400, detail="Twitch authorization was not granted")
    if not code or not state:
        raise HTTPException(status_code=400, detail="OAuth callback is incomplete")
    cookie_state = request.cookies.get("twitch_oauth_state")
    user = await exchange_callback(code, state, cookie_state, db)
    session_token = secrets.token_urlsafe(32)
    credential = db.get(TwitchCredential, user["broadcaster_id"])
    credential.auth_session_hash = _hash_token(session_token)
    credential.auth_session_expires_at = datetime.now(UTC) + timedelta(days=7)
    db.commit()
    redirect = RedirectResponse(settings.frontend_url.rstrip("/"), status_code=303)
    redirect.delete_cookie("twitch_oauth_state", path="/auth/twitch/callback")
    redirect.set_cookie(
        "stream_pilot_session", session_token, max_age=604800, httponly=True,
        secure=settings.app_env == "production", samesite="strict", path="/",
    )
    session = db.scalar(select(StreamSession).where(
        StreamSession.broadcaster_id == user["broadcaster_id"], StreamSession.ended_at.is_(None)
    ).order_by(StreamSession.id.desc()))
    if session is None:
        session = StreamSession(broadcaster_id=user["broadcaster_id"], mode="live")
        db.add(session)
        db.commit()
    task = getattr(app.state, "eventsub_task", None)
    if task is None or task.done():
        app.state.eventsub_task = asyncio.create_task(run_eventsub(user["broadcaster_id"]))
    return redirect


@app.get("/api/challenges")
def list_challenges(db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)) -> list[dict[str, object]]:
    statement = select(Challenge).join(StreamSession).options(
        selectinload(Challenge.options), selectinload(Challenge.votes)
    ).order_by(Challenge.id.desc()).limit(100)
    if broadcaster_id != "demo":
        statement = statement.where(StreamSession.broadcaster_id == broadcaster_id)
    challenges = db.scalars(statement).all()
    return [challenge_payload(db, challenge) for challenge in challenges]


@app.get("/api/chat/messages")
def list_chat_messages(
    limit: int = 50, db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)
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


@app.post("/api/challenges", status_code=201)
def post_challenge(
    proposal: ChallengeProposal, db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)
) -> dict[str, object]:
    return challenge_payload(db, create_challenge(db, proposal, broadcaster_id))


@app.post("/api/challenges/{challenge_id}/approve")
def approve_challenge(
    challenge_id: int, db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)
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


@app.post("/api/challenges/{challenge_id}/launch")
async def launch_challenge(
    challenge_id: int, db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)
) -> dict[str, object]:
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
    if session and session.overlay_token_hash:
        await overlay_broadcast(session.overlay_token_hash, {"type": "challenge.started", "challenge": challenge_payload(db, active)})
    return challenge_payload(db, active)


@app.post("/api/challenges/{challenge_id}/end")
async def end_challenge(
    challenge_id: int, db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)
) -> dict[str, object]:
    challenge = loaded_challenge(db, challenge_id)
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    session = db.get(StreamSession, challenge.session_id)
    if broadcaster_id != "demo" and (session is None or session.broadcaster_id != broadcaster_id):
        raise HTTPException(status_code=404, detail="Challenge not found")
    did_close = close_challenge(db, challenge)
    ended = loaded_challenge(db, challenge_id)
    if session and session.overlay_token_hash:
        await overlay_broadcast(session.overlay_token_hash, {"type": "challenge.completed", "challenge": challenge_payload(db, ended)})
    if did_close and session and session.mode == "live":
        credential = db.get(TwitchCredential, session.broadcaster_id)
        if credential:
            await send_chat_message(credential, challenge_result_message(ended), db=db)
    return challenge_payload(db, ended)


@app.post("/api/challenges/{challenge_id}/votes")
async def vote(challenge_id: int, payload: VoteRequest, db: Session = Depends(get_db)) -> dict[str, object]:
    if not settings.demo_mode:
        raise HTTPException(status_code=403, detail="Live votes are accepted through Twitch EventSub")
    result = cast_vote(db, challenge_id=challenge_id, twitch_user_id=payload.twitch_user_id,
                       option_number=payload.option_number, event_id=payload.event_id)
    if result is None:
        raise HTTPException(status_code=409, detail="Vote was not accepted")
    challenge = loaded_challenge(db, challenge_id)
    session = db.get(StreamSession, challenge.session_id) if challenge else None
    if session and session.overlay_token_hash:
        await overlay_broadcast(session.overlay_token_hash, {"type": "vote.updated", "challenge": result})
    return result


@app.post("/api/overlay/token")
async def create_overlay_token(
    db: Session = Depends(get_db), broadcaster_id: str = Depends(require_streamer)
) -> dict[str, str]:
    session = db.scalar(select(StreamSession).where(
        StreamSession.broadcaster_id == broadcaster_id, StreamSession.ended_at.is_(None)
    ).order_by(StreamSession.id.desc()))
    if session is None:
        raise HTTPException(status_code=409, detail="Start a session before setting up the overlay")
    prior_hash = session.overlay_token_hash
    token = secrets.token_urlsafe(32)
    session.overlay_token_hash = _hash_token(token)
    db.commit()
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
    db = next(get_db())
    try:
        session = db.scalar(select(StreamSession).where(StreamSession.overlay_token_hash == token_hash))
        if not session:
            await websocket.close(code=1008)
            return
        await overlay_connect(token_hash, websocket)
        active = db.scalar(select(Challenge).options(
            selectinload(Challenge.options), selectinload(Challenge.votes)
        ).where(Challenge.session_id == session.id, Challenge.status.in_(["active", "completed"])).order_by(Challenge.id.desc()).limit(1))
        if active and active.status == "active":
            await websocket.send_json({"type": "challenge.started", "challenge": challenge_payload(db, active)})
        elif active:
            await websocket.send_json({"type": "challenge.completed", "challenge": challenge_payload(db, active)})
        else:
            await websocket.send_json({"type": "connection.status", "status": "connected", "challenge": None})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        overlay_disconnect(token_hash, websocket)
        db.close()
