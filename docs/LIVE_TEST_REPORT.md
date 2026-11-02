# StreamPilot AI — Live Synthetic E2E Report (historical run)

This report records a prior fail-closed rehearsal and is not a current verification certificate. Poll-suggestion/native-poll rows refer to backend compatibility scenarios; those controls are hidden from the current dashboard.

- Run: `20261102T112413Z`
- Started (UTC): `2026-11-02T11:24:13.373092+00:00`
- Finished (UTC): `2026-11-02T11:24:19.318299+00:00`
- Target identity gate: `False`
- Messages sent: **0**
- Gemini calls: **0**; tokens in/out: **0 / 0**
- Estimated cost (USD): **0.0** (configurable estimate; see `config/gemini_pricing.json`)
- Stop reason: preflight_blocked

| Scenario | Status | Key evidence | Latency |
|---|---|---|---:|
| S1 Authentication, overlay and broadcaster isolation | **NOT RUN** | Explicitly deferred by this focused demo run. | — |
| S6 Flag-only moderation review | **NOT RUN** | Explicitly deferred by this focused demo run. | — |
| S9 Restart, token rotation and Gemini degradation | **NOT RUN** | Explicitly deferred by this focused demo run. | — |
| S10 Optional native Twitch polls | **NOT RUN** | Explicitly deferred by this focused demo run. | — |
| S11 Dashboard Playwright screenshots and console errors | **NOT RUN** | Explicitly deferred by this focused demo run. | — |
| S0 Baseline live EventSub message flow | **BLOCKED** | LIVE_TEST_CHANNEL_LOGIN does not match the connected broadcaster; no chat will be sent. | — |
| S2 Question detection, grouping and negative filtering | **BLOCKED** | LIVE_TEST_CHANNEL_LOGIN does not match the connected broadcaster; no chat will be sent. | — |
| S3 Grounded AI reply and explicit send | **BLOCKED** | LIVE_TEST_CHANNEL_LOGIN does not match the connected broadcaster; no chat will be sent. | — |
| S4 Poll suggestion, approved challenge, votes and timer | **BLOCKED** | LIVE_TEST_CHANNEL_LOGIN does not match the connected broadcaster; no chat will be sent. | — |
| S5 Prompt injection, malformed input and rendering safety | **BLOCKED** | LIVE_TEST_CHANNEL_LOGIN does not match the connected broadcaster; no chat will be sent. | — |
| S7 Message deletion and chat clear regression guard | **BLOCKED** | LIVE_TEST_CHANNEL_LOGIN does not match the connected broadcaster; no chat will be sent. | — |
| S8 Session end, aggregate recap and caching | **BLOCKED** | LIVE_TEST_CHANNEL_LOGIN does not match the connected broadcaster; no chat will be sent. | — |

## Bugs found and fixed

No live product defects were assessed; this was an offline preparation run.

## Open issues / blocked checks

- LIVE_TEST_CHANNEL_LOGIN does not match the connected broadcaster; no chat will be sent.

## What this does NOT prove

This run does not prove behavior under high-volume chat, representative viewer slang, provider load, or missed-event recovery. A Twitch subscription marked enabled is not proof of EventSub delivery; only a received canary establishes that for this run. Synthetic accounts and a curated repository dataset do not represent real viewers. No result is inferred from credentials alone.

## Safety and artifacts

Only messages stored in `data/live_test_script.jsonl` are designated for live sends. Secrets, tokens, headers, raw provider bodies, and local viewer token files are excluded from reports. This report was generated in offline mode; the live driver has not been run.
