"""Broadcaster-scoped dashboard WebSocket connections and safe event publishing."""

import json

from fastapi import WebSocket, WebSocketDisconnect

from app.models import InteractionGroup

_clients: dict[str, set[WebSocket]] = {}


async def connect(broadcaster_id: str, websocket: WebSocket) -> None:
    await websocket.accept()
    _clients.setdefault(broadcaster_id, set()).add(websocket)


def disconnect(broadcaster_id: str, websocket: WebSocket) -> None:
    clients = _clients.get(broadcaster_id)
    if clients:
        clients.discard(websocket)
        if not clients:
            _clients.pop(broadcaster_id, None)


async def broadcast(broadcaster_id: str, event: dict[str, object]) -> None:
    clients = list(_clients.get(broadcaster_id, set()))
    for websocket in clients:
        try:
            await websocket.send_text(json.dumps(event, default=str))
        except (OSError, RuntimeError, WebSocketDisconnect):
            disconnect(broadcaster_id, websocket)


def interaction_payload(group: InteractionGroup) -> dict[str, object]:
    """Safe public dashboard projection; excludes viewer identities and member text."""
    return {
        "id": group.id, "kind": group.kind, "canonical_text": group.canonical_text,
        "summary": group.summary, "status": group.status,
        "snoozed_until": group.snoozed_until, "priority_score": group.priority_score,
        "priority_components": {
            "askers": group.priority_asker_component,
            "recency": group.priority_recency_component,
            "repetition": group.priority_repetition_component,
        },
        "asker_count": group.asker_count, "message_count": group.message_count,
        "first_seen_at": group.first_seen_at, "last_seen_at": group.last_seen_at,
        "answered_at": group.answered_at,
    }
