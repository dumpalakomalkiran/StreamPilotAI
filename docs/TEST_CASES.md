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
| T32 | Dashboard WebSocket | Cookie authentication and broadcaster-only event delivery; reconnect uses REST snapshot |
| T33 | Smart Queue UI | Mocked REST/WebSocket render, filter, connection indicator, answer/snooze/dismiss/feature actions and keyboard shortcuts |
| T34 | Featured overlay question | Only canonical question/count display; reconnect restores open feature; answer/dismiss/moderation hides it |
| T35 | Reply suggestion | Explicitly requested Gemini call uses structured output, canonical text, <=5 sample messages and streamer facts; invalid output/provider timeout uses safe holding draft |
| T36 | Reply grounding | Missing stream facts do not call Gemini and produce holding text; unsupported model answer cannot be used as a factual response |
| T37 | Reply editing/discard | Broadcaster can edit 1–500 characters or discard; neither action sends a Twitch message |
| T38 | Explicit reply send | Only POST `/api/responses/{id}/send` calls Helix; success audits actor/time/text/message ID and answers the queue group; failures preserve draft/open state |
| T39 | Reply send bounds/isolation | Over-500 text rejected; other broadcaster cannot read, edit, or send a suggestion |
| T40 | Reply UI | Suggest, edit, copy, explicit send and discard display; errors inline; no send occurs before the button action |
| T41 | Poll disagreement detection | One mocked Gemini batch creates a poll for at least three distinct viewers across at least two competing choices; agreement and fewer than three viewers create none |
| T42 | Poll safeguards | Cooldown, active challenge, and a pending suggestion suppress a new poll while grouping remains available |
| T43 | Poll lifecycle and scoping | Broadcaster can list/dismiss; edited approval creates an existing challenge draft, then existing approve/launch/vote path counts option five; foreign broadcaster gets 404 |
| T44 | Poll UI and results | UI renders supporting snippets, editable options/duration, count badge and actions; completion view uses existing challenge tallies |
| T45 | Analytics fixture | Live counts, unique chatters, question groups, answer duration, poll decisions, top items, and exactly 30 minute buckets are computed from fixture rows |
| T46 | Recap generation and cache | Mock structured Gemini output is validated; default repeat returns cache; explicit regenerate may issue one new call |
| T47 | Empty recap | Empty session receives deterministic zero-activity recap without calling Gemini |
| T48 | Session end and recap | End closes only the authenticated broadcaster's active session, responds pending, schedules recap; foreign session is 404 |
| T49 | Moderation deterministic flags | Four identical posts within 20 seconds, excessive caps, repeated emotes, and new-account links are flagged; returning-user links and ordinary messages are not |
| T50 | Moderation Gemini batch | Borderline chat is classified in the existing queue/poll Gemini call; valid output can add a flag, invalid schema is ignored, and no per-message calls occur |
| T51 | Moderation review audit | Dismiss/review transitions are tenant-scoped, reject repeat transitions, and create one AuditLog entry per status change |
| T52 | Moderation safety boundaries | Moderation code has no Twitch action dependency; incident creation publishes only to broadcaster dashboard WebSocket and never to either overlay socket |
| T53 | Moderation UI | Review list shows the safety banner, message context, category/severity/reason and streamer-controlled actions |

## Owner-approved controlled live test
Confirm channel/viewer accounts and approve any post/challenge. Test OAuth; harmless viewer chat; delete message; Gemini suggestion; approve/launch; viewer vote and vote change; overlay update; timer closure; one result announcement; overlay reconnect. Record only safe observations, never credentials or chat text. Enabled subscription is not delivery proof.
| T54 | Optional native Twitch polls | Poll scope is absent by default and requested only after settings opt-in/re-authorization; mocked Helix creation success, Affiliate/Partner 403 with `!vote N` fallback, 429, active Twitch/StreamPilot conflict and missing-scope response; tenant-scoped tracking/end |
