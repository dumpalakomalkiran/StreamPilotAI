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
- `POST /api/challenges/{id}/votes`: demo-only helper; live votes come from EventSub chat. `!vote N` accepts positions 1–5, including polls with five choices.
- Schema bounds: title 1–80; question 1–180; 2–4 options; label 1–60; description max 140; duration 15–60; reason 1–240.

## Chat and overlay
- `GET /api/chat/messages?limit=50`: max 100 response; max 500 recent rows/session. `is_synthetic` marks development replay rows; live Twitch messages are false. Moderation events delete stored rows; bounded tombstones prevent replay restoration. A vote already accepted is not undone by later chat deletion.
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

## AI poll suggestions
The periodic batched Gemini grouping request can include zero or more audience poll drafts; no second model request is made. The backend requires at least three distinct HMAC-keyed viewers across two or more non-overlapping preferences, validates supporting message IDs against the same broadcaster/session, and uses `POLL_SUGGESTION_COOLDOWN_SECONDS` (default 300). An active challenge or pending suggestion suppresses additional polls. A suggestion never starts automatically.

- `GET /api/polls/suggestions`: pending suggestions for the authenticated broadcaster's current session, with retained supporting snippets.
- `POST /api/polls/suggestions/{id}/approve` with `{"title":"Ranked or casual?","options":["Ranked","Casual"],"duration_seconds":30}`: saves streamer edits and creates an unapproved existing Challenge draft. The dashboard then invokes the normal challenge approve and launch endpoints; the existing timer, `!vote N`, overlay, and result calculation are reused.
- `POST /api/polls/suggestions/{id}/dismiss`: dismiss without creating a challenge.
- `WS /ws/dashboard` emits `poll.suggestion.created` to the authenticated broadcaster. The client refreshes the REST list after this event or reconnect.

Options are unique labels of 1–25 characters (2–5 choices); duration is 15–60 seconds. Suggestions expire when the session ends or after 24 hours. This feature does not call Twitch native polls.

```bash
curl -b 'stream_pilot_session=<SESSION_COOKIE>' 'http://localhost:8000/api/polls/suggestions'
curl -X POST -b 'stream_pilot_session=<SESSION_COOKIE>' -H 'Content-Type: application/json' -d '{"title":"Ranked or casual?","options":["Ranked","Casual"],"duration_seconds":30}' 'http://localhost:8000/api/polls/suggestions/7/approve'
curl -X POST -b 'stream_pilot_session=<SESSION_COOKIE>' 'http://localhost:8000/api/polls/suggestions/7/dismiss'
```

Current EventSub types and scopes must be checked against current official docs before modification. Safe provider errors must never include keys, tokens, raw diagnostics or private chat.

## Community Intelligence
Analytics endpoints use the authenticated broadcaster. Current-session message counts use the app's persisted, moderation-filtered chat window (maximum 500 recent rows/session); messages/minute always returns 30 minute buckets. Average answer time is measured from a question group's first observation to its `answered_at` timestamp. Questions detected counts retained queue candidate messages. `GET /api/analytics/current` returns live counts, the top five open groups, poll-suggestion approved/dismissed totals, and open unanswered questions.

`POST /api/analytics/current/end` ends the active session and returns immediately with a pending recap status. Gemini recap work is scheduled in the background; `stream.offline` also schedules a recap. The Insights page polls the read-only summary route. `POST /api/analytics/sessions/{id}/summary` generates once and then serves the cache; send `{"regenerate":true}` only when an explicit new generation is wanted. The prompt contains aggregate stats, at most 12 top queue groups, and challenge/poll results; it never includes complete raw chat or member messages. Empty sessions get a local zero-activity recap without calling Gemini. Audience mood is plain cautious language, never a score.

```bash
curl -b 'stream_pilot_session=<SESSION_COOKIE>' 'http://localhost:8000/api/analytics/current'
curl -X POST -b 'stream_pilot_session=<SESSION_COOKIE>' 'http://localhost:8000/api/analytics/current/end'
curl -b 'stream_pilot_session=<SESSION_COOKIE>' 'http://localhost:8000/api/analytics/sessions/5/summary'
curl -X POST -b 'stream_pilot_session=<SESSION_COOKIE>' -H 'Content-Type: application/json' -d '{"regenerate":false}' 'http://localhost:8000/api/analytics/sessions/5/summary'
curl -X POST -b 'stream_pilot_session=<SESSION_COOKIE>' -H 'Content-Type: application/json' -d '{"regenerate":true}' 'http://localhost:8000/api/analytics/sessions/5/summary'
```

