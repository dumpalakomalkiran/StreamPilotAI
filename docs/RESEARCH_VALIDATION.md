# Provider Research Validation — Official Sources

## Provenance
The refreshed Deep Research response referenced by the owner was not available in the chat or repository during this review. This is a verification checklist, not a claim that the missing report was incorporated. Codex must compare current code with official sources and record checked date and outcome before changing provider code.

## Twitch
- Authentication: https://dev.twitch.tv/docs/authentication/
- Token validation: https://dev.twitch.tv/docs/authentication/validate-tokens/
- Scopes: https://dev.twitch.tv/docs/authentication/scopes/
- Chat: https://dev.twitch.tv/docs/chat/
- Chat authentication: https://dev.twitch.tv/docs/chat/authenticating/
- Send chat: https://dev.twitch.tv/docs/api/reference/#send-chat-message
- EventSub WebSocket: https://dev.twitch.tv/docs/eventsub/handling-websocket-events/
- Subscription types: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types/

Verify whether `user:read:chat`/`user:write:chat` remain correct; token type and user identity for each chat subscription; sender/broadcaster identity and permission for send-chat; token validation cadence and refresh-token rotation; reconnect URL/session transfer and subscription recreation; rate-limit/retry guidance; logout/revocation.

## Gemini
- API: https://ai.google.dev/gemini-api/docs
- Structured output: https://ai.google.dev/gemini-api/docs/structured-output
- Models: https://ai.google.dev/gemini-api/docs/models
- Rate limits: https://ai.google.dev/gemini-api/docs/rate-limits
- Troubleshooting: https://ai.google.dev/gemini-api/docs/troubleshooting

Verify selected model availability, structured schema support, installed `google-genai` call shape, timeout/retry/quota handling, and key/data controls.

## Evidence format
For each item record official URL, date checked, requirement, code location, pass/fail/unclear, required change and test added. Never include keys, tokens, private chat or account-specific secrets.
