import re
from datetime import UTC, datetime

from sqlalchemy import exists, literal, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, selectinload

from app.models import Challenge, ChallengeOption, ProcessedEvent, StreamSession, Vote
from app.schemas import ChallengeProposal


def challenge_payload(db: Session, challenge: Challenge) -> dict[str, object]:
    counts = {option.id: 0 for option in challenge.options}
    for vote in challenge.votes:
        counts[vote.option_id] = counts.get(vote.option_id, 0) + 1
    return {
        "id": challenge.id,
        "session_id": challenge.session_id,
        "title": challenge.title,
        "question": challenge.question,
        "reason": challenge.reason,
        "duration_seconds": challenge.duration_seconds,
        "source": challenge.source,
        "status": challenge.status,
        "approved": challenge.approved,
        "started_at": challenge.started_at,
        "ends_at": challenge.ends_at,
        "ended_at": challenge.ended_at,
        "options": [
            {"id": option.id, "label": option.label, "description": option.description,
             "position": option.position, "votes": counts.get(option.id, 0)}
            for option in sorted(challenge.options, key=lambda item: item.position)
        ],
        "total_votes": len(challenge.votes),
        "winning_option_id": challenge.winning_option_id,
    }


def challenge_result_message(challenge: Challenge) -> str:
    winner = next((option for option in challenge.options if option.id == challenge.winning_option_id), None)
    if winner is None:
        return "Audience challenge ended in a tie."
    total = sum(vote.option_id == winner.id for vote in challenge.votes)
    return f"Audience challenge result: {winner.label} won with {total} votes."


def create_challenge(db: Session, proposal: ChallengeProposal, broadcaster_id: str = "demo") -> Challenge:
    session = db.scalar(select(StreamSession).where(
        StreamSession.broadcaster_id == broadcaster_id, StreamSession.ended_at.is_(None)
    ).order_by(StreamSession.id.desc()))
    if session is None:
        session = StreamSession(broadcaster_id=broadcaster_id, mode="live" if broadcaster_id != "demo" else "demo")
        db.add(session)
        db.flush()
    challenge = Challenge(
        session_id=session.id, title=proposal.title, question=proposal.question,
        reason=proposal.reason, duration_seconds=proposal.duration_seconds, source=proposal.source,
        options=[ChallengeOption(label=o.label, description=o.description, position=i)
                 for i, o in enumerate(proposal.options, start=1)],
    )
    db.add(challenge)
    db.commit()
    db.refresh(challenge)
    return challenge


def loaded_challenge(db: Session, challenge_id: int) -> Challenge | None:
    return db.scalar(
        select(Challenge)
        .options(selectinload(Challenge.options), selectinload(Challenge.votes))
        .where(Challenge.id == challenge_id)
        .execution_options(populate_existing=True)
    )


def cast_vote(db: Session, *, challenge_id: int, twitch_user_id: str, option_number: int,
              event_id: str | None = None, now: datetime | None = None) -> dict[str, object] | None:
    now = now or datetime.now(UTC)
    challenge = loaded_challenge(db, challenge_id)
    if not challenge or challenge.status != "active" or not challenge.ends_at:
        return None
    ends_at = challenge.ends_at
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=UTC)
    if now >= ends_at:
        return None
    option = next((option for option in challenge.options if option.position == option_number), None)
    if option is None or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", twitch_user_id):
        return None
    if event_id:
        inserted = db.execute(
            sqlite_insert(ProcessedEvent).values(event_id=event_id)
            .on_conflict_do_nothing(index_elements=[ProcessedEvent.event_id])
        )
        if inserted.rowcount != 1:
            db.rollback()
            return None
    still_open = exists(select(Challenge.id).where(
        Challenge.id == challenge_id,
        Challenge.status == "active",
        Challenge.ends_at > now,
    ))
    result = db.execute(
        sqlite_insert(Vote).from_select(
            ["challenge_id", "option_id", "twitch_user_id", "updated_at"],
            select(Challenge.id, ChallengeOption.id, literal(twitch_user_id), literal(now))
            .join(ChallengeOption, ChallengeOption.challenge_id == Challenge.id)
            .where(
                Challenge.id == challenge_id,
                Challenge.status == "active",
                Challenge.ends_at > now,
                ChallengeOption.id == option.id,
            ),
        ).on_conflict_do_update(
            index_elements=[Vote.challenge_id, Vote.twitch_user_id],
            set_={"option_id": option.id, "updated_at": now},
            where=still_open,
        )
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    challenge = loaded_challenge(db, challenge_id)
    return challenge_payload(db, challenge) if challenge else None


def close_challenge(db: Session, challenge: Challenge) -> bool:
    if challenge.status != "active":
        return False
    now = datetime.now(UTC)
    result = db.execute(
        update(Challenge)
        .where(Challenge.id == challenge.id, Challenge.status == "active")
        .values(status="completed", ended_at=now)
    )
    if result.rowcount != 1:
        db.rollback()
        db.refresh(challenge)
        return False
    tallies = {option.id: 0 for option in challenge.options}
    for vote in challenge.votes:
        tallies[vote.option_id] = tallies.get(vote.option_id, 0) + 1
    winners: list[int] = []
    if tallies:
        high = max(tallies.values())
        winners = [option_id for option_id, count in tallies.items() if count == high and high > 0]
    challenge.winning_option_id = winners[0] if len(winners) == 1 else None
    db.commit()
    db.refresh(challenge)
    return True
