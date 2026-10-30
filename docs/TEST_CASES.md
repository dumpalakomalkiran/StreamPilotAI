# Test Cases — StreamPilot AI

Mock Gemini and Twitch in automated tests; tests must not require API quota or a real account.

| ID | Scenario | Expected result | Priority |
|---|---|---|---|
| TC-001 | Health endpoint | HTTP 200 and status `ok` | P0 |
| TC-002 | Gemini key missing | Demo mode works; AI shown unconfigured | P0 |
| TC-003 | Valid Gemini structured response | Schema validation succeeds | P0 |
| TC-004 | Malformed model output | Rejected or safe labeled fallback | P0 |
| TC-005 | Gemini timeout | Safe handling; no secrets/traceback | P0 |
| TC-006 | Gemini quota error | Safe error/fallback; app stays usable | P0 |
| TC-007 | Prompt injection in chat | Treated as untrusted content | P0 |
| TC-008 | Fewer than 2 options | Rejected | P0 |
| TC-009 | More than 4 options | Rejected | P0 |
| TC-010 | Duration outside 15–60 sec | Rejected | P0 |
| TC-011 | Launch without approval | Rejected | P0 |
| TC-012 | Approved challenge launched | Status becomes active | P0 |
| TC-013 | Vote with no active challenge | Rejected/ignored | P0 |
| TC-014 | Valid `!vote 1` | Vote persisted and totals update | P0 |
| TC-015 | Invalid option number | Rejected/ignored | P0 |
| TC-016 | Same user submits duplicate vote | Only one current vote counts | P0 |
| TC-017 | User changes vote | Previous vote replaced | P0 |
| TC-018 | Vote after closure | Rejected | P0 |
| TC-019 | Duplicate EventSub message | Not double-counted | P0 |
| TC-020 | Tie | Documented tie behavior applied | P1 |
| TC-021 | Timer expires | Challenge closes once | P0 |
| TC-022 | Duplicate closure event | Only one announcement | P0 |
| TC-023 | OAuth state mismatch | Callback rejected | P0 |
| TC-024 | Valid OAuth callback | Broadcaster identity resolved | P0 |
| TC-025 | Revoked/expired token | Reconnect-required status | P0 |
| TC-026 | EventSub reconnect | No duplicate subscriptions | P0 |
| TC-027 | Status endpoint | No keys/tokens exposed | P0 |
| TC-028 | Invalid overlay token | Access rejected | P0 |
| TC-029 | Vote event | Overlay receives updated totals | P0 |
| TC-030 | Overlay reconnect | Current challenge state restored | P1 |
| TC-031 | Dashboard renders | No frontend crash | P0 |
| TC-032 | Approve challenge | UI reflects action | P0 |
| TC-033 | API failure | Useful error feedback | P1 |
| TC-034 | Mobile viewport | Core actions remain usable | P1 |
| TC-035 | No credentials | Clearly labeled demo works | P0 |
| TC-036 | Production frontend build | Build succeeds | P0 |
| TC-037 | Clean README setup | Both apps start | P1 |
| TC-038 | Git secret scan | No secrets or `.env` tracked | P0 |
| TC-039 | Duplicate EventSub chat message | Stored once and vote counted once | P0 |
| TC-040 | OAuth state mismatch | Rejected before token exchange | P0 |
| TC-041 | OAuth token persistence | Tokens encrypted at rest and absent from API responses | P0 |
| TC-042 | Twitch deletes one received chat message | Matching stored row disappears from live chat and is excluded from the next Gemini context | P0 |
| TC-043 | Twitch clears one user's messages | All of that user's rows in the current session are removed; other users remain | P0 |
| TC-044 | Twitch clears the chat room | All stored messages for the current session are removed | P0 |
| TC-045 | Duplicate/replayed chat event after deletion | Deleted message remains absent from storage and Gemini context | P0 |
| TC-046 | OpenAPI / Swagger contract | HTTP operations include summaries, descriptions, bounded request schemas, and representative request/response examples | P1 |

## Manual end-to-end test
1. Configure Twitch app and Gemini key locally.
2. Start backend/frontend; authorize broadcaster.
3. Verify real chat messages arrive.
4. Request and approve a challenge.
5. Vote from two viewer accounts; change one vote.
6. Verify overlay updates without refresh.
7. Wait for closure and verify one announcement.
8. Verify history persistence and capture demo evidence.
