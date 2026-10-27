# Build Progress

## Current status

The existing React dashboard and project documentation have been preserved and connected to a FastAPI backend. Persistence, challenge approval/lifecycle, vote handling, Gemini structured output, Twitch OAuth/EventSub/chat handling, and the protected WebSocket overlay have implementation and automated mock coverage. One real Gemini structured suggestion is now verified; broadcaster OAuth and EventSub subscription acceptance are verified against Twitch. No real EventSub chat notification has been observed yet, and deployment remains unverified.

## Completed implementation

- [x] Inspected the scaffold, read project instructions/design/docs, and recorded the environment: Python 3.15.0 (Python 3.13 also installed), Node 24.13.1, npm.
- [x] Installed backend/frontend dependencies; removed the Uvicorn `standard` extra because its PyYAML dependency could not build under the selected Python 3.15 environment.
- [x] Added SQLAlchemy session/challenge/option/vote/EventSub/chat/credential persistence and additive SQLite schema updates.
- [x] Implemented create, approve, launch, end, history, current-vote replacement, event deduplication, atomic challenge closure, one active challenge, and tie/no-vote handling.
- [x] Implemented explicit Gemini suggestions through `google-genai`, Pydantic structured output, bounded untrusted chat context, SDK and async timeouts, request limiting, and labeled fallback.
- [x] Implemented Twitch OAuth state/cookie checks, token encryption, minimum chat scopes, token refresh/revocation handling, EventSub reconnect, bounded recent chat capture, strict `!vote N` parsing, and official chat announcements.
- [x] Connected live chat history and persisted vote totals to the dashboard; demo examples remain labeled synthetic data.
- [x] Added the protected overlay WebSocket with reconnect, current challenge recovery, countdown, vote totals, completion result, and private token rotation.
- [x] Added same-origin dev proxy and Nginx/Compose deployment configuration with persistent SQLite volume, private backend service, and health check.
- [x] Recorded architecture decisions in `docs/DECISIONS.md`; updated setup, architecture, TDD, and README.

## Verification run

Commands and results from the latest completed runs:

- `python -m pytest -q -p no:cacheprovider` — 11 passed on final backend code (Gemini/Twitch services mocked; no test API quota used).
- `python -m ruff check app tests` — passed.
- `npm test -- --run` — 2 passed on final frontend code.
- `npm run typecheck` — passed.
- `npm run build` — passed.
- `npm audit` — 0 vulnerabilities reported.
- `docker --version` and `docker compose config --quiet` — could not run because Docker is not installed in the environment.

The first backend verification accidentally loaded the developer's local `.env`, which caused tests to attempt a real Gemini call. Tests were corrected to isolate settings and mock all provider calls. A configured Gemini credential appeared in that failed local test output; rotate that key in AI Studio. No credential value is recorded in this document or source files.

The owner has configured Gemini and Twitch app credentials. The configured model is `gemini-3.5-flash-lite`. Safe diagnostic calls (no Twitch chat) showed: text-only request HTTP 200; structured request initially HTTP 400 `INVALID_ARGUMENT` because the generated schema contained unsupported `additional_properties`; after removing `extra="forbid"` from the Gemini-only output models, a 20-second structured request returned HTTP 504 `DEADLINE_EXCEEDED`; the same structured path returned a validated real Gemini proposal with `AI_REQUEST_TIMEOUT_SECONDS=60`. The timeout is persisted as 60 seconds in ignored `backend/.env`. The dashboard was also tested through **Generate suggestion** and displayed a real game challenge (3 options); its `/api/challenges/suggest` request returned HTTP 200. A brief dashboard test against `gemini-3.8-flash` returned HTTP 503 `UNAVAILABLE` due to temporary provider demand; configuration was restored to the owner's selected `gemini-3.5-flash-lite` before the successful dashboard test. Provider failure logs now include sanitized status/message diagnostics, with configured API keys and chat snippets redacted; prompts, headers, and raw response bodies are not logged. A local settings audit found the Twitch encryption secret was still a placeholder and demo mode was on. I generated a fresh `APP_SECRET_KEY` directly into the ignored `backend/.env` without printing it and switched `DEMO_MODE=false`; settings now confirm both integrations configured, app secret ready, and live mode. The owner completed broadcaster OAuth. The dashboard reported **LIVE MODE** and **Twitch account authorized**, and authenticated `/api/challenges` and `/api/chat/messages` requests returned HTTP 200. This verifies the OAuth callback/session and protected API access only; no real EventSub chat notification has been observed yet. Secret-free EventSub worker, connection, welcome, subscription, and retry logs have been added to support live diagnosis. No Twitch message or announcement was sent during verification.

