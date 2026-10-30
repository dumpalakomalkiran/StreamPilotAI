# Technical Design Document — StreamPilot AI

## Status
Describes source visible in reviewed commit. Live provider behaviour remains unverified until observed end-to-end.

## Stack and modules
React 18/TypeScript/Vite; FastAPI/Pydantic 2; SQLAlchemy 2/SQLite; `google-genai`; Twitch OAuth/Helix/EventSub WebSocket; FastAPI WebSocket; pytest/Vitest. `main.py` owns routes/lifecycle; `config.py` settings; `database.py` engine/schema; `models.py` ORM; `schemas.py` contracts; services split Gemini, OAuth, EventSub, chat send, voting, timer and overlay.

## Data model
`StreamSession`; `Challenge`; `ChallengeOption`; `Vote` unique on `(challenge_id,twitch_user_id)`; `ProcessedEvent`; `ChatMessage`; `DeletedChatMessage` tombstones; `TwitchCredential` with encrypted access/refresh tokens, scopes/expiry and hashed app-session state.

## Invariants
One active challenge; draft requires explicit approval; one current vote per viewer/challenge; later valid vote replaces earlier; reject votes at/after deadline; closure is conditional/atomic; tie/no votes yields no winning option; broadcaster chat cannot vote; duplicate EventSub messages are not double-counted.

## Gemini boundary
Only explicit streamer invocation. At most 50 chat messages, each max 500 chars, treated as untrusted data. Structured response validated to title/question/reason, 2–4 options and 15–60 seconds. Backend assigns source. Fallback is labelled. Confirm model availability and installed SDK compatibility; historical model names are not authoritative.

## Twitch boundary
OAuth authorization-code flow; state validation; broadcaster identity lookup; encrypted tokens and refresh. Current code defaults to `user:read:chat user:write:chat`. Validate exact current scope, token type, sender/broadcaster identity and subscription conditions against Twitch docs before changing. EventSub subscriptions: `channel.chat.message`, `.message_delete`, `.clear_user_messages`, `.clear`. Subscription acceptance is not delivery proof.

## API and overlay
HTTP routes: `/api/health`, `/api/status`, `/auth/twitch`, `/auth/twitch/callback`, `/api/challenges/suggest`, `/api/challenges`, `/api/challenges/{id}/approve|launch|end`, demo-only `/api/challenges/{id}/votes`, `/api/chat/messages`, `/api/overlay/token`; WebSocket `/ws/overlay`. See `API_REFERENCE.md` and runtime OpenAPI. Events include `connection.status`, `challenge.started`, `vote.updated`, `challenge.completed`.

## Risks/limitations
`APP_SECRET_KEY` rotation makes prior Fernet ciphertext unreadable absent re-encryption. Rate limiter, worker and WebSocket registry are process-local; MVP assumes one backend process. SQLite additive schema updates are not a full migration system. Overlay URL is bearer credential. Validate all broadcaster/session ownership paths, cookies/CSRF/CORS and demo-mode production safety.
