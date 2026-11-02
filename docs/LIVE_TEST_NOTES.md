# Live test notes (historical observations)

These notes describe earlier rehearsal attempts. They are not a statement that the current dashboard exposes poll suggestions or that any provider path is fully verified. The current product snapshot is maintained in `PROGRESS.md`.

Updated: 2026-11-02

These are owner-guided manual observations from the step-by-step live rehearsal. They are supplemental to `LIVE_TEST_REPORT.md`, which records the harness run as offline/preflight-blocked.

## Observed checks

| Check | Observation | Status |
|---|---|---|
| Backend readiness | `/api/health` responded OK. `/api/status` reported `demo_mode=false`, Twitch authorized, and EventSub worker running. | PASS for readiness only; not a full live-path verification. |
| Canary chat ingestion | The viewer-sent `Synthetic StreamPilot test canary` was later found once in `backend/streampilot.db`. The first lookup returned zero, then a later lookup found it. Exact delivery latency and visibility in the dashboard were not captured. | PARTIAL |
| First Smart Queue question | `Can my RTX 3060 handle this game?` was found in stored chat and linked to an open question group. The group reported `asker_count=0` and `message_count=0` while one Interaction row referenced it. | FAIL — queue count inconsistency; investigate before relying on displayed counts. |
| Second Smart Queue question | `What settings would you use on a 3060 for steady frames?` was found, but it attached to a different group (group 2), which was already `answered`; that group now reports 1 asker / 3 messages. Its stored viewer hash matches the first question's hash. | FAIL / INCONCLUSIVE — it did not join the first question's group, and the stored identity does not show a distinct viewer despite the requested account switch. Confirm the actual account used and test grouping again after the baseline pass. |
| Third Smart Queue question | `Is DLSS worth turning on here?` was found, but attached to a new open group (group 3), not either earlier GPU group. Its group reports 0 askers / 0 messages despite one Interaction row; its viewer hash again matches the first question. | FAIL — repeated counter inconsistency and no same-topic grouping observed. Distinct-viewer behavior remains unverified. |
| Fourth Smart Queue question | `Which graphics preset keeps the game near 60 FPS?` was found, but attached to another new open group (group 4). It reports 0 askers / 0 messages despite one Interaction row, and its stored viewer hash matches the first question. | FAIL — no grouping across the GPU/settings phrasings; counter and distinct-viewer anomalies persist. |
| S4 ranked preference (first message) | `Let's queue ranked after this match!` was found in stored chat. No poll suggestion exists yet, as expected before competing preferences/viewer threshold. | IN PROGRESS — waiting for distinct viewers with an opposing preference. |
| S4 casual preference (second message) | `Can we keep it casual tonight instead?` was found in the same session, but its sender ID is the broadcaster's ID. The ranked message was from a non-broadcaster. Neither message has an Interaction row and no poll suggestion exists. | BLOCKED — broadcaster messages do not count as viewer preferences; repeat this step from a non-broadcaster viewer account. |
| S4 casual preference retry | Latest casual message was found from a non-broadcaster, with a different Twitch chatter ID than the ranked message. The casual message has an Interaction row; the ranked message currently has none. No poll suggestion exists. | PARTIAL — distinct viewers and casual ingestion confirmed, but the ranked message is not present in the poll candidate interactions and the poll has not formed. |
| AI reply and explicit send | Owner reports clicking Suggest reply and sending the answer. Database shows one AI response in `sent` status, an audit row, Twitch message ID recorded, and its group is `answered` with 1 asker / 2 messages. The outgoing Twitch message was not found in stored EventSub chat at the time checked. | PARTIAL — explicit-send/audit lifecycle is evidenced; EventSub echo and exact chat latency remain unverified. |
| Harness live preflight | A run with `--confirm-live` stopped before sending anything. It reported zero messages and zero Gemini calls. At check time, the process lacked `LIVE_TEST_CHANNEL_LOGIN`, `LIVE_TEST_DASHBOARD_SESSION`, and `LIVE_TEST_MODE`; the viewer token file was absent. | BLOCKED — expected fail-closed behavior. |
| Local browser/frontend | The in-app browser showed the dashboard URL, but browser automation access was denied. The frontend port 5173 was not listening during the readiness check. | BLOCKED — dashboard UI could not be inspected. |
| Dashboard authentication logs | Local requests to `/api/chat/messages` returned 401 and `/ws/dashboard` returned 403. These are StreamPilot local authentication failures, not Twitch API responses. The screenshot showed the backend launched from the repository root; because config and SQLite URLs are relative to the working directory, a wrong working directory/database is a possible cause, not yet confirmed. | OPEN |

