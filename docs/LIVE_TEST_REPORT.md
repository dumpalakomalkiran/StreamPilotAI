# StreamPilot AI — Live Synthetic E2E Report

- Run: `20261031T231907Z`
- Started (UTC): `2026-10-31T23:19:07.727168+00:00`
- Finished (UTC): `2026-10-31T23:19:07.731969+00:00`
- Target identity gate: `False`
- Messages sent: **0**
- Gemini calls: **0**; tokens in/out: **0 / 0**
- Estimated cost (USD): **None** (configurable estimate; see `config/gemini_pricing.json`)
- Stop reason: offline_report_only

| Scenario | Status | Key evidence | Latency |
|---|---|---|---:|
| S0 Baseline live EventSub message flow | **BLOCKED** | Offline mode deliberately makes no Twitch, Gemini, or app API requests. | — |
| S1 Authentication, overlay and broadcaster isolation | **BLOCKED** | No live scenario was run; this mode performs no network or account action. | — |
| S2 Question detection, grouping, lifecycle and fallback | **BLOCKED** | No live scenario was run; this mode performs no network or account action. | — |
| S3 Grounded AI reply and explicit send | **BLOCKED** | No live scenario was run; this mode performs no network or account action. | — |
| S4 Poll suggestion, approved challenge, votes and timer | **BLOCKED** | No live scenario was run; this mode performs no network or account action. | — |
| S5 Prompt injection, malformed input and rendering safety | **BLOCKED** | No live scenario was run; this mode performs no network or account action. | — |
| S6 Flag-only moderation review | **BLOCKED** | No live scenario was run; this mode performs no network or account action. | — |
| S7 Message deletion and chat clear regression guard | **BLOCKED** | No live scenario was run; this mode performs no network or account action. | — |
| S8 Session end, aggregate recap and caching | **BLOCKED** | No live scenario was run; this mode performs no network or account action. | — |
| S9 Restart, token rotation and Gemini degradation | **BLOCKED** | No live scenario was run; this mode performs no network or account action. | — |
| S10 Optional native Twitch polls | **BLOCKED** | No live scenario was run; this mode performs no network or account action. | — |
| S11 Dashboard Playwright screenshots and console errors | **NOT BUILT** | Automated screenshot capture is not implemented in the harness. | — |

## Bugs found and fixed

No product defects were assessed in this blocked/offline run.

## Open issues / blocked checks

- Live run deferred by the owner; no provider/API requests or chat actions were made.

## What this does NOT prove

This run does not prove behavior under high-volume chat, representative viewer slang, provider load, or missed-event recovery. A Twitch subscription marked enabled is not proof of EventSub delivery; only a received canary establishes that for this run. Synthetic accounts and a curated repository dataset do not represent real viewers. No result is inferred from credentials alone.

## Safety and artifacts

Only messages stored in `data/live_test_script.jsonl` are designated for a future driver. Secrets, tokens, headers, raw provider bodies, and local viewer token files are excluded from reports. Live mode currently fails closed because the scenario driver and evidence capture are incomplete.