## Flag-only moderation assistant
All moderation endpoints are authenticated and broadcaster-scoped. Deterministic flags cover an identical message posted at least four times by one viewer within 20 seconds, messages with at least 12 letters and 80% uppercase, bursts of at least six repeated tokens or eight Twitch emote fragments, and links from accounts with no earlier chat in that session. The optional Gemini classification considers only borderline messages inside the existing periodic queue/poll batch. Invalid/failed output is ignored; these flags never call Twitch moderation APIs. New tables use additive metadata creation; the SQLite schema helper adds `chat_messages.moderation_ai_checked` to existing databases.

- `GET /api/moderation/incidents?status=open` lists up to 200 flags (`open|dismissed|reviewed|all`).
- `POST /api/moderation/incidents/{id}/dismiss` records a dismiss decision and AuditLog status transition.
- `POST /api/moderation/incidents/{id}/reviewed` records a review decision and AuditLog status transition.
- `/ws/dashboard` emits `moderation.incident.created` only to that broadcaster's dashboard connection. Moderation is never sent through `/ws/overlay` or `/ws/overlay/question`.

```bash
curl -b 'stream_pilot_session=<SESSION_COOKIE>' 'http://localhost:8000/api/moderation/incidents?status=open'
curl -X POST -b 'stream_pilot_session=<SESSION_COOKIE>' 'http://localhost:8000/api/moderation/incidents/12/dismiss'
curl -X POST -b 'stream_pilot_session=<SESSION_COOKIE>' 'http://localhost:8000/api/moderation/incidents/12/reviewed'
```

## Optional native Twitch polls
Native polls are disabled by default. Turning on `PUT /api/settings/native-polls` records the broadcaster's preference and reports whether Twitch reauthorization is required; the optional `channel:manage:polls` OAuth scope is requested only on a subsequent `/auth/twitch` authorization start while that broadcaster's authenticated session is present. Disabling the feature does not request the scope. Reauthorization replaces the stored OAuth grant with the selected scopes. Existing `!vote N` challenge creation remains available at all times.

The current [Twitch Create Poll reference](https://dev.twitch.tv/docs/api/reference/#create-poll) requires a user token with `channel:manage:polls`; title max 60 characters; 2–5 choices; choice title max 25 characters; duration 15–1800 seconds. Twitch starts the poll immediately and allows one active poll at a time. Twitch returns 400 for invalid bounds or an already-running poll, 401 for missing scope/invalid identity/token, 403 when the channel is not Affiliate or Partner, and 429 for rate limiting. StreamPilot maps these to safe errors. The Affiliate/Partner error message explicitly offers the existing `!vote N` path; the dashboard starts that path only after the streamer chose “Approve & create Twitch poll.”

- `GET /api/settings/native-polls`: read the opt-in and `reauthorization_required` state.
- `PUT /api/settings/native-polls` with `{"enabled":true}`: enable and prompt for Twitch reauthorization; `false` disables the feature.
- `GET /api/polls/twitch`: list this broadcaster's locally tracked polls and sync their status from Helix when the option is enabled and the scope is present.
- `POST /api/polls/twitch/create` with `{"suggestion_id":7,"duration_seconds":300}`: create a Twitch poll from an already approved suggestion. The approved challenge draft is retained, allowing fallback to `!vote N` if Twitch rejects eligibility.
- `POST /api/polls/twitch/{id}/end`: terminate the broadcaster-owned active poll, preserving visible results.

Before creation StreamPilot checks its active challenge/tracked poll state and queries Twitch for active polls; it also serializes per-broadcaster create requests in the current single-process deployment. No AI worker creates or ends native polls. `NativeTwitchPoll` stores broadcaster/session, approved suggestion and challenge references, Twitch poll ID, title/options/duration and status. Schema creation is additive through `Base.metadata.create_all`; an existing-database helper adds the optional preference and approved challenge link columns.

```bash
curl -b 'stream_pilot_session=<SESSION_COOKIE>' 'http://localhost:8000/api/settings/native-polls'
curl -X PUT -b 'stream_pilot_session=<SESSION_COOKIE>' -H 'Content-Type: application/json' -d '{"enabled":true}' 'http://localhost:8000/api/settings/native-polls'
curl -b 'stream_pilot_session=<SESSION_COOKIE>' 'http://localhost:8000/api/polls/twitch'
curl -X POST -b 'stream_pilot_session=<SESSION_COOKIE>' -H 'Content-Type: application/json' -d '{"suggestion_id":7,"duration_seconds":300}' 'http://localhost:8000/api/polls/twitch/create'
curl -X POST -b 'stream_pilot_session=<SESSION_COOKIE>' 'http://localhost:8000/api/polls/twitch/1/end'
```
