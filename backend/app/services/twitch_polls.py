"""Optional Twitch Helix poll integration. Never called by the AI suggestion worker."""

import asyncio
from datetime import UTC, datetime
from weakref import WeakKeyDictionary

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    BroadcasterSettings,
    Challenge,
    NativeTwitchPoll,
    PollSuggestion,
    StreamSession,
    TwitchCredential,
)
from app.services.twitch_auth import NATIVE_POLL_SCOPE, refresh_access_token_if_needed

HELIX_POLLS = "https://api.twitch.tv/helix/polls"
_create_locks: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = WeakKeyDictionary()


def _has_scope(credential: TwitchCredential) -> bool:
    return NATIVE_POLL_SCOPE in set(credential.scopes.split())


async def _get_token(credential: TwitchCredential, db: Session) -> str:
    if not _has_scope(credential):
        raise HTTPException(
            status_code=409,
            detail="Reconnect Twitch to grant channel:manage:polls. Native poll creation is disabled until Twitch authorization is updated.",
        )
    return await refresh_access_token_if_needed(credential, db)


def _headers(token: str) -> dict[str, str]:
    settings = get_settings()
    return {"Authorization": f"Bearer {token}", "Client-Id": settings.twitch_client_id,
            "Content-Type": "application/json"}


def _map_helix_error(response: httpx.Response, operation: str) -> None:
    if response.status_code == 429:
        raise HTTPException(status_code=429, detail="Twitch is rate limiting poll requests. Wait briefly, then try again.")
    if response.status_code == 403:
        raise HTTPException(
            status_code=403,
            detail="This Twitch channel must be an Affiliate or Partner to create native polls. Your approved suggestion can use the StreamPilot !vote N fallback.",
        )
    if response.status_code == 400:
        try:
            message = str(response.json().get("message", "")).casefold()
        except (ValueError, TypeError, AttributeError):
            message = ""
        if "already has a poll" in message or "poll that's running" in message or "poll that is running" in message:
            raise HTTPException(status_code=409, detail="A Twitch poll is already active. End it before creating another.")
        raise HTTPException(status_code=422, detail=f"Twitch rejected the poll {operation} request. Check its title, options and duration.")
    if response.status_code == 401:
        raise HTTPException(status_code=409, detail="Reconnect Twitch and grant channel:manage:polls to use native polls.")
    if response.status_code >= 500:
        raise HTTPException(status_code=502, detail=f"Twitch poll {operation} is temporarily unavailable.")
    if response.is_error:
        raise HTTPException(status_code=502, detail=f"Twitch could not {operation} the poll.")


async def _remote_polls(broadcaster_id: str, token: str) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5)) as client:
            response = await client.get(HELIX_POLLS, headers=_headers(token), params={"broadcaster_id": broadcaster_id, "first": 20})
        _map_helix_error(response, "read")
        return response.json().get("data", [])
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=502, detail="Twitch poll status is temporarily unavailable.") from exc


async def create_native_poll(db: Session, broadcaster_id: str, suggestion_id: int, duration_seconds: int | None = None) -> NativeTwitchPoll:
    """Create from a reviewed/approved suggestion; explicit API invocation is the only create path."""
    loop = asyncio.get_running_loop()
    lock = _create_locks.setdefault(loop, {}).setdefault(broadcaster_id, asyncio.Lock())
    async with lock:
        return await _create_native_poll_locked(db, broadcaster_id, suggestion_id, duration_seconds)


