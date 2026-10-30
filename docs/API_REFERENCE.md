# StreamPilot AI backend API reference

FastAPI Swagger UI is available at `http://localhost:8000/docs`; the machine-readable OpenAPI document is at `http://localhost:8000/openapi.json`. Swagger includes operation descriptions, field constraints, request examples, response models, and representative responses.

## Authentication and conventions

- `/api` routes that manage streamer data require the HttpOnly `stream_pilot_session` cookie created by Twitch OAuth in live mode. The cookie is sent by the browser; do not copy it into source code or share it.
- `/api/health`, `/api/status`, and the OAuth routes are public. Status returns booleans and safe status text only.
- JSON errors use FastAPI's `{"detail":"..."}` shape. Validation errors return HTTP 422. Missing or expired broadcaster sessions return HTTP 401.
- Examples below use local URLs and fake IDs. They are illustrative and are not Twitch credentials or live account data.

## System

### `GET /api/health`

Unauthenticated liveness check. HTTP 200:

```json
{"status":"ok","app":"StreamPilot AI","mode":"live"}
```

This endpoint does not test Gemini or Twitch connectivity.

### `GET /api/status`

Unauthenticated, secret-free configuration and worker status. HTTP 200:

```json
{
  "demo_mode": false,
  "gemini_configured": true,
  "twitch_configured": true,
  "twitch_authorized": true,
  "eventsub_worker": "running",
  "integrations_verified": false,
  "message": "Live service integrations remain unverified until authorized and tested against Twitch and Gemini."
}
```

`integrations_verified` remains false until real-service checks have actually passed.

## Twitch OAuth

### `GET /auth/twitch`

Starts Twitch broadcaster authorization. The backend sets an OAuth state cookie and responds with HTTP 302 redirecting the browser to Twitch. After consent Twitch redirects to the configured callback.

### `GET /auth/twitch/callback?code=…&state=…`

Validates OAuth state, exchanges the one-time code server-to-server, encrypts stored Twitch tokens, sets the HttpOnly app session cookie, and redirects to the dashboard with HTTP 303. The callback returns HTTP 400 for denial/incomplete parameters, HTTP 401 for invalid state or expired authorization, and HTTP 502 when Twitch is temporarily unavailable.

## Challenges

### `POST /api/challenges/suggest`

Explicitly asks Gemini for a structured suggestion. Request JSON (maximum 50 messages, 500 characters each):

```json
{"chat_messages":["Take the left path!","Save the healing item for the boss."]}
```

HTTP 200 response (actual values vary):

```json
{
  "title":"Choose the next route",
  "question":"Which route should the player take?",
  "options":[
    {"label":"Left","description":"Faster, with more enemies"},
    {"label":"Right","description":"Safer, but longer"}
  ],
  "duration_seconds":30,
  "reason":"Viewers suggested two viable paths.",
  "source":"gemini"
}
```

`source` is `gemini` for a validated provider response or `fallback` when generation fails. A suggestion is only a draft; this route never approves or launches it. Responses may be HTTP 401 (not connected) or HTTP 429 (rate limit).

### `POST /api/challenges`

Creates an unapproved draft. Request body follows the same challenge proposal schema as the suggestion response. HTTP 201 returns the persisted challenge, including `id`, `session_id`, `status: "draft"`, `approved: false`, option IDs, and vote counts. Invalid challenge bounds return HTTP 422.

### `GET /api/challenges`

Returns the latest 100 challenges for the authenticated broadcaster, newest first. Each item uses the persisted challenge response schema shown in Swagger.

### `POST /api/challenges/{challenge_id}/approve`

Approves a draft owned by the current broadcaster. No request body. HTTP 200 returns the updated challenge with `approved: true`; HTTP 404 means no matching draft is available to this broadcaster.

### `POST /api/challenges/{challenge_id}/launch`

Launches an approved draft and starts its timer. No request body. HTTP 200 returns `status: "active"`, `started_at`, and `ends_at`; HTTP 409 indicates approval is missing, the challenge is not a draft, or another challenge is active.

### `POST /api/challenges/{challenge_id}/end`

Ends an active challenge. No request body. HTTP 200 returns `status: "completed"`, final vote totals, and `winning_option_id` (null for a tie or no votes). In live mode the backend also sends the result announcement through Twitch.

### `POST /api/challenges/{challenge_id}/votes`

Demo-only vote helper. Request:

```json
{"twitch_user_id":"123456789","option_number":2,"event_id":"demo-event-001"}
```

HTTP 200 returns the updated challenge totals. In live mode this route returns HTTP 403: live votes must come from Twitch chat `!vote N` EventSub messages.

## Chat

### `GET /api/chat/messages?limit=50`

Returns up to 100 recent messages from the current session (default 50):

```json
[
  {"display_name":"ViewerOne","text":"Take the left path!","received_at":"2026-10-28T10:15:30Z"}
]
```

Twitch deletion/clear notifications remove matching rows. Removed messages are therefore absent from this response and from subsequent Gemini chat context. The API retains a maximum of 500 recent messages per session.

## Overlay

### `POST /api/overlay/token`

Creates a token for the current session and revokes any previous token. HTTP 200 returns a bearer token and URL:

```json
{
  "token":"example-token-not-real",
  "url":"http://localhost:5173/overlay?token=example-token-not-real"
}
```

The shown value is fake. Treat a real returned URL as a secret and only paste it into the streamer's own OBS browser source. HTTP 409 means there is no active session.

### `WS /ws/overlay`

WebSocket operations do not appear in Swagger's HTTP OpenAPI listing. The client supplies subprotocols `streampilot-overlay` and the private token. The server sends `connection.status`, `challenge.started`, `vote.updated`, and `challenge.completed` events; reconnect receives the current challenge state.

## Twitch EventSub message moderation

The backend subscribes to `channel.chat.message`, `channel.chat.message_delete`, `channel.chat.clear_user_messages`, and `channel.chat.clear`. A delete removes the message matching Twitch's `message_id`; user clear removes that user's current-session messages; full clear removes all current-session messages. Deleted message IDs are kept as bounded replay tombstones so a duplicate message event does not restore removed text. A vote already accepted from `!vote N` remains a separate action and is not reversed when its chat message is later removed.
