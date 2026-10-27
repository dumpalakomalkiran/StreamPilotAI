import json

from fastapi import WebSocket, WebSocketDisconnect

_clients: dict[str, set[WebSocket]] = {}


async def connect(token_hash: str, websocket: WebSocket) -> None:
    await websocket.accept(subprotocol="streampilot-overlay")
    _clients.setdefault(token_hash, set()).add(websocket)


def disconnect(token_hash: str, websocket: WebSocket) -> None:
    clients = _clients.get(token_hash)
    if clients:
        clients.discard(websocket)
        if not clients:
            _clients.pop(token_hash, None)


async def broadcast(token_hash: str, event: dict[str, object]) -> None:
    clients = list(_clients.get(token_hash, set()))
    for websocket in clients:
        try:
            await websocket.send_text(json.dumps(event, default=str))
        except (OSError, RuntimeError, WebSocketDisconnect):
            disconnect(token_hash, websocket)


async def revoke(token_hash: str) -> None:
    for websocket in list(_clients.get(token_hash, set())):
        try:
            await websocket.close(code=1008)
        except (OSError, RuntimeError, WebSocketDisconnect):
            pass
    _clients.pop(token_hash, None)


