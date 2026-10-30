# High-Level Document — StreamPilot AI

## Summary
StreamPilot AI turns Twitch chat into streamer-approved audience challenges. The streamer requests a Gemini suggestion, reviews and approves it, viewers vote in Twitch chat, and an OBS browser overlay displays live results.

## Goals
Audience participation without distracting the streamer; explicit streamer control; chat voting; real-time overlay; bounded/cost-conscious AI use; reliable demo and test coverage.

## Non-goals
Game automation, automatic AI publishing, software-executed game commands, Twitch Extension, payments, multi-tenant billing, and LLM calls for every chat message.

## Journey
Broadcaster OAuth → EventSub chat → streamer requests suggestion → Gemini structured output → Pydantic validation → streamer approval → launch → viewer `!vote N` → backend validates and persists latest vote → overlay updates → timer closes → result stored and announcement attempted → history.

## Functional requirements
FR-01 health/status; FR-02 OAuth; FR-03 bounded chat; FR-04 remove moderated messages from future context; FR-05 explicit Gemini suggestion (2–4 options, 15–60 sec); FR-06 labelled fallback; FR-07 approval required; FR-08 one active challenge; FR-09 validate chat votes; FR-10 one current vote per viewer; FR-11 overlay/reconnect; FR-12 persist result and announce once; FR-13 distinguish demo/live.

## Success criteria
A controlled authorized channel demonstrates OAuth, real chat receipt, message deletion, Gemini result, approval, launch, viewer vote, overlay update, timer closure, persisted result and one announcement. A mock test or enabled subscription alone does not meet this criterion.
