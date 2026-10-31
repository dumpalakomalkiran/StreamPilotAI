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

## Smart Interaction Queue
All queue routes use the same authenticated broadcaster session as challenge management. Data queries constrain both the row's `broadcaster_id` and its owning `StreamSession`. List order is deterministic priority descending (distinct askers, recency, repeated messages); `priority_components` explains the score. Unmatched candidates are batched for Gemini, with deterministic fallback if Gemini is unavailable. Deleted/cleared Twitch messages are removed from the queue too.

- `GET /api/interactions?status=open&kind=question&q=gpu`: list up to 200 groups; filters are optional. `status` is `open|answered|snoozed|dismissed`; `kind` is `question|request|suggestion|other`.
- `GET /api/interactions/{id}`: one group plus member messages.
- `POST /api/interactions/{id}/answer`: mark an open group answered.
- `POST /api/interactions/{id}/dismiss`: dismiss an open group.
- `POST /api/interactions/{id}/snooze` with `{"minutes":15}`: snooze for 1–1440 minutes. Expired snoozes reopen automatically.
- `POST /api/interactions/{id}/feature`: feature an open queue item's canonical text and viewer count on the OBS overlay. No member identities or raw messages are sent.
- `WS /ws/dashboard`: browser session cookie authentication and allowed-origin validation; events are restricted to the authenticated broadcaster: `interaction.created`, `interaction.updated`, `session.started`, `session.ended`. Reconnect by re-fetching `GET /api/interactions`; there is no event replay.
- The existing `WS /ws/overlay` endpoint and its challenge event contract are unchanged. Optional question display uses a separate `WS /ws/overlay/question` connection with the same token/subprotocol; reconnect sends the current `question.featured` or `question.hidden` state.

Example requests (replace the cookie placeholder with the browser's authenticated session; do not commit or share it):

```bash
curl -b 'stream_pilot_session=<SESSION_COOKIE>' 'http://localhost:8000/api/interactions?status=open&kind=question&q=gpu'
curl -b 'stream_pilot_session=<SESSION_COOKIE>' 'http://localhost:8000/api/interactions/42'
curl -X POST -b 'stream_pilot_session=<SESSION_COOKIE>' 'http://localhost:8000/api/interactions/42/answer'
curl -X POST -b 'stream_pilot_session=<SESSION_COOKIE>' 'http://localhost:8000/api/interactions/42/dismiss'
curl -X POST -b 'stream_pilot_session=<SESSION_COOKIE>' -H 'Content-Type: application/json' -d '{"minutes":15}' 'http://localhost:8000/api/interactions/42/snooze'
```

## AI reply drafts (streamer-controlled)
All endpoints are authenticated and broadcaster-scoped. `GET/PUT /api/settings/stream-facts` reads/writes up to 3000 characters of streamer-provided facts. Do not put credentials or private data there. Gemini is called only by `POST /api/interactions/{id}/response/suggest`, which considers the canonical question and up to five retained member messages. Chat is encoded as untrusted JSON, bounded, and never treated as instructions. If there are no stream facts, or Gemini cannot produce a valid structured answer grounded in the facts, the API saves a safe holding reply. Draft output is capped at 300 characters. A suggestion does not send chat.

- `POST /api/interactions/{id}/response/suggest`: create a reviewable draft.
- `PATCH /api/responses/{id}` with `{"text":"..."}`: edit an unsent draft, up to Twitch's 500-character limit.
- `POST /api/responses/{id}/discard`: discard without sending.
- `POST /api/responses/{id}/send`: the only suggestion endpoint that calls the existing Helix Send Chat Message adapter. It sends only the current reviewed text after an explicit streamer action. Success marks the draft sent, marks the interaction answered, and records broadcaster/actor, timestamp, exact sent text and Twitch message ID in the audit table. Rate limits and provider errors are returned as safe user-facing messages. No automatic send path exists.

```bash
curl -b 'stream_pilot_session=<SESSION_COOKIE>' 'http://localhost:8000/api/settings/stream-facts'
curl -X PUT -b 'stream_pilot_session=<SESSION_COOKIE>' -H 'Content-Type: application/json' -d '{"stream_facts":"I stream Tue/Thu at 7 PM. GPU: RTX 4070."}' 'http://localhost:8000/api/settings/stream-facts'
curl -X POST -b 'stream_pilot_session=<SESSION_COOKIE>' 'http://localhost:8000/api/interactions/42/response/suggest'
curl -X PATCH -b 'stream_pilot_session=<SESSION_COOKIE>' -H 'Content-Type: application/json' -d '{"text":"I use an RTX 4070!"}' 'http://localhost:8000/api/responses/1'
curl -X POST -b 'stream_pilot_session=<SESSION_COOKIE>' 'http://localhost:8000/api/responses/1/send'
curl -X POST -b 'stream_pilot_session=<SESSION_COOKIE>' 'http://localhost:8000/api/responses/1/discard'
```

Current EventSub types and scopes must be checked against current official docs before modification. Safe provider errors must never include keys, tokens, raw diagnostics or private chat.
