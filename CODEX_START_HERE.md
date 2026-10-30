# Codex Start Here — StreamPilot AI

You are continuing an existing application. Do not scaffold a new app or rebuild completed modules.

**Reviewed baseline:** `dumpalakomalkiran/StreamPilotAI`, branch `main`, commit `8c3d08f373388dd5b63d5cc1c81cdf78768ed1d4`.

Read: `AGENTS.md`, `docs/PROGRESS.md`, `docs/BUILD_PLAN.md`, `docs/NEXT_STEP_RUNBOOK.md`, `docs/API_REFERENCE.md`, `docs/TECHNICAL_DESIGN_DOCUMENT.md`, `docs/TEST_CASES.md`, `docs/RESEARCH_VALIDATION.md`.

## Existing implementation to preserve
React dashboard and OBS overlay; FastAPI API; SQLAlchemy models for sessions/challenges/options/votes/chat/credentials; challenge create/approve/launch/end/history; vote upsert and timer closure; Gemini structured output with Pydantic validation and labelled fallback; Twitch authorization-code OAuth and encrypted token storage/refresh; EventSub chat plus message-delete/user-clear/full-clear handlers; `!vote N`; protected overlay token and WebSocket updates; automated backend/frontend tests.

## Immediate task
Execute Phase 1 in `docs/NEXT_STEP_RUNBOOK.md`: inspect working tree; run all existing test/lint/build gates; fix only reproducible local defects; audit Twitch OAuth/scopes/EventSub/chat-send contracts and Gemini model/structured-output behaviour against current official docs; add mocked failure-path tests; prepare an owner-approved controlled live test.

### Commands (PowerShell)
Backend: `cd backend`; `python -m pip install -e ".[dev]"`; `python -m pytest -q`; `ruff check app tests`.
Frontend: `cd frontend`; `npm ci`; `npm test`; `npm run typecheck`; `npm run build`.

Do not display `.env` values. Do not change Twitch scopes speculatively. Do not send real chat, launch challenges, deploy or publish without explicit owner approval. Do not mark integrations verified unless the complete live path is observed.
