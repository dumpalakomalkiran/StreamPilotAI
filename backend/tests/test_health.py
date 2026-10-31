from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

def test_health_returns_ok():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_live_mode_is_the_default():
    from app.config import Settings

    assert Settings(_env_file=None).demo_mode is False
    assert Settings(_env_file=None).gemini_model == "gemini-3.5-flash-lite"
    assert Settings(_env_file=None, gemini_model="").gemini_model == "gemini-3.5-flash-lite"

def test_management_endpoints_require_authentication_in_live_mode(monkeypatch):
    from app import main

    monkeypatch.setattr(main.settings, "demo_mode", False)
    assert client.get("/api/challenges").status_code == 401
    assert client.post("/api/challenges/suggest", json={"chat_messages": []}).status_code == 401
    assert client.post("/api/overlay/token").status_code == 401

def test_status_does_not_expose_secrets():
    body = client.get("/api/status").json()
    assert "gemini_api_key" not in body
    assert "twitch_client_secret" not in body
    assert body["eventsub_worker"] in {"not_started", "running", "stopped"}

def test_status_does_not_report_saved_twitch_session_as_live_in_demo(monkeypatch):
    from app import main

    monkeypatch.setattr(main.settings, "demo_mode", True)
    body = client.get("/api/status").json()
    assert body["demo_mode"] is True
    assert body["twitch_authorized"] is False

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


def test_swagger_documents_api_details_and_examples():
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    suggest = paths["/api/challenges/suggest"]["post"]
    assert suggest["summary"] == "Generate a challenge suggestion"
    assert "never approves or launches" in suggest["description"]
    suggest_request_schema = suggest["requestBody"]["content"]["application/json"]["schema"]
    suggest_model = schema["components"]["schemas"][suggest_request_schema["$ref"].split("/")[-1]]
    assert suggest_model["examples"][0]["chat_messages"]

    chat = paths["/api/chat/messages"]["get"]
    assert "remove matching rows" in chat["description"]
    chat_schema_ref = chat["responses"]["200"]["content"]["application/json"]["schema"]["items"]["$ref"]
    chat_model = schema["components"]["schemas"][chat_schema_ref.split("/")[-1]]
    assert chat_model["examples"][0]["text"] == "Take the left path!"

    documented_api_methods = [
        operation
        for path, methods in paths.items() if path.startswith("/api/")
        for operation in methods.values() if isinstance(operation, dict) and "operationId" in operation
    ]
    assert all(operation.get("summary") and operation.get("description")
               for operation in documented_api_methods)
