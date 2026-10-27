# Testing Guide

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