The dashboard was reopened after a live chat inspection. The active Twitch session had zero stored messages. A read-only Twitch Helix subscription check returned HTTP 200 with two stale `channel.chat.message` subscriptions in `websocket_disconnected` state and one current subscription in `enabled` state after the backend worker restarted. The EventSub handler previously discarded broadcaster-authored messages before persistence; it now persists those messages but still excludes broadcaster `!vote` commands. The chat panel now explicitly says it is waiting for its first Twitch message, and synthetic sample messages are not shown when an authorized live account has no received chat. This fixes the empty-state confusion; actual message delivery still requires a streamer-authored test message and has not been verified.

### Sanitized Gemini request/response diagnostics

- Text-only request — model `gemini-3.5-flash-lite`; no chat context; HTTP 200; text response received.
- Structured request before schema fix — HTTP 400; `INVALID_ARGUMENT`; Google rejected `additional_properties` in `generation_config.response_schema` and `generation_config.response_schema.properties[2].value.items`.
- Structured request after schema fix with 20-second timeout — HTTP 504; `DEADLINE_EXCEEDED`; provider message: “Deadline expired before operation could complete.”
- Structured request after schema fix with 60-second timeout — SDK success; validated result `source=gemini`, 3 options, duration 30 seconds.
- Dashboard **Generate suggestion** on `gemini-3.5-flash-lite` — `/api/challenges/suggest` HTTP 200; displayed a game challenge with 3 voting options.

## Remaining verification and blockers

- [ ] Verify clean Docker Compose configuration/build on a machine with Docker (Docker is not installed here).
- [x] Diagnose the real Gemini structured-output HTTP 400 and timeout; verify a real structured suggestion returns `source=gemini` with the saved 60-second timeout.
- [x] Complete real Twitch OAuth with the user's registered app and broadcaster authorization; confirm live-mode dashboard status and authenticated API access.
- [x] Confirm Twitch accepted an EventSub `channel.chat.message` subscription; the current subscription state is `enabled` (two older subscriptions are stale/disconnected).
- [ ] Confirm EventSub delivery. The live `/api/chat/messages` store was empty at inspection time; an enabled subscription is not proof of receiving or persisting a real chat message.
- [ ] Observe a real EventSub chat notification and vote from the broadcaster's chat, then verify overlay delivery, token refresh/revocation, and one chat result announcement in a controlled channel. The app will not post a test message without explicit approval.
- [ ] Confirm the live deployment host/HTTPS URL and exact OAuth callback; deployment requires owner approval.
- [ ] Capture real integration demo evidence, verify repository/secret scan, and complete the external submission only with owner authorization.
- [ ] Initialize/verify the Git repository; this workspace currently has no `.git` metadata.

## Next task

Gemini structured generation has passed both a real service request and the dashboard button flow with `gemini-3.5-flash-lite` and the persisted 60-second timeout. Broadcaster OAuth, authenticated API access, and an enabled Twitch chat subscription are verified. The live chat panel now accurately shows that no messages have arrived yet. Next, the owner should send a harmless ordinary message in the connected Twitch channel; then verify its appearance here before testing live viewer votes and overlay behavior. Do not describe EventSub chat delivery or Twitch voting as verified until those checks pass against Twitch.

Local development servers are currently running at `http://localhost:5173` (dashboard) and `http://localhost:8000` (backend). The dashboard shows **Twitch account authorized** and **Waiting for first chat message** in live mode. To verify EventSub end to end, the owner must send a harmless ordinary message in the connected Twitch channel while the app is connected; do not publish a bot message without permission.