async def _create_native_poll_locked(db: Session, broadcaster_id: str, suggestion_id: int, duration_seconds: int | None) -> NativeTwitchPoll:
    suggestion = db.scalar(select(PollSuggestion).where(
        PollSuggestion.id == suggestion_id, PollSuggestion.broadcaster_id == broadcaster_id,
    ))
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Approved poll suggestion not found.")
    if suggestion.status != "approved" or suggestion.approved_challenge_id is None:
        raise HTTPException(status_code=409, detail="Approve the poll suggestion before creating a Twitch poll.")
    if db.scalar(select(NativeTwitchPoll.id).where(
        NativeTwitchPoll.broadcaster_id == broadcaster_id,
        NativeTwitchPoll.suggestion_id == suggestion_id,
    )):
        raise HTTPException(status_code=409, detail="A Twitch poll was already created from this approved suggestion.")
    challenge = db.scalar(select(Challenge).join(StreamSession).where(
        Challenge.id == suggestion.approved_challenge_id,
        StreamSession.broadcaster_id == broadcaster_id,
        Challenge.session_id == suggestion.session_id,
    ))
    if challenge is None or challenge.status != "draft" or challenge.approved:
        raise HTTPException(status_code=409, detail="The approved suggestion's StreamPilot vote draft is no longer available.")
    if len(challenge.title) > 60 or not 2 <= len(challenge.options) <= 5 or any(len(o.label) > 25 for o in challenge.options):
        raise HTTPException(status_code=422, detail="Twitch polls require a title up to 60 characters and 2–5 options up to 25 characters each.")
    poll_duration = duration_seconds if duration_seconds is not None else challenge.duration_seconds
    if not 15 <= poll_duration <= 1800:
        raise HTTPException(status_code=422, detail="Twitch poll duration must be between 15 and 1800 seconds.")
    if db.scalar(select(Challenge.id).where(Challenge.status == "active")):
        raise HTTPException(status_code=409, detail="A StreamPilot !vote poll is already active; end it before creating a Twitch poll.")
    if db.scalar(select(NativeTwitchPoll.id).where(
        NativeTwitchPoll.broadcaster_id == broadcaster_id, NativeTwitchPoll.status == "ACTIVE",
    )):
        raise HTTPException(status_code=409, detail="A StreamPilot-tracked Twitch poll is already active.")
    row = db.get(TwitchCredential, broadcaster_id)
    if row is None:
        raise HTTPException(status_code=401, detail="Reconnect Twitch before creating a native poll.")
    setting = db.get(BroadcasterSettings, broadcaster_id)
    if setting is None or not setting.native_twitch_polls_enabled:
        raise HTTPException(status_code=409, detail="Enable Native Twitch polls in Settings first.")
    token = await _get_token(row, db)
    remote = await _remote_polls(broadcaster_id, token)
    if any(poll.get("status") == "ACTIVE" for poll in remote):
        raise HTTPException(status_code=409, detail="A Twitch poll is already active. End it before creating another.")
    body = {
        "broadcaster_id": broadcaster_id,
        "title": challenge.title,
        "choices": [{"title": option.label} for option in challenge.options],
        "duration": poll_duration,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5)) as client:
            response = await client.post(HELIX_POLLS, headers=_headers(token), json=body)
        _map_helix_error(response, "create")
        result = response.json()["data"][0]
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError) as exc:
        raise HTTPException(status_code=502, detail="Twitch poll creation failed. The approved !vote N draft is still available.") from exc
    poll = NativeTwitchPoll(
        broadcaster_id=broadcaster_id, session_id=suggestion.session_id, suggestion_id=suggestion.id,
        challenge_id=challenge.id, twitch_poll_id=str(result["id"]), title=challenge.title,
        options=[option.label for option in challenge.options], duration_seconds=poll_duration,
        status=str(result.get("status", "ACTIVE")),
    )
    db.add(poll)
    db.commit()
    db.refresh(poll)
    return poll


async def list_native_polls(db: Session, broadcaster_id: str) -> list[NativeTwitchPoll]:
    setting = db.get(BroadcasterSettings, broadcaster_id)
    row = db.get(TwitchCredential, broadcaster_id)
    if setting and setting.native_twitch_polls_enabled and row and _has_scope(row):
        token = await _get_token(row, db)
        remote = await _remote_polls(broadcaster_id, token)
        remote_by_id = {str(poll.get("id")): poll for poll in remote}
        tracked = db.scalars(select(NativeTwitchPoll).where(
            NativeTwitchPoll.broadcaster_id == broadcaster_id,
        ).order_by(NativeTwitchPoll.started_at.desc()).limit(20)).all()
        for poll in tracked:
            current = remote_by_id.get(poll.twitch_poll_id)
            if current and current.get("status"):
                poll.status = str(current["status"])
                if current.get("ended_at"):
                    try:
                        poll.ended_at = datetime.fromisoformat(str(current["ended_at"])).replace(tzinfo=None)
                    except ValueError:
                        pass
        db.commit()
    return db.scalars(select(NativeTwitchPoll).where(
        NativeTwitchPoll.broadcaster_id == broadcaster_id,
    ).order_by(NativeTwitchPoll.started_at.desc()).limit(20)).all()


async def end_native_poll(db: Session, broadcaster_id: str, poll_id: int) -> NativeTwitchPoll:
    poll = db.scalar(select(NativeTwitchPoll).where(
        NativeTwitchPoll.id == poll_id, NativeTwitchPoll.broadcaster_id == broadcaster_id,
    ))
    if poll is None:
        raise HTTPException(status_code=404, detail="Twitch poll not found for this broadcaster.")
    if poll.status != "ACTIVE":
        raise HTTPException(status_code=409, detail="This Twitch poll is no longer active.")
    row = db.get(TwitchCredential, broadcaster_id)
    if row is None:
        raise HTTPException(status_code=401, detail="Reconnect Twitch before ending a native poll.")
    token = await _get_token(row, db)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5)) as client:
            response = await client.patch(HELIX_POLLS, headers=_headers(token), json={
                "broadcaster_id": broadcaster_id, "id": poll.twitch_poll_id, "status": "TERMINATED",
            })
        _map_helix_error(response, "end")
        result = response.json().get("data", [{}])[0]
        poll.status = str(result.get("status", "TERMINATED"))
        poll.ended_at = datetime.now(UTC)
        db.commit()
        db.refresh(poll)
        return poll
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError) as exc:
        raise HTTPException(status_code=502, detail="Twitch poll could not be ended. Try again shortly.") from exc
