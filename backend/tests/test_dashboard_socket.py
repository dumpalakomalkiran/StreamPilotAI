import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.services import dashboard_socket


class FakeSocket:
    def __init__(self):
        self.messages = []

    async def send_text(self, message):
        self.messages.append(message)


@pytest.mark.asyncio
async def test_dashboard_broadcast_is_scoped_to_broadcaster():
    first = FakeSocket()
    other = FakeSocket()
    dashboard_socket._clients.clear()
    dashboard_socket._clients["channel-a"] = {first}
    dashboard_socket._clients["channel-b"] = {other}
    await dashboard_socket.broadcast("channel-a", {"type": "session.started"})
    assert json.loads(first.messages[0]) == {"type": "session.started"}
    assert other.messages == []
    dashboard_socket._clients.clear()


def test_dashboard_websocket_rejects_missing_live_auth(monkeypatch):
    from app import main

    monkeypatch.setattr(main.settings, "demo_mode", False)
    with TestClient(app) as client, pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/dashboard"):
        pass
