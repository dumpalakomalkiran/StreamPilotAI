"""Authorize a synthetic Twitch viewer for chat-only live tests.

Twitch developer console callback URL: http://localhost:8765/callback
Requested scope: user:write:chat only. Tokens are stored locally in an ignored file.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
CALLBACK_URI = "http://localhost:8765/callback"
TOKENS_PATH = Path(os.environ.get("STREAM_PILOT_TEST_VIEWER_TOKENS", ROOT / "data/live_test_viewer_tokens.json"))
REQUIRED_SCOPE = "user:write:chat"
sys.path.insert(0, str(BACKEND))
load_dotenv(BACKEND / ".env", override=False)


def _settings() -> tuple[str, str]:
    from app.config import get_settings

    settings = get_settings()
    if not settings.twitch_client_id or not settings.twitch_client_secret:
        raise SystemExit("Twitch app credentials are not configured in the backend environment.")
    return settings.twitch_client_id, settings.twitch_client_secret


def _callback(state: str, timeout: int) -> dict[str, str]:
    result: dict[str, str] = {}
    received = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = parse_qs(urlparse(self.path).query)
            if not secrets.compare_digest(query.get("state", [""])[0], state):
                result["error"] = "OAuth state mismatch"
            elif "error" in query:
                result["error"] = "Twitch authorization was declined"
            else:
                result["code"] = query.get("code", [""])[0]
            body = b"Authorization received. You can close this tab and return to the terminal."
            self.send_response(200 if "code" in result else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            received.set()

        def log_message(self, _format, *_args):
            return

    try:
        server = HTTPServer(("localhost", 8765), Handler)
    except OSError as exc:
        raise SystemExit("Local callback port 8765 is busy; stop its other listener and retry.") from exc
    server.timeout = 1

    def serve():
        deadline = time.monotonic() + timeout
        while not received.is_set() and time.monotonic() < deadline:
            server.handle_request()

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    print(f"Waiting up to {timeout}s for Twitch authorization at {CALLBACK_URI}.")
    if not received.wait(timeout):
        server.server_close()
        raise SystemExit("Authorization timed out; no token was saved.")
    worker.join(timeout=2)
    server.server_close()
    if "error" in result:
        raise SystemExit(result["error"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alias", required=True, help="Local synthetic account alias, e.g. viewer_a")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if not args.alias.replace("_", "").isalnum():
        parser.error("--alias may contain letters, digits, and underscores only")
    client_id, client_secret = _settings()
    state = secrets.token_urlsafe(32)
    url = "https://id.twitch.tv/oauth2/authorize?" + urlencode({
        "client_id": client_id,
        "redirect_uri": CALLBACK_URI,
        "response_type": "code",
        "scope": REQUIRED_SCOPE,
        "state": state,
        "force_verify": "true",
    })
    print(f"Authorize the synthetic Twitch account for alias {args.alias!r}; requested scope is {REQUIRED_SCOPE} only.")
    if not webbrowser.open(url):
        print("Open this URL in a browser on this machine:")
        print(url)
    callback = _callback(state, max(30, min(args.timeout, 900)))
    try:
        response = httpx.post("https://id.twitch.tv/oauth2/token", data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": callback["code"],
            "grant_type": "authorization_code",
            "redirect_uri": CALLBACK_URI,
        }, timeout=15)
        response.raise_for_status()
        token = response.json()
        validate = httpx.get("https://id.twitch.tv/oauth2/validate",
                             headers={"Authorization": f"OAuth {token['access_token']}"}, timeout=10)
        validate.raise_for_status()
        identity = validate.json()
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise SystemExit(f"Twitch token exchange or validation failed ({type(exc).__name__}); no token was saved.") from exc
    scopes = set(identity.get("scopes", token.get("scope", [])))
    if scopes != {REQUIRED_SCOPE}:
        raise SystemExit("Twitch did not grant exactly user:write:chat; no token was saved. Revoke or re-authorize with only the requested scope.")
    stored = {}
    if TOKENS_PATH.exists():
        try:
            stored = json.loads(TOKENS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit("Existing viewer-token file is unreadable; no token was saved.") from exc
    stored[args.alias] = {
        "access_token": token["access_token"],
        "refresh_token": token["refresh_token"],
        "user_id": str(identity["user_id"]),
        "login": str(identity.get("login", "")),
        "client_id": str(identity.get("client_id", "")),
        "scopes": sorted(scopes),
        "expires_in": int(token.get("expires_in", 0)),
        "saved_at": int(time.time()),
    }
    TOKENS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKENS_PATH.write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(TOKENS_PATH, 0o600)
    except OSError:
        pass
    print(f"Authorized {args.alias!r} (login {identity.get('login', 'unknown')}); token saved to the gitignored local file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
