# High-Level Document (HLD) — StreamPilot AI

## Executive summary
StreamPilot AI turns live Twitch chat into streamer-approved audience challenges. Gemini proposes a structured interaction, viewers vote through Twitch chat, and a browser overlay shows live results.

## Problem
Fast-moving chat is difficult to follow while gaming. Suggestions are fragmented and often fail to become a structured audience activity.

## Goals
- Turn chat discussion into a clear challenge.
- Preserve streamer control through explicit approval.
- Accept votes through Twitch chat.
- Update an OBS browser overlay in real time.
- Limit AI calls and API cost.
- Provide a reliable, testable demo.

## Non-goals
No game automation, automatic unapproved publishing, Twitch Extension, payments, multi-tenant billing, or LLM call for every chat message.

## Personas
- Gaming streamer: wants engagement without losing focus.
- Viewer: wants an easy way to influence a decision.
- Judge: needs a verifiable working product and clear Codex use.

## User journey
1. Streamer authorizes Twitch.
2. App receives chat events.
3. Streamer requests a challenge suggestion.
4. Gemini returns structured output, validated by Pydantic.
5. Streamer edits and approves.
6. Overlay shows the challenge.
7. Viewers vote using `!vote N`.
8. Backend validates and persists the latest vote per user.
9. Timer closes voting; overlay shows result; one chat announcement is sent.
10. Activity view shows history.

## System components
React dashboard/overlay; FastAPI REST API; Twitch OAuth/EventSub/chat adapter; Gemini generation service; challenge lifecycle; vote service; WebSocket broadcaster; SQLAlchemy/SQLite persistence.

## Functional requirements
FR-01 connection status; FR-02 Twitch OAuth; FR-03 receive chat; FR-04 request AI suggestion; FR-05 2–4 options and 15–60 second duration; FR-06 approval required; FR-07 chat voting; FR-08 one current vote per user; FR-09 vote changes update totals; FR-10 reject late votes; FR-11 live overlay; FR-12 persist and announce results once; FR-13 clearly labeled demo mode.

## Non-functional requirements
Backend-only secrets, bounded inputs, safe errors, rate limits, OAuth state validation, authorization checks, reconnect handling, responsive accessible UI, deterministic tests, and documented setup.

## Risks
Twitch scopes/EventSub changes; Gemini quotas/latency; duplicate events; secret exposure; deadline pressure. Mitigate with early integration, mocked tests, bounded prompts, rate limits, and an end-to-end live test.

## Success criteria
A broadcaster authorizes Twitch, requests and approves a Gemini challenge, viewers vote in real chat, the overlay updates without refresh, the result is persisted and announced once, tests pass, and the demo proves the workflow.
