# Project Checklist — StreamPilot AI

## Repository and setup
- [ ] New project created within the permitted hackathon period.
- [ ] Codex read `AGENTS.md` and project docs.
- [ ] Python, Node/npm, Git versions recorded.
- [ ] `.env.example` contains placeholders only.
- [ ] `.gitignore` excludes secrets, DB files, caches, and builds.
- [ ] Backend/frontend install and start.

## Product
- [ ] Overview, Live Studio, Overlay Setup, Activity, Settings.
- [ ] Demo mode clearly labeled.
- [ ] Challenge suggestion can be reviewed/edited.
- [ ] Streamer approval mandatory.
- [ ] Timer and end action work.
- [ ] Vote totals correct and persisted.
- [ ] Overlay reconnects and displays final result.
- [ ] Activity history persists.

## Gemini
- [ ] Key stored backend-only.
- [ ] Model availability and quota checked.
- [ ] Structured output implemented using current SDK.
- [ ] Pydantic validation enforces options and duration.
- [ ] Bounded chat context treated as untrusted.
- [ ] Timeout, rate limits, fallback tested.
- [ ] Mock tests do not consume quota.
- [ ] Real API smoke test passed if configured.

## Twitch
- [ ] App registered and callback URI exact.
- [ ] OAuth state tested and minimum scopes selected.
- [ ] Broadcaster authorization succeeds.
- [ ] Real chat received.
- [ ] Reconnect/revocation tested.
- [ ] Viewer vote commands work.
- [ ] Chat announcement works with documented permissions.
- [ ] No credentials in logs, URLs, frontend, or Git.

## QA and security
- [ ] Backend tests pass.
- [ ] Frontend tests pass.
- [ ] Typecheck and production build pass.
- [ ] Concurrency/duplicate-event cases tested.
- [ ] Unauthorized control and invalid overlay tokens rejected.
- [ ] Mobile and error states checked.
- [ ] Clean setup instructions tested.

## Submission
- [ ] Official rules and deadline confirmed.
- [ ] Public repository verified.
- [ ] Public demo video under 3 minutes.
- [ ] Video shows actual Twitch functionality and Codex development.
- [ ] Screenshots captured.
- [ ] Primary Codex session ID recorded using supported `/status`.
- [ ] Devpost fields complete and accurate.
- [ ] Final status says Submitted, not Draft.
- [ ] Submission confirmation saved.
