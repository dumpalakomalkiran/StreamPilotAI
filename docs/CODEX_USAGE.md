# Codex usage log

This log summarizes Codex's contribution from the repository history and the project sessions recorded in `docs/PROGRESS.md`. Product requirements and live-action boundaries came from the project owner. Codex worked in the existing StreamPilot repository and extended its React/Vite frontend and FastAPI/SQLAlchemy backend; it did not replace the project with a new scaffold.

## Designed, implemented, and tested with Codex

- **Repository review and safety groundwork:** Codex inspected the starter application and documented its architecture, setup, test status, and limitations. It implemented live-mode defaults, SQLite WAL/busy timeout, stream session lifecycle, serialized Twitch token refresh, and configurable Gemini model settings (`4b77ec5`).
- **EventSub robustness regression:** Codex reviewed normal chat, vote, delete, and clear paths after moderation-event handling changed. It hardened malformed/unknown EventSub notification handling so invalid payloads are logged and skipped instead of escaping the worker, while preserving normal chat processing. It added mocked coverage for ordinary messages, duplicates, deletions, clears, malformed events, and unknown types (`b52223c`).
- **Smart Interaction Queue:** Codex implemented deterministic English/Hinglish detection, fuzzy grouping, bounded batched Gemini grouping, explainable ranking, lifecycle APIs, broadcaster-scoped WebSocket delivery, dashboard UI, and an optional featured-question overlay. It added backend and frontend tests (`2a393a4`, `e2edfbf`).
- **Streamer-controlled AI features:** Codex added grounded reply drafts with explicit send/audit boundaries, audience poll suggestions reusing the existing challenge/vote flow, optional native Twitch polls behind opt-in authorization, session analytics and recap, and flag-only moderation. Mocked provider and tenant-isolation tests were added for these paths (`80880ed`, `4847932`, `bd971f5`, `3149b15`, `8be77bb`).
- **Demo and evaluation support:** Codex added the timed real-channel demo script, development-only synthetic replay through the EventSub notification processor, the synthetic database marker, labeled evaluation data/script, prompt-injection tests, and a manual integration health check (`56cdb6d`).

## Verification and boundaries

Codex ran backend pytest and Ruff, frontend Vitest tests, TypeScript typecheck, Vite production build, Python compilation, queue evaluation, and a safe check that replay refuses `APP_ENV=production`. The latest local results are recorded in `docs/PROGRESS.md`. Provider calls in automated tests are mocked. The manual health check was not run because it refreshes and rotates the stored Twitch token and sends a small Gemini request. Codex did not claim live integration verification, publish a deployment, or send real chat or launch a challenge as part of the latest demo/evaluation task.

## Relevant repository history

- `b52223c` — `fix: skip malformed EventSub notifications safely`
- `4b77ec5` — `feat: harden live mode and stream lifecycle`
- `2a393a4` — `Build broadcaster-scoped smart interaction queue`
- `e2edfbf` — `Add realtime smart interaction queue UI`
- `80880ed` — `Add grounded AI reply suggestions`
- `4847932` — `Add AI audience poll suggestions`
- `bd971f5` — `Add community intelligence insights`
- `3149b15` — `Add flag-only moderation assistant`
- `8be77bb` — `Add opt-in native Twitch polls`
- `56cdb6d` — `Add demo replay and queue evaluation tools`
