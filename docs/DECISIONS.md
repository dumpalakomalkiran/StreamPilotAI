# Architecture Decision Records

## ADR-001 — Gemini
Use Google's `google-genai` SDK and Gemini API because the project owner prefers Gemini and wants to manage cost via available quotas. Verify model availability and quotas; mock AI in tests.

## ADR-002 — SQLite
Use SQLite/SQLAlchemy for single-streamer MVP simplicity. Confirm persistent storage or migrate to a managed database for production.

## ADR-003 — Streamer approval
AI suggestions never launch automatically to preserve streamer control.

## ADR-004 — Demo mode
Provide deterministic, clearly labeled demo mode for testing. Do not present synthetic data as real Twitch traffic.

## ADR-005 — No Twitch Extension
Use dashboard + browser overlay + supported APIs to reduce implementation/review complexity.

## ADR-006 — One active challenge and deterministic closure
Enforce a single active challenge with a partial unique database index. Vote uniqueness is `(challenge_id, twitch_user_id)` and an upsert changes a viewer's current choice. Closure is an atomic status update; a tied or zero-vote result has no winning option. Only the process that closes an active challenge announces the result.

## ADR-007 — Twitch token and overlay credential handling
Encrypt Twitch OAuth access/refresh tokens with Fernet derived from a deployment-provided `APP_SECRET_KEY`; store the browser session and overlay token as hashes. The WebSocket receives the overlay token in a dedicated subprotocol instead of a URL path, and only display-safe state is broadcast. The overlay source URL still contains its bearer token and must be treated as private.

## ADR-008 — Same-origin frontend and API proxy
Use same-origin API URLs in the React app. Vite proxies API/auth/WebSocket requests during development; the Compose Nginx service proxies them to FastAPI in the packaged setup. SQLite is mounted at the actual database path under a named volume.

## ADR-009 — Twitch-moderated messages are removed from AI context
Subscribe to Twitch `channel.chat.message_delete`, `channel.chat.clear_user_messages`, and `channel.chat.clear` alongside `channel.chat.message`. On receipt, delete the matching stored message(s) from the active session so they disappear from the live signals endpoint and cannot be included in a later Gemini suggestion. Keep only a bounded set of deleted message IDs (500 per session) as replay tombstones; do not retain deleted message text. A vote already accepted from `!vote N` remains a separate voter action and is not reversed by later message moderation; changing that would require an explicit vote moderation policy and a durable link from a vote to its source message.
