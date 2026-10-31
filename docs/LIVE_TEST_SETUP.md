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

Review the synthetic script and current code before allowing side effects. The current harness implements preflight, schedule/limit validation, and report generation, but **does not yet implement the full S0–S10 live scenario driver**. It refuses to send chat unless flags are supplied, but those flags do not make unimplemented steps safe or complete. Therefore, do not use the live-send flags yet. The `--offline` report accurately marks live scenarios blocked and UI screenshot automation not built.

When the full scenario driver is implemented and reviewed, a future run command may be:

```powershell
python scripts/live_e2e.py --confirm-live-synthetic --allow-streamer-actions
```

The current command is intentionally documented as unavailable until then. Twitch's official authorization-code flow documentation describes the exact redirect matching and `state` parameter requirements: [Get OAuth Access Tokens](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/). The chat send API's required scope and limits are documented in [Twitch API Reference](https://dev.twitch.tv/docs/api/reference/#send-chat-message).
