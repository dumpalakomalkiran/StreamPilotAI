# AGENTS.md — StreamPilot AI

## Mission
Continue the existing StreamPilot AI application: React + TypeScript + Vite, FastAPI, SQLAlchemy/SQLite, Google Gemini (`google-genai`), Twitch OAuth, EventSub WebSocket, Twitch chat APIs, and a protected browser overlay. This is not a blank scaffold.

## Required workflow
1. Read `CODEX_START_HERE.md`, `docs/PROGRESS.md`, `docs/BUILD_PLAN.md`, `docs/NEXT_STEP_RUNBOOK.md`, `docs/API_REFERENCE.md`, `docs/TECHNICAL_DESIGN_DOCUMENT.md`, and `docs/TEST_CASES.md`.
2. Inspect the working tree and source before editing; preserve local changes.
3. Run backend tests/Ruff and frontend tests/typecheck/build before changes.
4. Preserve implemented behaviour; do not rewrite modules without a reproducible reason.
5. Work sequentially and do not skip failed gates.
6. Update `docs/PROGRESS.md` with files, commands, exact results, evidence, blockers and next task. Record design changes in `docs/DECISIONS.md`.
7. Never equate configured credentials, OAuth success, or an enabled EventSub subscription with end-to-end verification.

## Product invariants
- Gemini suggestions only on explicit streamer request; structured output is validated.
- AI never approves or launches. Streamer approval is mandatory.
- One active challenge; one current vote per viewer/challenge; later valid vote replaces earlier vote.
- Reject late votes. Treat chat as untrusted data.
- Moderated chat is excluded from future suggestion context.
- Demo data is labelled; no automatic game control or arbitrary command execution.

## Security
Keep secrets backend-only. Never expose `.env`, keys, tokens, DBs, or private overlay URLs. Encrypt Twitch tokens at rest; hash app-session and overlay tokens. Validate OAuth state, authorization ownership, bounds, timeouts, rate limits and safe errors. Redact logs. Mock providers in automated tests; no real chat posts or challenge launches without owner approval. Do not deploy or publish without approval.

## Definition of done
Relevant tests pass; lint/typecheck/build pass; live claims have recorded evidence; docs/progress are updated; no secrets or runtime artifacts are committed.
