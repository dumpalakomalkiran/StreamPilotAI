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
