# Architecture Decision Records

- **ADR-001 Gemini:** use `google-genai`; explicit generation, structured output, mock tests; verify model/quota before selection.
- **ADR-002 SQLite:** use SQLite/SQLAlchemy for single-streamer MVP; migrate only when required.
- **ADR-003 Approval:** AI never approves/launches.
- **ADR-004 Demo:** deterministic labelled demo; never present synthetic data as live; production must not rely on demo auth bypass.
- **ADR-005 No Extension:** dashboard + browser overlay + APIs for MVP.
- **ADR-006 Voting:** one active challenge; unique `(challenge_id,twitch_user_id)` vote updated on later valid vote; tie/no votes has no winner.
- **ADR-007 Tokens:** Fernet-encrypt Twitch tokens; hash app-session/overlay tokens. Changing `APP_SECRET_KEY` invalidates ciphertext unless deliberate re-encryption is implemented.
- **ADR-008 Proxy:** same-origin APIs; Vite in dev, Nginx in Compose; verify WebSocket upgrade.
- **ADR-009 Moderation:** delete moderated text from future context; bounded ID tombstones; prior vote is a separate action and is not reversed.
- **ADR-010 Single process:** in-memory rate limit, worker and WebSocket registry are process-local; no horizontal replicas without shared coordination/leader ownership.
- **ADR-011 Twitch scopes:** no speculative scope changes; verify exact current scope/token/identity requirements and record evidence.
- **ADR-012 Next phase:** verify existing integrations and failure coverage before new features.
- **ADR-013 Smart Interaction Queue:** persist eligible EventSub chat as broadcaster-scoped interactions; detect and fuzzy-group deterministically first; batch unmatched candidates to Gemini on a configurable interval/count with schema validation and deterministic fallback. Hash viewer identity with HMAC, persist score components, and remove queue entries when chat is moderated. New tables are additive via the existing metadata creation path because Alembic is not configured.

## 2026-11-01 — Stream lifecycle and database behavior
- Use Twitch `stream.online`/`stream.offline` EventSub subscriptions to open and close the broadcaster's live session. Twitch documents these subscriptions as requiring no user scopes; preserve existing chat scopes unchanged.
- SQLite uses WAL with a 5-second busy timeout. Synchronous ORM work in async EventSub, challenge-clock, OAuth callback, challenge action and overlay paths is dispatched through the threadpool.
- Keep Gemini structured output on the installed SDK's `response_schema` Pydantic integration (confirmed with google-genai 1.75.0); set `gemini-3.5-flash-lite` as the documented default while allowing `GEMINI_MODEL` override.

## 2026-11-01 — Real-time queue and featured overlay
- Keep the overlay socket's token auth and challenge messages; add a separate session-cookie-authenticated `/ws/dashboard` broadcaster channel. Clients refetch REST state after reconnect rather than relying on event replay.
- Persist the optional featured group ID on `StreamSession`. Only canonical queue text, kind and viewer count reach the overlay. Answer, dismiss and moderation clear the feature, and moderation replaces affected canonical text from retained messages.

## 2026-11-01 — Grounded AI reply drafts
- AI replies are drafts only. The authenticated streamer explicitly asks for a draft, reviews/edits it, and only a separate send endpoint posts it through the existing Helix chat-send adapter.
- Keep up to 3000 characters of optional streamer-authored facts in broadcaster-scoped settings. Missing/unsupported facts yield a safe holding draft. Chat samples are capped at five, JSON encoded, treated as untrusted, and passed to Gemini with structured Pydantic output and no tools.
- Store suggestions and a separate successful-send audit record (actor, time, exact message and Twitch ID); record model/usage/latency/failure in redacted logs, without storing request text in logs. Add tables through metadata creation as this repo has no Alembic history.

- **ADR-014 Audience polls:** infer disagreement inside the existing batched queue-grouping Gemini call; do not make a separate request per message. Validate distinct-viewer evidence in backend code. Persist only a draft; streamer action converts it to an unapproved existing Challenge. The default approval path reuses `!vote N`, timer and overlay. Native Twitch polls are a separate explicit option, disabled by default; only an opt-in saved in broadcaster settings requests `channel:manage:polls` during a later OAuth flow. If native creation is unavailable for Affiliate/Partner eligibility, fall back to the existing StreamPilot vote path. Add poll tracking through metadata creation because Alembic is not configured.

- **ADR-015 Community Intelligence:** aggregate only persisted moderation-filtered chat and queue/poll data. Cache structured summaries by broadcaster/session, call Gemini once for a missing recap and only regenerate on an explicit request. Session end is non-blocking and schedules recap generation; empty sessions use a deterministic zero-activity recap.

- **ADR-016 Flag-only moderation assistant:** deterministic signals create broadcaster/session-scoped review flags; optional borderline classification is folded into the existing batched queue/poll Gemini request. The model may only add a validated flag; moderation data is dashboard-only, and status transitions are audited. No Twitch ban, timeout, or delete API is imported or invoked from this path. Add tables through metadata creation and the AI-checked marker through the existing additive SQLite schema helper.

- **ADR-017 Optional native Twitch polls:** use Twitch Helix native polls only after streamer opt-in plus reauthorization for `channel:manage:polls`. Never request this permission by default or create polls from AI/background paths. Check active polls in Twitch and StreamPilot before creation. Keep `!vote N` as the default and explicit eligibility fallback. Current Create Poll bounds are title <=60 characters, 2–5 choices of <=25 characters and duration 15–1800 seconds; map rate, scope, eligibility and active-poll failures safely.
# ADR-018: Isolate rehearsal replay and report evaluation conservatively

The optional development replay feeds EventSub's existing notification processor rather than duplicating persistence/detection logic. A separate `synthetic-replay` broadcaster/session prevents replay data from mixing with a connected broadcaster, and an additive `is_synthetic` chat column plus REST/UI label preserves provenance. Replay refuses `APP_ENV=production`. It is rehearsal tooling only; live demonstration uses Twitch EventSub directly.

The deterministic evaluation data and metrics are versioned with an explicit limitations statement. A small curated set is a regression signal, not a claim of live accuracy. The manual health check forces Twitch refresh to prove the refresh grant is accepted, which rotates and persists OAuth tokens, then checks token identity, active WebSocket chat subscription, Gemini reachability and temporary database writes. It never posts chat or creates polls.
