# StreamPilot AI — Implementation Overview

This document explains what StreamPilot AI currently does in everyday language. It is intended for a streamer, reviewer, or teammate who wants to understand the product without reading the source code.

## What StreamPilot AI is

StreamPilot AI is a Twitch companion for busy streamers. It watches incoming chat, identifies questions and useful requests, groups repeated questions together, and presents a short queue for the streamer. Google Gemini helps with selected, batched tasks, but it never controls the channel by itself. The streamer remains the decision-maker for replies, challenges, and other consequential actions; the former poll panels are not part of the current dashboard.

## The main experience

1. The streamer connects a Twitch account through OAuth.
2. Twitch EventSub delivers chat events to the backend.
3. The app stores the chat message and shows it in the dashboard's raw-chat view.
4. A deterministic filter ignores commands, emote-only lines, very short noise, bots, and the broadcaster's own messages.
5. Questions and requests are grouped when several viewers ask the same thing in different words.
6. The Smart Queue ranks groups so the streamer can focus on the most repeated and recent topics.
7. The streamer can answer, snooze, or dismiss a question. Overlay featuring is currently hidden pending controlled OBS verification.
8. If desired, the streamer explicitly asks Gemini for a reply draft, edits it, and explicitly sends it.
9. The streamer reviews and launches the single Challenge suggestion editor. Voting uses the existing StreamPilot `!vote N` flow.
10. At stream end, the app records the end time and can create a cached recap from aggregate data.

## What has been implemented

### Dashboard and overlay

- React, TypeScript, Vite dashboard with a dark streamer-focused design.
- Raw live-chat panel and Smart Interaction Queue shown together.
- Queue filters for status, kind, and text search.
- Queue cards show the question, type, viewer count, priority, recency, and score explanation.
- Keyboard shortcuts: `A` answer, `S` snooze, and `D` dismiss the focused card.
- Connection status and automatic REST refresh after a WebSocket reconnect.
- Protected OBS browser overlay code exists for challenge results and an optional “Question of the moment,” but the dashboard feature control is currently hidden pending verification.
- Overlay data is deliberately limited: it does not expose private member messages or moderation details.

### Twitch connection and chat

- Authorization-code OAuth with state validation and broadcaster identity checks.
- Encrypted Twitch tokens stored in the database and refreshed safely.
- EventSub WebSocket handling for chat messages, single-message deletion, per-user clear, and full chat clear.
- Stream online/offline handling and session lifecycle tracking.
- Narrow `!vote N` parsing with vote replacement, late-vote rejection, duplicate-event protection, and timer closure.
- Twitch chat-send support is available only through an explicit streamer action.

### Smart Interaction Queue

- `InteractionGroup` and `Interaction` database records scoped to a broadcaster and session.
- English and Hinglish question/request detection.
- Normalisation removes URLs, punctuation, emote noise, and repeated characters before matching.
- Fuzzy matching groups similar questions without requiring an AI call for every message.
- A periodic, bounded Gemini call can group remaining candidates in one batch.
- Invalid, slow, unavailable, or unconfigured Gemini responses are rejected safely; deterministic grouping continues.
- Explainable priority components are stored so the UI can show why an item is high priority.
- Answer, dismiss, snooze, automatic reopen, and new-asker reopen lifecycle rules.
- Moderated or deleted messages are removed from future suggestion context.

### AI reply suggestions

- Stream facts/settings let the streamer provide safe information such as PC specifications, schedule, and social links.
- Gemini receives the canonical question, up to five sample messages, and the stream facts as clearly delimited data.
- Suggestions are short, structured, validated, and limited in length.
- When the facts do not support an answer, the app uses a rotating safe holding reply instead of inventing facts, so repeated requests are not shown the exact same sentence every time.
- Drafts can be edited, copied, discarded, or sent.
- Only the explicit “Send to chat” action can call Twitch chat-send.
- Successful sends create an audit record and mark the queue item answered.
- AI usage telemetry records model, latency, token counts when available, and failure class without storing secrets or full prompts.

### Challenge voting (supported workflow)

