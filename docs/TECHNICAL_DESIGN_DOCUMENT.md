# Technical Design Document (TDD) — StreamPilot AI

## 1. Scope and status
This document describes the current MVP implementation and target behavior. Automated service tests use mocks; Gemini and Twitch live behavior remains unverified until tested with authorized real accounts.

## 2. Stack
React, TypeScript, Vite; Python 3.12+, FastAPI, Pydantic; SQLAlchemy + SQLite; `google-genai`; official Twitch OAuth, EventSub WebSocket and chat APIs; FastAPI WebSockets; pytest/HTTPX and Vitest/React Testing Library.

## 3. Modules
`main.py`: app, middleware, and REST routes. `config.py`: environment settings. `models.py`: ORM. `schemas.py`: request validation. `services/challenges.py`: Gemini. `services/twitch_auth.py`: OAuth. `services/eventsub.py`: WebSocket lifecycle and chat ingestion. `services/twitch_chat.py`: send chat. `services/voting.py`: vote and tally logic. `services/overlay.py`: realtime fan-out.

## 4. Data model
- StreamSession: ID, broadcaster ID/login, start/end timestamps, demo/live mode.
- Challenge: ID, session ID, title/question/reason, status, duration, source, start/end/completion timestamps, winning option.
- ChallengeOption: ID, challenge ID, label, description, order.
- Vote: ID, challenge ID, option ID, Twitch user ID, update timestamp. Unique constraint on `(challenge_id, twitch_user_id)`.
- TwitchCredential: broadcaster ID, server-side token fields, scopes, expiry. Never serialize credentials to public APIs.
- ChatMessage: bounded recent messages for a stream session, keyed by EventSub message ID to avoid duplicate storage.

## 5. API contract (target)
- `GET /api/health`
- `GET /api/status` (booleans only, no secrets)
- `GET /auth/twitch`, `GET /auth/twitch/callback`
- `POST /api/challenges/suggest`
- `POST /api/challenges`
- `POST /api/challenges/{id}/approve`
- `POST /api/challenges/{id}/launch`
- `POST /api/challenges/{id}/end`
- `GET /api/challenges`
- `GET /api/chat/messages?limit=50`
- `POST /api/overlay/token` (returns one private overlay URL; token is stored only as a hash)
- `WS /ws/overlay` (overlay token is passed as a WebSocket subprotocol, not a URL path)

Document any route changes and keep frontend types aligned.

## 6. Gemini generation
Only call when the streamer requests a suggestion. Bound chat message count/length. Treat messages as untrusted data, not instructions. Use structured output through the current `google-genai` SDK, validate with Pydantic, require 2–4 options and 15–60 seconds, and handle timeout/invalid output safely. Fallback must be labeled `fallback`, never AI-generated. Keep `GEMINI_API_KEY` backend-only and configure model via `GEMINI_MODEL`.

Official docs: https://ai.google.dev/gemini-api/docs and https://ai.google.dev/gemini-api/docs/structured-output

## 7. Twitch integration
Register app and exact callback. Use authorization-code OAuth with cryptographically random state and minimum required scopes. Handle EventSub welcome, notification, keepalive, reconnect, revocation, and error events. Deduplicate using message IDs. Send chat through official API and correct permissions. Handle token expiry/revocation. Redact tokens from logs.

Official docs: https://dev.twitch.tv/docs/authentication/ ; https://dev.twitch.tv/docs/chat/ ; https://dev.twitch.tv/docs/eventsub/handling-websocket-events/ ; https://dev.twitch.tv/docs/api/reference/

## 8. Voting algorithm
Confirm active challenge and deadline; ignore broadcaster's own messages; validate `!vote N`; deduplicate EventSub messages; store at most 500 recent chat messages per session; upsert vote by challenge and Twitch user ID; broadcast totals; close atomically; reject late votes; apply documented tie handling; announce once.

## 9. WebSocket events
`challenge.started`: safe challenge display fields and ends_at. `vote.updated`: counts, percentages, total. `challenge.completed`: result or tie. `connection.status`: connection state. `error`: safe code and user-readable message. Never send credentials or private metadata.

## 10. Security
Backend-only secrets; OAuth state validation; strict CORS; HTTPS in production; CSRF protection where cookies are used; Pydantic validation; authorization for state changes; rate limits; random overlay tokens; redacted logs; bounded untrusted AI context; no arbitrary game control.

## 11. Testing
Mock Gemini and Twitch for ordinary tests. Cover schema, votes, duplicate events, concurrency, OAuth state, reconnects, WebSocket events, and frontend actions. Manually verify real OAuth, chat, overlay, and announcement before submission.

## 12. Deployment
Use HTTPS, persistent DB, correct callback URL, strict origins, WebSocket-capable host, environment variables, health checks, and explicit approval before deployment.
