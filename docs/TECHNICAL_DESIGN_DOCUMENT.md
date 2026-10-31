# Technical Design Document — StreamPilot AI

## Status
Describes source visible in reviewed commit. Live provider behaviour remains unverified until observed end-to-end.

## Stack and modules
React 18/TypeScript/Vite; FastAPI/Pydantic 2; SQLAlchemy 2/SQLite; `google-genai`; Twitch OAuth/Helix/EventSub WebSocket; FastAPI WebSocket; pytest/Vitest. `main.py` owns routes/lifecycle; `config.py` settings; `database.py` engine/schema; `models.py` ORM; `schemas.py` contracts; services split Gemini, OAuth, EventSub, chat send, voting, timer and overlay.

## Data model
`StreamSession`; `Challenge`; `ChallengeOption`; `Vote` unique on `(challenge_id,twitch_user_id)`; `ProcessedEvent`; `ChatMessage`; `DeletedChatMessage` tombstones; `InteractionGroup`; `Interaction`; `TwitchCredential` with encrypted access/refresh tokens, scopes/expiry and hashed app-session state. New interaction tables are additive and are created with the existing `Base.metadata.create_all` startup path; this project has no Alembic migration history.

## Invariants
One active challenge; draft requires explicit approval; one current vote per viewer/challenge; later valid vote replaces earlier; reject votes at/after deadline; closure is conditional/atomic; tie/no votes yields no winning option; broadcaster chat cannot vote; duplicate EventSub messages are not double-counted.

## Gemini boundary
Challenge suggestions are only generated on explicit streamer invocation. The interaction queue has a separate scheduled batching task: unmatched candidate messages are grouped in one bounded Gemini request per batch, never per message; each message is truncated to 300 characters and at most 20 candidates and 100 open group descriptions are included. Chat is JSON encoded and explicitly delimited as untrusted data; no tools are configured. A Pydantic structured response is validated, and invalid/failed/timed-out results fall back to deterministic grouping. Grouping does not approve or launch challenges. Challenge suggestions remain bounded to 50 messages × 500 characters.

## Smart Interaction Queue
EventSub's existing `channel.chat.message` persistence path detects question/request candidates without an LLM call. It ignores the broadcaster, known/flagged bots, commands other than `!ask`, very short text, and Twitch fragments that contain only emotes. Text normalization removes URLs, known emotes and punctuation, and collapses repeated characters. `difflib.SequenceMatcher` compares normal and sorted-token forms against open/snoozed groups in the same session. Unmatched rows persist with a nullable `group_id` until the periodic or threshold batch resolves them. Per-viewer identity is an HMAC-SHA256 using `APP_SECRET_KEY`, not a newly stored Twitch identity.

Priority is `5*ln(1+asker_count) + 3*max(0,1-age_hours/24) + 2*ln(1+max(0,message_count-1))`. Stored asker, recency and repetition components are returned for UI explanation. Answer, dismiss and snooze require an open group. Snoozed groups reopen on expiry; answered/dismissed groups reopen after the configured number of distinct askers beyond their close-time count. EventSub moderation removes affected interaction rows and recalculates group counts, so moderated text is not retained in queue context.

## Twitch boundary
OAuth authorization-code flow; state validation; broadcaster identity lookup; encrypted tokens and refresh. Current code defaults to `user:read:chat user:write:chat`. Validate exact current scope, token type, sender/broadcaster identity and subscription conditions against Twitch docs before changing. EventSub subscriptions: `channel.chat.message`, `.message_delete`, `.clear_user_messages`, `.clear`, `stream.online`, and `stream.offline`. Stream lifecycle events have no user-scope requirement and use only the broadcaster condition; chat scopes remain unchanged. `stream.offline` ends the persisted session and `stream.online` creates a new one. Subscription acceptance is not delivery proof.

## API and overlay
HTTP routes include `/api/interactions` list/detail/lifecycle and `/api/interactions/{id}/feature`; see `API_REFERENCE.md` and runtime OpenAPI. The new `/ws/dashboard` registry is keyed by authenticated broadcaster and publishes only safe queue projections plus session lifecycle events. Clients fetch REST state at connection/reconnection; no replay buffer is maintained. Existing `/ws/overlay` route, auth and challenge events are unchanged; optional featured questions use a separate token-protected `/ws/overlay/question`, exposing canonical text, kind and viewer count only, with reconnect state recovered from `StreamSession.featured_interaction_group_id`. Moderation clears the featured pointer, hides the overlay item and refreshes affected queue groups after replacing canonical text with retained non-moderated member text.

## Risks/limitations
`APP_SECRET_KEY` rotation makes prior Fernet ciphertext unreadable absent re-encryption. Rate limiter, worker and WebSocket registry are process-local; MVP assumes one backend process. SQLite uses WAL and a 5-second busy timeout; async worker/action DB work is dispatched through a threadpool. SQLite additive schema updates are not a full migration system. Overlay URL is bearer credential. Validate all broadcaster/session ownership paths, cookies/CSRF/CORS and demo-mode production safety.
