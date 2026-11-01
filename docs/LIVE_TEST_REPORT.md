# StreamPilot AI — Live Synthetic E2E Report

- Run: `20261101T055230Z`
- Started (UTC): `2026-11-01T05:52:30.244718+00:00`
- Finished (UTC): `2026-11-01T05:52:30.248239+00:00`
- Target identity gate: `False`
- Messages sent: **0**
- Gemini calls: **0**; tokens in/out: **0 / 0**
- Estimated cost (USD): **None** (configurable estimate; see `config/gemini_pricing.json`)
- Stop reason: offline_report_only

| Scenario | Status | Key evidence | Latency |
|---|---|---|---:|
| S0 Baseline live EventSub message flow | **BLOCKED** | Offline mode deliberately makes no Twitch, Gemini, or app API requests. | — |
| S2 Question detection, grouping and negative filtering | **BLOCKED** | Offline mode deliberately makes no Twitch, Gemini, or app API requests. | — |
| S3 Grounded AI reply and explicit send | **BLOCKED** | Offline mode deliberately makes no Twitch, Gemini, or app API requests. | — |
| S4 Poll suggestion, approved challenge, votes and timer | **BLOCKED** | Offline mode deliberately makes no Twitch, Gemini, or app API requests. | — |
| S5 Prompt injection, malformed input and rendering safety | **BLOCKED** | Offline mode deliberately makes no Twitch, Gemini, or app API requests. | — |
| S7 Message deletion and chat clear regression guard | **BLOCKED** | Offline mode deliberately makes no Twitch, Gemini, or app API requests. | — |
| S8 Session end, aggregate recap and caching | **BLOCKED** | Offline mode deliberately makes no Twitch, Gemini, or app API requests. | — |
| S1 Authentication, overlay and broadcaster isolation | **NOT RUN** | Skipped by the selected scenario plan. | — |
| S6 Flag-only moderation review | **NOT RUN** | Skipped by the selected scenario plan. | — |
| S9 Restart, token rotation and Gemini degradation | **NOT RUN** | Skipped by the selected scenario plan. | — |
| S10 Optional native Twitch polls | **NOT RUN** | Skipped by the selected scenario plan. | — |
| S11 Dashboard Playwright screenshots and console errors | **NOT RUN** | Skipped by the selected scenario plan. | — |

## Bugs found and fixed

No live product defects were assessed; this was an offline preparation run.

## Open issues / blocked checks

- Live run deferred by the owner; no provider/API requests or chat actions were made.

## What this does NOT prove

This run does not prove behavior under high-volume chat, representative viewer slang, provider load, or missed-event recovery. A Twitch subscription marked enabled is not proof of EventSub delivery; only a received canary establishes that for this run. Synthetic accounts and a curated repository dataset do not represent real viewers. No result is inferred from credentials alone.

## Safety and artifacts

Only messages stored in `data/live_test_script.jsonl` are designated for live sends. Secrets, tokens, headers, raw provider bodies, and local viewer token files are excluded from reports. This report was generated in offline mode; the live driver has not been run.
