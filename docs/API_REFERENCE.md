# API Reference — Reviewed Source Contract

Runtime OpenAPI (`/openapi.json`) and Swagger (`/docs`) are authoritative for precise request/response schemas.

## Auth
In live mode `/api` management routes use HttpOnly `stream_pilot_session` from Twitch OAuth. Health/status/OAuth routes are public. Demo mode bypasses streamer auth and must not be treated as a production boundary.

## System and OAuth
- `GET /api/health`: liveness only, not provider connectivity.
- `GET /api/status`: safe configuration/worker flags; `integrations_verified` remains false in reviewed code.
- `GET /auth/twitch`: state cookie and Twitch redirect.
- `GET /auth/twitch/callback`: state validation, code exchange, broadcaster identity, encrypted token storage and app cookie.
- EventSub `stream.online` starts a persisted live session and `stream.offline` sets its `ended_at`; both use Twitch's broadcaster-only condition and add no OAuth scopes.

## Challenge routes
- `POST /api/challenges/suggest`: explicit Gemini request; bounded chat context; returns Gemini or labelled fallback; never approves/launches.
- `POST /api/challenges`: create draft. `GET /api/challenges`: history.
- `POST /api/challenges/{id}/approve`, `/launch`, `/end`.
- `POST /api/challenges/{id}/votes`: demo-only helper; live votes come from EventSub chat.
- Schema bounds: title 1–80; question 1–180; 2–4 options; label 1–60; description max 140; duration 15–60; reason 1–240.

## Chat and overlay
- `GET /api/chat/messages?limit=50`: max 100 response; max 500 recent rows/session. Moderation events delete stored rows; bounded tombstones prevent replay restoration. A vote already accepted is not undone by later chat deletion.
- `POST /api/overlay/token`: issue/rotate private token and OBS URL; treat as bearer secret.
- `WS /ws/overlay`: subprotocols `streampilot-overlay` and private token. Events: `connection.status`, `challenge.started`, `vote.updated`, `challenge.completed`; reconnect receives current state.

Current EventSub types and scopes must be checked against current official docs before modification. Safe provider errors must never include keys, tokens, raw diagnostics or private chat.