## Known issues to revisit after the first test pass

1. **Queue count drift:** one persisted interaction is attached to an open group whose asker/message counters remain zero. Verify the API payload and dashboard display, inspect the grouping transaction/update path, and add a regression test before changing code.
2. **Dashboard auth is not reaching the API/WebSocket:** local API calls return 401 and dashboard WebSocket handshakes return 403. Verify the `stream_pilot_session` cookie is present for the same browser host, and verify the backend was launched from `backend/` (or has the intended absolute `DATABASE_URL`) so it reads the database containing the broadcaster credential.
3. **Live harness prerequisites are not configured in its process:** channel allowlist and dashboard session variables are absent, `LIVE_TEST_MODE` is false, and `data/live_test_viewer_tokens.json` does not exist. Do not bypass these gates.
4. **Local dashboard unavailable to the test session:** frontend port 5173 was not listening and Chrome access was denied, so UI visibility and WebSocket status remain unverified.
5. **Canary evidence lacks timing/UI confirmation:** storage proves receipt eventually, but there is no measured EventSub latency and no confirmed dashboard raw-chat display.
6. **Sent reply EventSub echo:** the approved reply is recorded as sent and audited, but there is no matching `chat_messages.event_id` echo yet. Confirm whether Twitch delivers the broadcaster's own sent chat through the current EventSub subscription and whether the UI needs to update from the send response instead.

## Next checks, before fixes

Continue one scenario at a time, record the message and expected UI/API evidence, and stop when a scenario is blocked. Do not send more messages until the owner is ready. Keep the current queue count inconsistency open while collecting the remaining baseline observations; then fix the issues together with focused tests.

## S1 authentication and tenant-safety check — 2026-11-02

The backend was started locally on `127.0.0.1:8000` with `DEMO_MODE=false`. The unauthenticated management routes `/api/interactions`, `/api/chat/messages`, `/api/challenges`, and `POST /api/overlay/token` returned HTTP 401. WebSocket handshakes without credentials were rejected: `/ws/dashboard` returned HTTP 403 and `/ws/overlay` returned HTTP 403. The isolated synthetic tenant fixture created two broadcasters and exposed one group to broadcaster A with `other_broadcaster_leaked=false`.

Status: **PASS for local authentication and tenant-isolation checks**. This does not prove a browser session cookie is present, a live Twitch identity is authorized, or that an authenticated dashboard/overlay WebSocket remains connected; those require the owner’s current browser session and live credentials.

## Fixes applied — 2026-11-02

- **Queue count drift fixed:** `GET /api/interactions` now recomputes each returned group's asker and message counters from its Interaction rows before building the response. A regression test covers a group whose stored counters are zero while a member exists.
- **Old queue on a new session fixed:** `GET /api/interactions` now excludes ended sessions. A regression test confirms completed-session groups do not appear in the active queue.
- **Verification:** the focused lifecycle tests pass (**4 passed**) and Ruff passes. A backend restart and dashboard refresh are required before live confirmation.

The remaining observations are not yet classified as product defects: the failed grouping run used repeated viewer hashes and may have involved the broadcaster account or an account-switching problem; dashboard 401/403 responses require checking the cookie host and the backend database/working directory; the missing EventSub echo for a sent broadcaster reply requires a fresh live observation; and the live harness remains correctly fail-closed until its variables, viewer token file, and fresh usage log are configured.
