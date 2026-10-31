# Sequential Build Plan

This replaces the old scaffold-first plan. Re-run completed phases only to detect regressions.

## Phase 0 — Baseline verification
Inspect branch/working tree, versions, `.gitignore` and env example. Run backend pytest/Ruff and frontend tests/typecheck/build. **Gate:** all pass or failures are isolated and documented.

## Phase 1 — Provider-contract and live integration verification (NEXT)
Audit Twitch OAuth grant/state/redirect/scopes/token refresh/validation/revocation; EventSub types/versions/conditions/WebSocket welcome/keepalive/reconnect/revocation; chat read/send identity and permissions; Gemini model availability/structured output/SDK/timeouts/quota. Add mocked failure tests. Prepare an owner-approved controlled-channel test. **Gate:** contract audit and mocked tests pass; live message, moderation, vote, overlay, timer and announcement are observed or individually documented as blocked.

## Phase 2 — Security and session correctness
Verify broadcaster/session ownership on all reads/writes; demo-mode production safety; cookies/CSRF/CORS; OAuth lifecycle; key rotation; overlay token rotation; logging; rate limits; single-process worker assumptions; SQLite migration/backup. **Gate:** security matrix passes and residual limitations are documented.

## Phase 3 — UX and resilience
Implemented Smart Queue raw-chat/queue split, status/kind/search filters, queue actions, keyboard shortcuts, connection indicator, dashboard WebSocket REST recovery, and optional featured overlay question. Verify UI tests/typecheck/build once frontend dependencies install; test loading/empty/error/reconnect/stale states, demo/live distinction, bounded chat, accessibility and mobile. Improve only confirmed defects.

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
