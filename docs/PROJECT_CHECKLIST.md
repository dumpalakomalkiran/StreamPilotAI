# Project Checklist

## Baseline
- [ ] Branch/commit and working tree inspected; local changes preserved.
- [ ] `.env.example` placeholders only; `.gitignore` excludes secrets/DB/builds.
- [ ] Python/Node/npm versions recorded.
- [ ] Backend pytest/Ruff pass.
- [ ] Frontend tests/typecheck/build pass.

## Existing feature regression
- [ ] Dashboard and demo/live distinction.
- [ ] Challenge suggest/create/approve/launch/end/history.
- [ ] One active challenge; vote dedupe/replacement/deadline.
- [ ] Gemini schema and fallback.
- [ ] OAuth state/encryption/refresh.
- [ ] EventSub chat/moderation and tombstones.
- [ ] Overlay token, broadcast and reconnect.

## Provider/live
- [ ] Twitch scopes/token identity/subscription conditions verified from official docs.
- [ ] OAuth refresh/revocation verified.
- [ ] Chat send permissions verified.
- [ ] Gemini model/schema/quota verified.
- [ ] Real chat and moderation event observed.
- [ ] Real vote updates overlay; timer closes; one announcement observed.

## Security/operations/submission
- [ ] Broadcaster ownership, demo-mode safety, cookies/CSRF/CORS reviewed.
- [ ] Key rotation, overlay token, logging and single-process limitations documented.
- [ ] DB backup/migration plan.
- [ ] Compose clean build and WebSocket proxy verified.
- [ ] Secret scan; accurate demo evidence; owner approval for deploy/submission.
