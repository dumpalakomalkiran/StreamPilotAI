# StreamPilot AI — hackathon submission

## Project description (ready to paste; about 300 words)

StreamPilot AI is an audience interaction co-pilot for Twitch gaming streamers. During a busy stream, useful questions and community preferences can disappear in a fast chat feed. StreamPilot receives Twitch chat through EventSub and places the raw conversation beside a Smart Queue that detects English and Hinglish questions, groups similar messages, counts distinct viewers, and ranks items using explainable signals such as repetition and recency. The streamer can answer, snooze, dismiss, or feature a question on the optional OBS overlay.

Google Gemini supports bounded, structured tasks: grouping unresolved questions in batches, suggesting polls when viewers show competing preferences, drafting replies when the streamer asks, and preparing a post-stream recap from aggregate data. The existing StreamPilot poll flow uses viewer `!vote N` messages. Optional native Twitch poll creation is implemented separately, disabled by default, and requires streamer opt-in, reauthorization, and Affiliate or Partner eligibility. Reply drafts can use streamer-provided facts and are editable; a message is sent only when the streamer explicitly chooses Send to chat.

StreamPilot also provides live community statistics and conservative moderation flags for patterns such as repeated spam, excessive caps, and new-account links. Flags are suggestions only: the system does not automatically ban, time out, or delete messages. Viewer text is treated as untrusted data, Gemini responses are schema-validated, and unavailable or invalid AI output falls back to deterministic queue behavior or safe local responses. The application uses a React and TypeScript dashboard, FastAPI backend, SQLAlchemy with SQLite, Twitch OAuth and Helix, EventSub WebSockets, and the Google `google-genai` SDK.

Codex was used iteratively to inspect the existing repository, design and implement requested backend and dashboard features, write mocked tests, debug a malformed-EventSub notification regression, and update setup and verification documentation. Automated provider tests are mocked. Live Twitch and Gemini end-to-end behavior still requires controlled demonstration evidence; configured credentials or enabled subscriptions alone are not presented as proof.

## 2:45 demo-video outline

Follow the exact accounts, chat lines, timing, and expected outcomes in [DEMO_SCRIPT.md](./DEMO_SCRIPT.md).

| Time | Video segment |
|---|---|
| 0:00–0:14 | Introduce the real connected channel and show raw chat beside Smart Queue. |
| 0:15–0:39 | Seven alternate accounts send six varied English GPU/settings questions and one Hinglish question. |
| 0:40–0:58 | Show the grouped question, viewer counts, explainable priority, and streamer-controlled actions. |
| 0:59–1:22 | Three viewers disagree about ranked versus casual; show a poll suggestion only if it appears, without approving it. |
| 1:23–1:47 | Send emote spam and the same repeated line four times from one account; show the flag-only safety banner. |
| 1:48–2:20 | Send the prompt-injection attempt as ordinary chat text; show that it does not trigger a Twitch action. |
| 2:21–2:45 | Show the optional featured question overlay and close on the dashboard. |

## Pre-submission checklist

- [ ] Confirm the repository URL is accessible to judges: https://github.com/dumpalakomalkiran/StreamPilotAI
- [ ] Add the final YouTube link: `[paste video URL]`; verify it is **public** and **under 3 minutes**.
- [ ] Record the Codex Session ID from `/status`: `[paste Session ID]`.
- [ ] Show the project working against Twitch in the video: observe actual EventSub chat delivery; distinguish provider evidence from mocked/local tests.
- [ ] Verify the public repository contains no `.env`, Twitch client secret, OAuth token, Gemini API key, private overlay URL, or local database.
- [ ] Replace README screenshot placeholders with safe screenshots, or remove any placeholder the submission does not use.
- [ ] Confirm README setup, safety boundaries, limitations, and optional native-poll eligibility are accurate for the submitted revision.
- [ ] Run the backend/frontend gates and record whether a clean Docker Compose build was possible in the submission environment.
