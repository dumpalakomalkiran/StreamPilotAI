# Build Progress — Reviewed Baseline

## Review metadata
Repository `dumpalakomalkiran/StreamPilotAI`; branch `main`; reviewed commit `8c3d08f373388dd5b63d5cc1c81cdf78768ed1d4`; review date 2026-10-31. This is a source review, not a fresh local test run. Re-run all checks in the active Codex workspace.

## Implemented in source (not necessarily live verified)
- [x] React/TypeScript/Vite dashboard and OBS overlay.
- [x] FastAPI routes, Pydantic schemas, SQLAlchemy models and SQLite config.
- [x] Challenge suggest/create/approve/launch/end/history.
- [x] One-active-challenge index; vote replacement, event dedupe and timer closure.
- [x] Gemini structured-output adapter, validation, bounded untrusted context, timeout and labelled fallback.
- [x] Twitch authorization-code flow, state validation, identity lookup, encrypted token storage and refresh.
- [x] EventSub chat message, single delete, per-user clear and full clear handlers.
- [x] Bounded recent chat and deletion tombstones.
- [x] Narrow `!vote N` handling; broadcaster messages excluded from votes.
- [x] Protected overlay token and WebSocket broadcasts.
- [x] Backend/frontend automated test suites; Docker/Compose/Nginx files.

## Verification status
| Area | Evidence in repository | Current conclusion |
|---|---|---|
| Backend/frontend | Test scripts and tests exist | Must rerun in current workspace |
| Gemini | Mock tests; progress notes report 504 then 503 | Real generation unverified |
| Twitch OAuth | Mock tests; previous key rotation invalidated stored ciphertext | Reauthorization/current state must be checked |
| EventSub | Code/tests; prior notes say subscriptions accepted/enabled | Actual delivery unverified |
| Chat moderation | Handler/tests | Real moderation event unverified |
| Viewer vote/overlay | Code/tests | Full real path unverified |
| Chat announcement | Send adapter and timer call | Real permission/send unverified |
| Docker/deployment | Files exist | Clean Compose/deployment unverified |

## Historical blockers (do not assume still current)
Prior progress notes recorded Gemini HTTP 504 `DEADLINE_EXCEEDED`, then 503 `UNAVAILABLE`; Twitch token decryption failure after `APP_SECRET_KEY` replacement; EventSub subscriptions accepted but no observed notification delivery; Docker unavailable in a prior environment. Historical model names/configs are not authoritative. Never print `.env` values.

## Next task
Run `docs/NEXT_STEP_RUNBOOK.md` Phase 1. Establish local test baseline, verify provider contracts from current official docs, add missing mocked failure-path coverage, then seek owner approval for controlled live verification.

## Entry template
Date/commit; files changed; commands and exact counts; tests added; live actions performed; observed vs inferred; blockers; next task.

## Local verification — 2026-11-01
- Commit reviewed: `9f351b3` (`Docs Update with new features`); worktree was clean before verification.
- Backend dependency install: `python -m pip install -e ".[dev]"` completed successfully (all dependencies were already satisfied; editable package reinstalled).
- Backend tests: `python -m pytest -q` failed during collection with filesystem `OSError`/`ImportError` errors while pytest cache paths were inaccessible. Re-run as `python -m pytest -q -p no:cacheprovider`: **17 passed** in 3.02s.
- Backend lint: `ruff check app tests`: **passed** (`All checks passed!`).
- Frontend baseline: `npm test` failed because `vitest` was not found; `npm run typecheck` and `npm run build` failed because `tsc` was not found.
- Frontend dependency recovery: `npm ci` stalled after reporting one deprecated package; stopped after no further output. Retried with approved escalation and observed the same stall, then stopped. Frontend gates remain unverified, not code failures.
- No source changes or live Twitch/Gemini calls were made during this verification entry. Next: inspect git history and EventSub deletion/clear processing before the smallest regression fix.

## EventSub regression review — 2026-11-01
- Compared `8c3d08f^` with `8c3d08f`; that commit added four chat/moderation subscriptions, deletion tombstones and removal handlers. The normal `channel.chat.message` persistence and vote path remained in place.
- Hardened notification/frame parsing in `backend/app/services/eventsub.py`: malformed objects and unknown subscription/message types are logged and skipped; malformed frames no longer escape the receive loop; deletion/clear handlers validate payload shape.
- Updated `backend/tests/test_eventsub.py` to cover regular messages, duplicate message IDs with conflicting vote text, deletion, per-user clear, full chat clear, malformed input and unknown subscription types.
- Verification: `python -m pytest -q -p no:cacheprovider`: **18 passed**; `ruff check app tests`: **passed**.
- No live Twitch messages, challenge launch, or Gemini requests were made. EventSub delivery remains unverified live.
