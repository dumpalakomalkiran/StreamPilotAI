# Test Cases — StreamPilot AI

Mock providers in automated tests; never consume quota or send real chat.

| ID | Scenario | Expected result |
|---|---|---|
| T01 | Health/status | 200; no secret leakage; verification flag honest |
| T02 | Gemini valid schema | Structured output validates; source=gemini |
| T03 | Gemini empty/malformed/429/5xx/timeout | Safe labelled fallback; bounded timeout |
| T04 | Chat prompt injection | Treated as untrusted data |
| T05 | Challenge bounds | Invalid option count/duration rejected |
| T06 | Launch unapproved | Rejected |
| T07 | Concurrent launch | One active challenge only |
| T08 | Vote replacement | One current vote per viewer |
| T09 | Duplicate/invalid/late/broadcaster vote | Not counted |
| T10 | Tie/no votes | No winning option |
| T11 | Concurrent close | One completion/announcement attempt |
| T12 | OAuth state missing/mismatch/expired/replay | Reject before exchange |
| T13 | OAuth denied/exchange/identity failure | Safe failure |
| T14 | Missing required scope | Authorization rejected |
| T15 | Token refresh/rotation | New access and refresh tokens encrypted |
| T16 | Invalid/revoked refresh | Session invalidated; reconnect required |
| T17 | EventSub welcome/keepalive | Session healthy; subscriptions correct |
| T18 | EventSub reconnect/revocation | Reconnect without duplicate subscription; revocation stops worker |
| T19 | Chat duplicate/delete/user clear/full clear | Idempotent removal; replay tombstone works |
| T20 | Chat send success/401/403/429/5xx | Response handled safely; no token leak |
| T21 | Broadcaster isolation | No cross-account/session data access |
| T22 | Overlay token rotate/reconnect | Old token invalid; current safe state restored |
| T23 | Frontend errors/demo-live | Useful errors; synthetic data labelled |
| T24 | Frontend typecheck/build | Pass |
| T25 | Compose/secret scan | Clean build, DB persistence, WebSocket proxy, no secrets tracked |
| T26 | Interaction detection | English/Hinglish questions and requests accepted; commands/emote-only/broadcaster/bot messages ignored |
| T27 | Interaction grouping | Six GPU questions with varied phrasing join one same-session group; distinct asker/message counts are correct |
| T28 | Gemini batch grouping | Mocked valid schema attaches to group; invalid schema and timeout use deterministic fallback; no per-message model call |
| T29 | Interaction priority | Explainable asker/recency/repetition components order groups deterministically |
| T30 | Interaction lifecycle | Answer, dismiss, snooze expiry and new-asker reopen behave as configured |
| T31 | Interaction tenant/privacy | List/detail/actions are broadcaster-scoped; moderated chat deletes its queue item |

## Owner-approved controlled live test
Confirm channel/viewer accounts and approve any post/challenge. Test OAuth; harmless viewer chat; delete message; Gemini suggestion; approve/launch; viewer vote and vote change; overlay update; timer closure; one result announcement; overlay reconnect. Record only safe observations, never credentials or chat text. Enabled subscription is not delivery proof.
