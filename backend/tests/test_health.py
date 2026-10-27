from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

def test_health_returns_ok():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_status_does_not_expose_secrets():
    body = client.get("/api/status").json()
    assert "gemini_api_key" not in body
    assert "twitch_client_secret" not in body
    assert body["eventsub_worker"] in {"not_started", "running", "stopped"}

def test_suggestion_returns_labeled_fallback_without_external_call(monkeypatch):
    from app import main
    from app.services.challenges import make_fallback_challenge

    async def fallback(_messages):
        return make_fallback_challenge()

    monkeypatch.setattr(main, "generate_challenge", fallback)
    response = client.post("/api/challenges/suggest", json={"chat_messages": ["left", "right"]})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "fallback"
    assert 2 <= len(body["options"]) <= 4
    assert 15 <= body["duration_seconds"] <= 60
