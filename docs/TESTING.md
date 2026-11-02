# Testing Guide

The current frontend verification command also covers the challenge option focus regression and restoration of persisted vote results after reload. The frontend dashboard no longer exposes the legacy poll-suggestion/native-poll panels; backend compatibility routes should be tested separately and must not be described as current UI behavior.

Backend:
```powershell
cd backend
pytest -q
```

Frontend:
```powershell
cd frontend
npm install
npm test
npm run typecheck
npm run build
```

Mock Gemini and Twitch in ordinary tests. Use `docs/TEST_CASES.md` for the full suite. Real service tests require configured credentials and should be run separately.
