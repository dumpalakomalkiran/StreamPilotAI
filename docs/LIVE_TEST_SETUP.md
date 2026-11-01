# Live synthetic test setup

The live harness is fail-closed and uses only messages in `data/live_test_script.jsonl`. Run its offline validation first:

```powershell
python scripts/live_e2e.py --offline
```

## Twitch app registration

For each synthetic viewer account, register this exact OAuth Redirect URL in the Twitch Developer Console application used by StreamPilot:

```text
http://localhost:8765/callback
```

The authorization helper uses Twitch's authorization-code flow with an unpredictable `state` value and requests only `user:write:chat`. The redirect URI in the authorization request and token exchange must exactly match the registered URL. Do not add this scope to the broadcaster's default authorization. Run once for each separate synthetic viewer account:

```powershell
python scripts/authorize_test_viewer.py --alias viewer_a
python scripts/authorize_test_viewer.py --alias viewer_b
python scripts/authorize_test_viewer.py --alias viewer_c
```

Tokens are written to the ignored local file `data/live_test_viewer_tokens.json`. The helper does not print access tokens. Use separate accounts that you control; the broadcaster cannot be a synthetic viewer. If only one synthetic viewer is available, grouping/poll diversity assertions will be weaker and must be reported as such.

## Before a live run

Start the backend and frontend as usual and complete Twitch broadcaster OAuth in the dashboard. Confirm the backend reports live mode and a running EventSub worker. In the same PowerShell window used to launch the harness, set the exact broadcaster login and the app-session cookie value copied locally from the authenticated dashboard browser session:

```powershell
$env:LIVE_TEST_CHANNEL_LOGIN = "your-demo-channel-login"
$env:LIVE_TEST_DASHBOARD_SESSION = "<copy locally; never paste into chat or commit>"
$env:DEMO_MODE = "false"
```

Review `data/live_test_script.jsonl` before a live run. The focused driver implements S0, S2, S3, S4, S5, S7 and S8; S1, S6, S9, S10 and S11 are deliberately not run. It requires the confirmation flag and all preflight gates to pass. It stops at the configured message, Gemini-call or elapsed-time ceiling. No results are live-verified until the owner runs it and checks the generated report.

Use a new usage log under the ignored repo `logs/` folder. Set the same path in the backend terminal and the harness terminal. Start with these limits (the program also enforces safe defaults and bounds):

```powershell
$env:LIVE_TEST_MODE = "true"
$env:LIVE_TEST_MAX_MESSAGES = "200"
$env:LIVE_TEST_MAX_GEMINI_CALLS = "150"
$env:LIVE_TEST_MAX_SECONDS = "1800"
$env:STREAM_PILOT_AI_USAGE_LOG = (Join-Path (Get-Location) "logs/live-e2e-<unique-run-id>.jsonl")
$env:LIVE_TEST_CHANNEL_LOGIN = "your-demo-channel-login"
$env:LIVE_TEST_DASHBOARD_SESSION = "<local authenticated app-session cookie value>"
$env:DEMO_MODE = "false"
python scripts/live_e2e.py --confirm-live
```

The schedule contains only synthetic test text. Three different authorized viewer accounts are needed to pass the competing-viewer poll threshold; with fewer accounts S4 is BLOCKED. The harness sends a canary before scenarios, but it does not run until all checks pass. During S7, the owner must delete the indicated synthetic message and issue `/clear` as the broadcaster, then press Enter in the harness terminal. The harness ends the active session in S8, so use a dedicated test session. Twitch's official authorization-code flow documentation describes the redirect matching and `state` requirements: [Get OAuth Access Tokens](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/). The Send Chat Message scope is checked by the harness as `user:write:chat`; the endpoint is documented in the [Twitch API Reference](https://dev.twitch.tv/docs/api/reference/#send-chat-message).