- The dashboard provides one explicit Challenge suggestion editor with editable title, question, options, and duration.
- After approval and launch, viewers vote with `!vote N`; the existing timer, vote replacement, late-vote rejection, overlay events, and persisted result are reused.
- Earlier AI poll-suggestion and native Twitch poll implementations remain as backend compatibility code, but their dashboard controls were removed because they duplicated the challenge workflow and the demo channel is not eligible for native polls.
- The current dashboard does not automatically suggest, approve, launch, or create a poll.

### Community insights and recap

- Live analytics include message count, unique chatters, detected questions, queue status counts, time-to-answer, top open groups, poll outcomes, and messages per minute.
- A post-stream summary is generated from aggregate data and top topics rather than the entire raw chat log.
- Recaps contain a headline, topics, answered/unanswered questions, follow-up items, an observational mood sentence, and content ideas.
- Summaries are cached and only regenerated when requested.

### Flag-only moderation assistant

- Deterministic flags detect repeated spam, excessive caps/emotes, and links from first-time chatters.
- An optional batched Gemini pass can classify borderline incidents.
- Incidents are review suggestions only. StreamPilot never bans, times out, or deletes messages automatically.
- Moderation incidents and status changes are tenant-scoped and audited.
- Moderation information is kept out of the public overlay.

### Development, evaluation, and live-test tooling

- A development-only synthetic chat replay uses the same EventSub ingestion path and labels replayed data.
- A labeled evaluation set measures question detection and grouping purity.
- Prompt-injection examples verify that hostile chat text stays data and cannot trigger actions.
- A health-check script can check configuration, database writes, Twitch authorization/EventSub state, and Gemini reachability.
- A fail-closed live E2E driver supports bounded scenarios, channel allowlisting, message/Gemini/time limits, rate limiting, redacted reports, and explicit approval steps.
- Offline reports and live-test notes distinguish PASS, FAIL, BLOCKED, and NOT BUILT instead of claiming success from configuration alone.

## Safety in simple terms

- The streamer must approve every action that changes the channel.
- Chat is treated as untrusted text. A message saying “ignore previous instructions” is still only a message.
- Gemini cannot call tools, send chat, launch challenges, create polls, or moderate users.
- AI output must match a strict schema before the app uses it.
- Provider failures fall back to deterministic behavior or a safe holding response.
- Secrets stay on the backend and are excluded from reports and source control.
- Every database query is scoped to the authenticated broadcaster.
- Demo/replay data is labeled so it cannot be mistaken for real Twitch activity.

## Verification status

Local automated verification is strong: the backend test suite, Ruff, frontend tests, TypeScript checks, and production build have been run successfully in the documented clean dependency environment. The repository also contains mocked tests for failure paths and security rules.

The complete live Twitch + Gemini path is not yet certified. The live rehearsal has observed some real chat delivery, but queue grouping, chat-send edge cases, session recap timing, deletion/clear handling, and overlay behavior still need a clean owner-run pass. See [PROGRESS.md](PROGRESS.md) and [LIVE_TEST_REPORT.md](LIVE_TEST_REPORT.md) for the evidence and blockers.

## Current limitations

- Gemini quality depends on the configured model, provider availability, language, and chat style.
- Fuzzy grouping can miss questions that are semantically similar but use very different wording.
- EventSub does not replay messages missed during a disconnect.
- The current live driver requires a fresh session, authorized viewer accounts, a current dashboard session cookie, and manual approval for destructive or public actions.
- Legacy poll endpoints and the protected overlay are not current supported dashboard workflows and remain live-verification-gated.
- Multi-platform chat, automatic moderation, game control, payments, and billing are outside the current product.

## Where to look next

- Setup and product pitch: [README.md](../README.md)
- Build history and evidence: [PROGRESS.md](PROGRESS.md)
- API examples: [API_REFERENCE.md](API_REFERENCE.md)
- Technical design: [TECHNICAL_DESIGN_DOCUMENT.md](TECHNICAL_DESIGN_DOCUMENT.md)
- Test coverage: [TEST_CASES.md](TEST_CASES.md)
- Live-test instructions: [LIVE_TEST_SETUP.md](LIVE_TEST_SETUP.md)

