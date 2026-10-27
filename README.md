# StreamPilot AI

AI-powered audience interaction co-pilot for gaming streamers.

**Stack:** React + TypeScript + Vite, FastAPI, SQLAlchemy, SQLite, Google Gemini API (`google-genai`), Twitch OAuth/EventSub/chat, WebSockets.

The dashboard, persistence, challenge lifecycle, Gemini adapter, Twitch adapter, and protected overlay are implemented. Gemini and Twitch live-service behavior remains unverified until credentials are configured and the manual checks in [docs/TEST_CASES.md](docs/TEST_CASES.md) are run. Demo mode uses synthetic data.

## Start in Codex
1. Open this repository in VS Code.
2. Keep credentials in `backend/.env`, never in source code or prompts.
3. Follow [docs/MANUAL_SETUP.md](docs/MANUAL_SETUP.md).

## Prerequisites
- Python 3.12+
- Node.js LTS and npm
- Git
- Gemini API key for live AI generation
- Twitch Developer application for real Twitch integration

## Run backend (PowerShell)
```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
Copy-Item .env.example .env
uvicorn app.main:app --reload --port 8000
```

## Run frontend (second terminal)
```powershell
cd frontend
npm install
npm run dev
```

Open the Vite URL shown in the terminal. Initial dashboard content is synthetic demo data and is explicitly labeled.

## Docker Compose
Copy `backend/.env.example` to `backend/.env`, set a unique `APP_SECRET_KEY`, and configure Gemini/Twitch values. Set `DEMO_MODE=false` when testing real Twitch. Register the Twitch callback URL as `http://localhost:8080/auth/twitch/callback` for the local Compose setup, then run:

```powershell
docker compose up --build
```

Open `http://localhost:8080`. Compose stores SQLite data in a named volume and keeps the backend port private behind Nginx. For a public deployment, set `APP_ENV=production`, HTTPS frontend/callback URLs, and exact `CORS_ORIGINS` before starting; deployment itself has not been performed.

## Documents
- `docs/HIGH_LEVEL_DOCUMENT.md`
- `docs/TECHNICAL_DESIGN_DOCUMENT.md`
- `docs/TEST_CASES.md`
- `docs/PROJECT_CHECKLIST.md`
- `docs/BUILD_PLAN.md`
- `docs/MANUAL_SETUP.md`
- `docs/ARCHITECTURE.md`
- `docs/PROGRESS.md`
- `docs/SUBMISSION_CHECKLIST.md`

Never commit `.env`, API keys, OAuth tokens, refresh tokens, or local databases.
