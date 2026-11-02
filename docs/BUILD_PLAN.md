# Sequential Build Plan

This replaces the old scaffold-first plan. Re-run completed phases only to detect regressions.

## Phase 0 — Baseline verification
Inspect branch/working tree, versions, `.gitignore` and env example. Run backend pytest/Ruff and frontend tests/typecheck/build. **Gate:** all pass or failures are isolated and documented.

## Phase 1 — Provider-contract and live integration verification (NEXT)
Audit Twitch OAuth grant/state/redirect/scopes/token refresh/validation/revocation; EventSub types/versions/conditions/WebSocket welcome/keepalive/reconnect/revocation; chat read/send identity and permissions; Gemini model availability/structured output/SDK/timeouts/quota. Add mocked failure tests. Prepare an owner-approved controlled-channel test. **Gate:** contract audit and mocked tests pass; live message, moderation, vote, overlay, timer and announcement are observed or individually documented as blocked.

## Phase 2 — Security and session correctness
Verify broadcaster/session ownership on all reads/writes; demo-mode production safety; cookies/CSRF/CORS; OAuth lifecycle; key rotation; overlay token rotation; logging; rate limits; single-process worker assumptions; SQLite migration/backup. **Gate:** security matrix passes and residual limitations are documented.

## Phase 3 — UX and resilience
Implemented Smart Queue raw-chat/queue split, status/kind/search filters, queue actions, keyboard shortcuts, connection indicator, dashboard WebSocket REST recovery, stable challenge-option input focus, and persisted challenge-result restoration after reload. The protected featured-question overlay remains implemented but its navigation is hidden pending controlled verification. Frontend local gates currently pass; live provider behavior remains separate.

## Phase 4 — Deployment readiness
Clean Compose build, persistent DB volume, Nginx WebSocket proxy, exact callback, HTTPS, strict CORS and production secrets. Deploy only with approval.

## Phase 5 — Submission
Check rules/deadline, repository/secret scan, honest demo evidence and Codex use. Submit only with approval.

## Implemented areas — do not rebuild
React dashboard/overlay; FastAPI/Pydantic; SQLAlchemy/SQLite; challenge lifecycle/voting; Gemini adapter; Twitch OAuth/token handling; EventSub chat/moderation; protected overlay/WebSocket; mocked tests; Docker/Compose/Nginx files. Presence in source is not proof of live verification.

## Smart Interaction Queue — implementation added
The queue adds EventSub question/request intake, same-session fuzzy grouping, optional bounded batched Gemini grouping with deterministic fallback, explainable priority, broadcaster-scoped lifecycle APIs, a broadcaster-scoped dashboard WebSocket and a Smart Queue UI alongside raw chat and the existing challenge editor. The existing overlay protocol retains its auth/challenge behavior and supports an optional featured queue question. Local tests are mocked and do not verify real Gemini/EventSub delivery.

## AI reply drafts — implementation added
The queue supports streamer-requested Gemini reply drafts grounded by optional broadcaster stream facts. Drafts can be edited, copied or discarded. Twitch chat is sent only by an explicit `Send to chat` action, through the shared Helix adapter; successful sends are audited and mark the queue item answered. Mocked backend coverage passes; frontend and live-provider verification remain pending as noted in `PROGRESS.md`.

## Legacy poll backend — compatibility only
The backend still contains the earlier batched poll-suggestion and optional native-poll routes for stored-data/API compatibility and future reassessment. The current dashboard deliberately does not fetch, display, approve, dismiss, or create these polls. The supported streamer workflow is the single Challenge suggestion editor followed by the existing `!vote N` path. Do not present legacy poll routes as current UI functionality.

## Community Intelligence — implementation added
The Insights view uses authenticated live analytics and a lightweight 30-minute messages/minute chart, top unanswered questions, and a copyable post-stream recap. `SessionSummary` is cached; Gemini receives only aggregate statistics, poll/challenge results and bounded canonical group summaries. End-session and Twitch stream-offline paths schedule recap generation after closing the session; an explicit `regenerate=true` request is required to bypass the cache. Empty streams receive a local recap with no Gemini call. See `PROGRESS.md` for test and build evidence.

## Optional native Twitch polls — backend compatibility only
Native Twitch poll code is retained but not exposed in the current dashboard. It remains disabled by default, requires opt-in/reauthorization and Affiliate/Partner eligibility, and has no live verification evidence. The supported default remains StreamPilot's existing `!vote N` challenge flow.

## Demo and evaluation tooling — implementation added
The timed live-channel video script is in `DEMO_SCRIPT.md`. A development-only optional JSONL replay uses the same EventSub notification processor with a separate `synthetic-replay` broadcaster and `ChatMessage.is_synthetic=true`; it is blocked under `APP_ENV=production` and is not needed for the real-channel demo. The deterministic queue evaluator and curated 150-message data set are documented in `EVALUATION.md`. `scripts/health_check.py` provides a manual one-command check for configuration, a Twitch refresh/validation, active EventSub chat subscription, one minimal Gemini request and a temporary database write. The health check rotates stored Twitch tokens when run and is not part of automated tests.
