# Sequential Codex Build Plan

## Phase 0 — Inspect and establish
Read the docs, inspect repository and toolchain, install dependencies, run existing tests, update progress.
**Checkpoint:** both apps start and backend health works.

## Phase 1 — Backend foundation
Add validated settings, SQLAlchemy models/session management, challenge/session/vote persistence, API routes, exception handling, and tests.
**Checkpoint:** backend tests pass.

## Phase 2 — Frontend foundation
Verify the starter build, add typed API client and views for Overview, Live Studio, Overlay Setup, Activity, and Settings; add loading/error states and accessibility.
**Checkpoint:** frontend typecheck, tests, and production build pass.

## Phase 3 — Challenge lifecycle and votes
Implement create/approve/launch/end/history. Enforce one current vote per Twitch user per challenge, permit vote changes, reject late votes, use transaction-safe closure and documented tie handling.
**Checkpoint:** deterministic end-to-end lifecycle test passes.

## Phase 4 — Gemini
Use `google-genai` and current official Gemini docs. Configure `GEMINI_API_KEY` and `GEMINI_MODEL` backend-only. Add structured output, Pydantic validation, bounded untrusted chat context, timeout, rate limits, labeled fallback, and mocked tests.
**Checkpoint:** mocked tests pass; real API test only when configured.

## Phase 5 — Twitch OAuth and chat
Implement OAuth state validation, callback, token storage, minimum scopes, EventSub lifecycle, chat subscriptions, duplicate-event handling, reconnect/revocation, and official send-chat API.
**Checkpoint:** actual broadcaster-authorized chat message is processed in a controlled test.

## Phase 6 — Realtime overlay
Implement protected overlay token, WebSocket challenge/vote/countdown/result events, reconnect recovery, and multi-client tests.
**Checkpoint:** real chat vote updates overlay without refresh.

## Phase 7 — Quality and security
Run backend/frontend tests, typecheck, lint, production build; test auth, validation, concurrency, timeouts, reconnects, and secret exposure.
**Checkpoint:** checks pass or limitations are documented.

## Phase 8 — Deployment
Prepare HTTPS, persistent storage, exact OAuth callback, strict origins, WebSocket-capable host, health checks, and reproducible deployment. Deploy only with owner approval.
**Checkpoint:** clean-start smoke test passes.

## Phase 9 — Submission
Finish README/docs, screenshots, sub-three-minute demo showing actual Twitch and Codex, primary Codex session ID, repository verification, and Devpost checklist.
**Checkpoint:** user confirms final submission before the official deadline.
