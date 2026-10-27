# Manual Setup

## Gemini
1. Open https://aistudio.google.com/apikey and create an API key.
2. Check current model availability, quota, pricing, and data terms.
3. Copy `backend/.env.example` to `backend/.env`, then set `GEMINI_API_KEY` and `GEMINI_MODEL` there. The model value is the model name enabled for your Gemini API project; check current model availability in the official docs.
4. Keep the key backend-only; never use `VITE_` variables for secrets.
5. Automated tests mock Gemini. A live request requires the configured key and is not run by the test suite.

Official docs: https://ai.google.dev/gemini-api/docs ; https://ai.google.dev/gemini-api/docs/structured-output ; https://ai.google.dev/gemini-api/docs/pricing

## Twitch
1. Open https://dev.twitch.tv/console/apps and register an application.
2. For direct local backend/frontend development set callback to `http://localhost:8000/auth/twitch/callback`. For Docker Compose use `http://localhost:8080/auth/twitch/callback` and set the same value in `TWITCH_REDIRECT_URI`.
3. Put client ID and secret in `backend/.env`; never put the secret in frontend variables or source control.
4. Set `APP_SECRET_KEY` in `backend/.env` to a generated random value (at least 32 characters); generate locally with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Do not share the generated value.
5. Set `DEMO_MODE=false` for real Twitch OAuth/EventSub. Demo mode intentionally bypasses streamer authentication and does not start EventSub.
6. Confirm required scopes and token types against current official docs.
7. Authorize the broadcaster and test with a controlled channel and separate viewer.

Official docs: https://dev.twitch.tv/docs/authentication/ ; https://dev.twitch.tv/docs/chat/ ; https://dev.twitch.tv/docs/chat/authenticating/ ; https://dev.twitch.tv/docs/eventsub/handling-websocket-events/ ; https://dev.twitch.tv/docs/api/reference/

## OBS
1. Generate and copy the protected overlay URL from the dashboard's Overlay Setup view.
2. Add a Browser Source in OBS and paste the URL.
3. Use a suitable canvas size such as 1920x1080.
4. Test contrast over gameplay.
5. Never put broadcaster tokens or API keys in the URL.

## Production
Use HTTPS, a unique random `APP_SECRET_KEY` of at least 32 characters, persistent DB storage, exact OAuth callback URLs, restricted `CORS_ORIGINS`, and a host that supports persistent WebSockets. The Compose stack is local deployment preparation, not evidence of a hosted deployment. Deploy only with project-owner approval.
