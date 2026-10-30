# Next Step Runbook — Phase 1

## Objective
Prove local stability and close the gap between provider code and live verification. No unrelated features.

### A. Protect workspace
Run `git status --short --branch`; record HEAD; inspect modified/untracked files; do not reset/clean; confirm secrets/DB ignored without printing values.

### B. Baseline
Backend: `cd backend`; `python -m pip install -e ".[dev]"`; `python -m pytest -q`; `ruff check app tests`. Frontend: `cd frontend`; `npm ci`; `npm test`; `npm run typecheck`; `npm run build`. Record exact exit codes and counts.

### C. Audit files
Inspect `main.py`, `config.py`, `database.py`, `twitch_auth.py`, `eventsub.py`, `twitch_chat.py`, `challenges.py`, `voting.py`, `challenge_clock.py`, `overlay.py`, and frontend `api.ts`, `App.tsx`, `Overlay.tsx`. Produce requirement/code/test/result table.

### D. Twitch official-contract audit
Verify authorization-code flow, state, callback, scopes, token validation/refresh rotation/revocation, chat read EventSub token identity, subscription types/versions/conditions, WebSocket welcome/keepalive/reconnect/revocation, send-chat sender/broadcaster IDs, permissions and rate limits. Do not change scopes without evidence.

### E. Gemini audit
Verify current model availability for the configured project, `generateContent` structured output compatibility with installed SDK/Pydantic schema, timeout, quota and retry behaviour. Keep tests mocked.

### F. Add missing mock tests
At minimum: refresh rotation and invalid refresh; OAuth denied/expired/replay; EventSub reconnect/no duplicate subscriptions/keepalive/revocation; send-chat success and 401/403/429/5xx; Gemini 429/5xx/empty/invalid schema; broadcaster isolation; overlay token rotation; timer close sends one announcement attempt.

### G. Live test (owner approval required)
Confirm channel and separate viewer; authorize; receive harmless message; delete/clear; request Gemini suggestion; approve/launch only with consent; vote/change vote; verify overlay; timer close; verify one announcement; reconnect overlay. No live post or challenge without explicit approval.

### H. Exit
Update `PROGRESS.md` with commands, counts, evidence and blockers; update API/TDD/ADRs for contract changes. Distinguish observed from inferred. Stop before deployment/publication absent approval.
