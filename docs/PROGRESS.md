# Build Progress

## Current status

The existing React dashboard and project documentation have been preserved and connected to a FastAPI backend. Persistence, challenge approval/lifecycle, vote handling, Gemini structured output, Twitch OAuth/EventSub/chat handling, and the protected WebSocket overlay have implementation and automated mock coverage. Current live configuration is loaded (`DEMO_MODE=false`, Gemini and Twitch credentials configured), but Gemini generation is returning transient provider errors and Twitch authorization must be renewed after the app encryption key was replaced. No real EventSub chat notification has been observed, and deployment remains unverified.

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
- Current configured model `gemini-3.8-flash` appears in the real Gemini model listing with `generateContent` support. Structured suggestion attempt returned HTTP 504 `DEADLINE_EXCEEDED`; retry with the 60-second timeout returned HTTP 503 `UNAVAILABLE` (high demand). Both returned the clearly labeled fallback, so current live suggestion generation is not verified. The ignored `.env` is set to `gemini-3.8-flash` and a 60-second timeout.

## Remaining verification and blockers

- [ ] Verify clean Docker Compose configuration/build on a machine with Docker (Docker is not installed here).
- [x] Diagnose the real Gemini structured-output HTTP 400 and timeout; verify a real structured suggestion returns `source=gemini` with the saved 60-second timeout.
- [x] Restore non-empty Gemini API key/model and Twitch Client ID/secret in ignored `backend/.env`; verify live mode and both integrations configured.
- [ ] Gemini `gemini-3.8-flash` structured generation: the model is listed and supported but current real calls returned 504 then 503; retry after provider demand clears.
- [ ] Renew Twitch broadcaster OAuth. The previous stored token could not be decrypted with the configured placeholder secret; a fresh random local `APP_SECRET_KEY` was generated, stale authorization was cleared, and the dashboard is ready for reconnect.
- [x] Complete real Twitch OAuth with the user's registered app and broadcaster authorization; confirm live-mode dashboard status and authenticated API access.
- [x] Confirm Twitch accepted an EventSub `channel.chat.message` subscription; the current subscription state is `enabled` (two older subscriptions are stale/disconnected).
- [ ] Confirm EventSub delivery. The live `/api/chat/messages` store was empty at inspection time; an enabled subscription is not proof of receiving or persisting a real chat message.
- [ ] Observe a real EventSub chat notification and vote from the broadcaster's chat, then verify overlay delivery, token refresh/revocation, and one chat result announcement in a controlled channel. The app will not post a test message without explicit approval.
- [ ] Confirm the live deployment host/HTTPS URL and exact OAuth callback; deployment requires owner approval.
- [ ] Capture real integration demo evidence, verify repository/secret scan, and complete the external submission only with owner authorization.
- [ ] Verify tracked and untracked files plus secret scan before any repository publication. Git metadata is present; current work has local modifications.

## Next task

With the owner-selected `gemini-3.5-flash-lite` active, verify one authenticated live Gemini suggestion and its response time. EventSub message, deletion, per-user clear, and full clear subscriptions are accepted and enabled on Twitch; next verify actual message delivery and deletion handling in a controlled channel. Then test live viewer voting and overlay delivery. Do not describe EventSub moderation delivery as verified until Twitch emits and StreamPilot processes a real deletion notification.

Live run on 2026-10-27: `.env` now loads in live mode with Gemini/Twitch configured. The owner selected `gemini-3.8-flash`; a real model listing confirms support for `generateContent`, but a real structured request returned 504 and a retry at 60 seconds returned 503 high demand, both safely falling back. The old `APP_SECRET_KEY` was the example placeholder, so Twitch could not decrypt the stored refresh token. A fresh random key was written without printing it; EventSub now clears an unreadable stored authorization rather than retrying forever. The current `/api/status` reports live mode, integrations configured, Twitch unauthorized, worker stopped. Twitch OAuth is open in the browser and awaits owner approval. Frontend now avoids showing synthetic sample chat in live mode, displays a reconnect link on chat API errors, and dev proxy targets IPv4 explicitly. Backend tests: 13 passed; Ruff passed; frontend tests: 2 passed; typecheck and production build passed. `APP_ENV=development`; no chat message or Twitch announcement was sent.

### 2026-10-27 — 60-second gateway timeout investigation

- Confirmed the backend `/api/status` endpoint responds in live mode with Gemini and Twitch configured and broadcaster authorization active. The frontend dev server on port 5173 was not listening during this inspection, so a fresh dashboard request trace could not be captured.
- The active local Gemini setting is `gemini-3.8-flash` with a configured 60-second timeout. The service also allowed up to 60 seconds, matching the reported gateway deadline and leaving no margin for the endpoint to deliver its response.
- Capped the Gemini SDK and async wait to 50 seconds so the endpoint can return its labeled fallback before a common 60-second gateway deadline. Added a mocked regression test confirming a configured 60-second setting is sent to the SDK as 50 seconds.
- This mitigates the gateway timeout; it does not prove Gemini successfully generates a suggestion. Earlier real calls to `gemini-3.8-flash` returned provider 504 `DEADLINE_EXCEEDED` and 503 `UNAVAILABLE`. No additional live Gemini request was made during this diagnosis.
- Verification: backend tests passed (14), Ruff passed, frontend tests passed (2), frontend typecheck passed, and production build passed. Restarted the backend and frontend; dashboard returned HTTP 200 and its `/api/status` proxy returned live mode, Gemini/Twitch configured, and broadcaster authorized. Windows Computer Use could not confidently bind the shared dashboard tab, so no live suggestion request was sent. Next: perform one controlled Gemini request through the authenticated dashboard and record HTTP status, duration, and result source without logging API keys or chat content.

### 2026-10-28 — Twitch moderation events and Swagger API reference

- EventSub now subscribes to `channel.chat.message`, `channel.chat.message_delete`, `channel.chat.clear_user_messages`, and `channel.chat.clear`. Single deletes remove by Twitch message ID; user clears remove that viewer's messages; full clears remove the current session's messages. Removed IDs are retained as bounded (500/session) tombstones so duplicate/replayed chat events do not restore the deleted text.
- Deleted messages are excluded from `GET /api/chat/messages` and the dashboard's next fresh suggestion context. Moderation does not roll back an already accepted vote; votes remain separate actions (ADR-009). A suggestion generated before a deletion is not retroactively rewritten.
- Added `docs/API_REFERENCE.md`, expanded Swagger operation summaries/descriptions, auth details, bounds, and request/response examples, and updated the technical design and test case documents.
- Twitch Helix EventSub subscriptions check returned HTTP 200; the current subscriptions for all four chat event types reported `enabled`. This confirms Twitch accepted the subscriptions, but no actual moderation notification has yet been observed end-to-end.
- Backend: `python -m pytest -q -p no:cacheprovider` — 17 passed; `ruff check app tests` — passed. Frontend: `npm test -- --run` — 2 passed; `npm run typecheck` — passed; `npm run build` — passed. OpenAPI tests verify docs, example request schemas, and moderation details.
- The ignored local `backend/.env` is set to `gemini-3.5-flash-lite`; no API key or token was printed. `integrations_verified` remains false. Next: verify a real Twitch chat deletion event is processed, then test one authenticated Gemini suggestion with the selected model.
