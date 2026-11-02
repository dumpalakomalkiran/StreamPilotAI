# High-Level Document — StreamPilot AI

For a plain-language explanation of the implemented product, see [IMPLEMENTATION_OVERVIEW.md](IMPLEMENTATION_OVERVIEW.md). This file remains the concise requirements-oriented reference.

## Summary
StreamPilot AI turns Twitch chat into a streamer-controlled Smart Queue and challenge workspace. The streamer can review grouped viewer questions, request a grounded Gemini reply draft, or create a challenge that viewers answer with `!vote N`. AI never sends, launches, moderates, or changes the channel without an explicit streamer action.

## Goals
Audience participation without distracting the streamer; explicit streamer control; chat voting; real-time overlay; bounded/cost-conscious AI use; reliable demo and test coverage.

## Non-goals
Game automation, automatic AI publishing, software-executed game commands, Twitch Extension, payments, multi-tenant billing, and LLM calls for every chat message.

## Journey
Broadcaster OAuth → EventSub chat → deterministic filtering/grouping → Smart Queue → optional explicit Gemini draft → streamer review/action. For a challenge: edit suggestion → approve → launch → viewer `!vote N` → backend validates and persists latest vote → result updates → timer closes → result stored and announcement attempted → reloadable history.

## Functional requirements
FR-01 health/status; FR-02 OAuth; FR-03 bounded chat; FR-04 remove moderated messages from future context; FR-05 explicit Gemini challenge suggestion (2–4 options, 15–60 sec); FR-06 labelled fallback; FR-07 approval required; FR-08 one active challenge; FR-09 validate chat votes; FR-10 one current vote per viewer; FR-11 dashboard reconnect and persisted challenge-result restore; FR-12 persist result and announce once; FR-13 distinguish demo/live; FR-14 broadcaster-scoped Smart Queue; FR-15 explicit reply drafts and flag-only moderation.

## Success criteria
A controlled authorized channel demonstrates OAuth, real chat receipt, queue grouping, explicit Gemini request, approval, launch, viewer vote, persisted result after reload, and (if separately verified) overlay/timer/announcement behavior. A mock test, configured credential, or enabled subscription alone does not meet this criterion. The current dashboard does not expose the former AI/native poll panels.
