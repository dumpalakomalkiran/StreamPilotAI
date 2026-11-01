"""Bounded live scenario driver. Imported only after --confirm-live is checked."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from live_e2e import (
    API_BASE,
    BACKEND,
    ROOT,
    SCENARIOS,
    VIEWERS_FILE,
)


class ScenarioBlocked(RuntimeError):
    """A safety gate or bounded wait prevented a scenario from continuing."""


class ScenarioFailed(RuntimeError):
    """An observed result did not meet the scenario expectation."""


class LimitsReached(ScenarioBlocked):
    """A configured run limit was reached."""


class Driver:
    def __init__(self, args, limits, script, doc, results, helpers):
        self.args = args
        self.limits = limits
        self.script = script
        self.doc = doc
        self.results = results
        self.record = helpers["record"]
        self.write_report = helpers["write_report"]
        self.api_client_factory = helpers["api_client"]
        self.twitch_credential = helpers["twitch_main_credential"]
        self.db_check = helpers["check_db_writable"]
        self.ws_check = helpers["websocket_rejection"]
        self.start = time.monotonic()
        self.started_wall = time.time()
        self.sent = 0
        self.last_send: dict[str, float] = {}
        self.twitch_token = ""
        self.broadcaster_id = ""
        self.client_id = ""
        self.viewers: dict[str, dict[str, Any]] = {}
        self.api: httpx.Client | None = None
        self.initial_usage_lines = self._usage_line_count()
        self.usage_cursor = self.initial_usage_lines
        self.usage_entries: list[dict] = []

    @property
    def usage_path(self) -> Path:
        configured = os.environ.get("STREAM_PILOT_AI_USAGE_LOG")
        if configured:
            return Path(configured)
        return next((p for p in (BACKEND / "logs/ai_usage.jsonl", ROOT / "logs/ai_usage.jsonl") if p.exists()),
                    BACKEND / "logs/ai_usage.jsonl")

    def _usage_line_count(self) -> int:
        try:
            return sum(1 for _ in self.usage_path.open(encoding="utf-8")) if self.usage_path.exists() else 0
        except OSError:
            return 0

    def _refresh_usage(self) -> None:
        if not self.usage_path.exists():
            return
        try:
            with self.usage_path.open(encoding="utf-8") as stream:
                for index, line in enumerate(stream):
                    if index < self.usage_cursor:
                        continue
                    if line.strip():
                        self.usage_entries.append(json.loads(line))
            self.usage_cursor = self._usage_line_count()
        except (OSError, json.JSONDecodeError):
            return
        self.doc["totals"]["gemini_calls"] = sum(1 for row in self.usage_entries if row.get("call_made", True))
        self.doc["totals"]["tokens_in"] = sum(int(row.get("tokens_in") or 0) for row in self.usage_entries)
        self.doc["totals"]["tokens_out"] = sum(int(row.get("tokens_out") or 0) for row in self.usage_entries)

    def _check_limits(self, *, reserve_message: bool = False, reserve_gemini: bool = False) -> None:
        elapsed = time.monotonic() - self.start
        if elapsed >= self.limits["seconds"]:
            raise LimitsReached("LIVE_TEST_MAX_SECONDS reached; stopping the scenario sequence.")
        self._refresh_usage()
        if reserve_message and self.sent >= self.limits["messages"]:
            raise LimitsReached("LIVE_TEST_MAX_MESSAGES reached; no further chat message was sent.")
        if reserve_gemini and int(self.doc["totals"]["gemini_calls"]) >= self.limits["gemini_calls"]:
            raise LimitsReached("LIVE_TEST_MAX_GEMINI_CALLS reached; no further Gemini request was started.")
        if not reserve_gemini and int(self.doc["totals"]["gemini_calls"]) >= self.limits["gemini_calls"]:
            raise LimitsReached("LIVE_TEST_MAX_GEMINI_CALLS reached; stopping the remaining scenarios.")

    def remaining(self, upper: int) -> float:
        return max(0.0, min(float(upper), self.limits["seconds"] - (time.monotonic() - self.start)))

    @staticmethod
    def _safe_request(client: httpx.Client, method: str, url: str, **kwargs) -> httpx.Response:
        response = client.request(method, url, **kwargs)
        if response.status_code >= 400:
            raise ScenarioFailed(f"{method} {url.removeprefix(API_BASE)} returned HTTP {response.status_code}.")
        return response

    def api_get(self, path: str) -> Any:
        assert self.api is not None
        return self._safe_request(self.api, "GET", f"{API_BASE}{path}").json()

    def api_post(self, path: str, body: dict | None = None) -> Any:
        assert self.api is not None
        return self._safe_request(self.api, "POST", f"{API_BASE}{path}", json=body or {}).json()

    def api_patch(self, path: str, body: dict) -> Any:
        assert self.api is not None
        return self._safe_request(self.api, "PATCH", f"{API_BASE}{path}", json=body).json()

    def wait_for(self, predicate: Callable[[], Any], timeout: int, *, interval: float = 1.0):
        deadline = time.monotonic() + self.remaining(timeout)
        while time.monotonic() < deadline:
            self._check_limits()
            try:
                result = predicate()
                if result:
                    return result
            except (httpx.HTTPError, ScenarioFailed):
                pass
            time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
        return None

    def rows(self, prefix: str) -> list[dict]:
        return [row for row in self.script if row["scenario_tag"].startswith(prefix)]

    def schedule_row(self, row: dict, *, honor_offset: bool = True) -> dict[str, Any]:
        """Send exactly one row loaded from the checked-in schedule; enforce account pacing."""
        self._check_limits(reserve_message=True)
        offset_wait = self.start + int(row["t_offset_seconds"]) - time.monotonic()
        if honor_offset and offset_wait > 0:
            time.sleep(min(offset_wait, self.remaining(int(row["t_offset_seconds"]) + 1)))
        token_alias = row["account_alias"]
        if token_alias not in self.viewers:
            if len(self.viewers) == 1:
                token_alias = next(iter(self.viewers))
            else:
                raise ScenarioBlocked(f"No authorized synthetic viewer token for {token_alias}.")
        viewer = self.viewers[token_alias]
        last = self.last_send.get(token_alias)
        if last is not None and time.monotonic() - last < 5:
            time.sleep(5 - (time.monotonic() - last))
            self._check_limits(reserve_message=True)
        self._check_limits(reserve_message=True)
        request = {
            "broadcaster_id": self.broadcaster_id,
            "sender_id": viewer["user_id"],
            "message": row["text"],
        }
        response = None
        for attempt in range(3):
            response = httpx.post(
                "https://api.twitch.tv/helix/chat/messages",
                headers={"Authorization": f"Bearer {viewer['access_token']}", "Client-Id": self.client_id},
                json=request,
                timeout=10,
            )
            if response.status_code != 429:
                break
            reset_epoch = response.headers.get("Ratelimit-Reset")
            delay = max(5, min(60, int(float(reset_epoch) - time.time()) if reset_epoch else 30))
            if attempt == 2:
                raise ScenarioBlocked("Twitch chat rate limit persisted after bounded backoff (HTTP 429).")
            time.sleep(min(delay, self.remaining(delay + 1)))
            self._check_limits(reserve_message=True)
        if response is None or response.status_code >= 400:
            code = response.status_code if response is not None else "no_response"
            raise ScenarioFailed(f"Twitch Send Chat Message returned HTTP {code}.")
        payload = response.json()
        sent = (payload.get("data") or [{}])[0]
        if not sent.get("is_sent", True):
            raise ScenarioFailed("Twitch accepted the request but reports is_sent=false.")
        self.last_send[token_alias] = time.monotonic()
        self.sent += 1
        self.doc["totals"]["messages_sent"] = self.sent
        return {"message_id": sent.get("message_id"), "account_alias": token_alias,
                "scenario_tag": row["scenario_tag"], "latency_ms": response.elapsed.total_seconds() * 1000}

    def wait_chat_text(self, text: str, timeout: int = 30):
        return self.wait_for(lambda: next((m for m in self.api_get("/api/chat/messages?limit=100")
                                           if m.get("text") == text), None), timeout)

    def send_rows(self, prefix: str) -> list[dict]:
        evidence = []
        for row in self.rows(prefix):
            self._check_limits(reserve_message=True)
            result = self.schedule_row(row)
            evidence.append(result)
        return evidence

    def send_selected(self, rows: list[dict], *, honor_offset: bool = True) -> list[dict]:
        evidence = []
        for row in rows:
            self._check_limits(reserve_message=True)
            evidence.append(self.schedule_row(row, honor_offset=honor_offset))
        return evidence

    def preflight(self) -> dict[str, Any]:
        if not self.args.confirm_live:
            raise ScenarioBlocked("Live mode requires --confirm-live; no external request was made.")
        channel_login = os.environ.get("LIVE_TEST_CHANNEL_LOGIN", "").strip().casefold()
        dashboard_cookie = os.environ.get("LIVE_TEST_DASHBOARD_SESSION", "")
        if not channel_login:
            raise ScenarioBlocked("LIVE_TEST_CHANNEL_LOGIN is required; no Twitch request was made.")
        if not dashboard_cookie:
            raise ScenarioBlocked("LIVE_TEST_DASHBOARD_SESSION is required; no app or Twitch action was made.")
        if os.environ.get("DEMO_MODE", "").strip().casefold() not in {"false", "0", "no"}:
            raise ScenarioBlocked("Set DEMO_MODE=false in this process; no external request was made.")
        if os.environ.get("LIVE_TEST_MODE", "").strip().casefold() not in {"true", "1", "yes"}:
            raise ScenarioBlocked("Set LIVE_TEST_MODE=true in the backend and harness process to enforce the Gemini call ceiling.")
        configured_log = os.environ.get("STREAM_PILOT_AI_USAGE_LOG")
        if not configured_log:
            raise ScenarioBlocked("Set STREAM_PILOT_AI_USAGE_LOG to a fresh gitignored file under repo logs/ before starting the backend.")
        log_path = Path(configured_log).resolve()
        if not any(log_path.is_relative_to(base.resolve()) for base in (ROOT / "logs", BACKEND / "logs")):
            raise ScenarioBlocked("AI usage log must be under the repository's ignored logs/ directory.")
        if log_path.exists() and log_path.stat().st_size:
            raise ScenarioBlocked("AI usage log must be a new empty file so the per-run Gemini ceiling starts at zero.")

        from app.config import get_settings

        settings = get_settings()
        if settings.demo_mode or not settings.twitch_client_id or not settings.twitch_client_secret:
            raise ScenarioBlocked("Backend is in demo mode or Twitch app credentials are absent.")
        if not settings.gemini_api_key:
            raise ScenarioBlocked("Gemini API key is not configured in backend settings.")

        self.api = self.api_client_factory(dashboard_cookie)
        self.api.get(f"{API_BASE}/api/health").raise_for_status()
        status = self.api.get(f"{API_BASE}/api/status")
        status.raise_for_status()
        status_data = status.json()
        if (status_data.get("demo_mode") or status_data.get("eventsub_worker") != "running"
                or not status_data.get("twitch_authorized") or not status_data.get("live_test_mode")):
            raise ScenarioBlocked("API must report live mode, Twitch authorization, and a running EventSub worker.")
        auth = self.api.get(f"{API_BASE}/api/interactions")
        if auth.status_code != 200:
            raise ScenarioBlocked(f"Dashboard session was rejected (HTTP {auth.status_code}).")

        self.twitch_token, self.broadcaster_id, identity = self.twitch_credential()
        if identity["login"].casefold() != channel_login:
            raise ScenarioBlocked("LIVE_TEST_CHANNEL_LOGIN does not match the connected broadcaster; no chat will be sent.")
        api_identity = self.api_get("/api/auth/identity")
        if api_identity.get("broadcaster_id") != self.broadcaster_id:
            raise ScenarioBlocked("Dashboard session broadcaster does not match the Twitch identity validated for this run.")
        self.client_id = identity["client_id"]
        subscriptions = httpx.get("https://api.twitch.tv/helix/eventsub/subscriptions",
                                  headers={"Authorization": f"Bearer {self.twitch_token}", "Client-Id": self.client_id},
                                  params={"status": "enabled"}, timeout=10)
        subscriptions.raise_for_status()
        active_chat = any(row.get("type") == "channel.chat.message"
                          and row.get("condition", {}).get("broadcaster_user_id") == self.broadcaster_id
                          and row.get("status") == "enabled" for row in subscriptions.json().get("data", []))
        if not active_chat:
            raise ScenarioBlocked("No enabled channel.chat.message subscription exists for this broadcaster.")

        token_file = VIEWERS_FILE
        if not token_file.exists():
            raise ScenarioBlocked("Authorize synthetic viewer accounts first with scripts/authorize_test_viewer.py.")
        stored = json.loads(token_file.read_text(encoding="utf-8"))
        self.viewers = self._validate_viewers(stored, settings.twitch_client_id)
        if not self.viewers:
            raise ScenarioBlocked("No valid non-broadcaster viewer token is available.")
        if len({viewer["user_id"] for viewer in self.viewers.values()}) < 3:
            self.doc["warnings"] = ["Fewer than three distinct synthetic viewers are authorized; S4 poll suggestion will be BLOCKED."]

        analytics = self.api_get("/api/analytics/current")
        if not analytics.get("session_id"):
            raise ScenarioBlocked("No active StreamPilot session exists for this broadcaster.")
        existing_messages = self.api_get("/api/chat/messages?limit=100")
        if existing_messages:
            raise ScenarioBlocked("Active session already contains chat; clear/start a test-only session before running synthetic tests.")
        self.doc["session_id"] = analytics["session_id"]
        self.doc["target_identity_match"] = True

        self.db_check()
        # Authenticate Gemini before chat sends. Count this tiny direct call against the cap.
        self._check_limits(reserve_gemini=True)
        from google import genai

        from app.services.ai_usage import record_ai_usage

        model = settings.gemini_model
        started = time.perf_counter()
        response = genai.Client(api_key=settings.gemini_api_key,
                                http_options={"timeout": min(10000, settings.ai_request_timeout_seconds * 1000)}).models.generate_content(
                                    model=model, contents="Reply with the single word OK.")
        if not response.text:
            raise ScenarioBlocked("Gemini preflight returned an empty response.")
        usage = getattr(response, "usage_metadata", None)
        record_ai_usage(operation="live_e2e_preflight", model=model,
                        tokens_in=getattr(usage, "prompt_token_count", None),
                        tokens_out=getattr(usage, "candidates_token_count", None),
                        latency_ms=round((time.perf_counter() - started) * 1000))
        self._refresh_usage()
        return {"api_mode": "live", "eventsub_chat_subscription": True,
                "viewer_accounts": len(self.viewers), "session_id": analytics["session_id"],
                "gemini_preflight_latency_ms": round((time.perf_counter() - started) * 1000)}

    def _validate_viewers(self, stored: dict, expected_client_id: str) -> dict[str, dict[str, Any]]:
        valid = {}
        for alias, record in stored.items():
            token = record.get("access_token") if isinstance(record, dict) else None
            if not isinstance(token, str) or not token:
                continue
            try:
                response = httpx.get("https://id.twitch.tv/oauth2/validate",
                                     headers={"Authorization": f"OAuth {token}"}, timeout=10)
                response.raise_for_status()
                identity = response.json()
                if set(identity.get("scopes", [])) != {"user:write:chat"}:
                    continue
                if str(identity.get("user_id")) == self.broadcaster_id:
                    continue
                if str(identity.get("client_id")) != expected_client_id:
                    continue
                valid[alias] = {"access_token": token, "user_id": str(identity["user_id"]),
                                "login": str(identity.get("login", "")).casefold()}
            except (httpx.HTTPError, KeyError, ValueError):
                continue
        return valid

    def _run_s0(self) -> None:
        row = self.rows("S0")[0]
        sent = self.schedule_row(row)
        found = self.wait_chat_text(row["text"], timeout=30)
        if not found:
            raise ScenarioBlocked("EventSub did not persist the synthetic canary in 30 seconds.")
        latency = max(0, round((time.time() - self.started_wall - row["t_offset_seconds"]) * 1000))
        self.record(self.results, "S0", "PASS", "Canary delivered through Twitch chat and EventSub to StreamPilot.",
                    "Exact scripted text appeared in the authenticated current-session chat API.",
                    {"message_id": sent.get("message_id"), "latency_ms": latency, "eventsub_visible": True})

    def _run_s2(self) -> int:
        rows = self.rows("S2-")
        questions = [row for row in rows if "gpu-question" in row["scenario_tag"] or "hinglish" in row["scenario_tag"]]
        expected_viewers = min(2, len({row["account_alias"] for row in questions if row["account_alias"] in self.viewers}))
        self.send_rows("S2-")

        def grouped():
            all_details = []
            for group in self.api_get("/api/interactions?status=open"):
                detail = self.api_get(f"/api/interactions/{group['id']}")
                texts = {entry.get("text") for entry in detail.get("messages", [])}
                matched = [row for row in questions if row["text"] in texts]
                all_details.extend(detail.get("messages", []))
                if len(matched) == len(questions):
                    return group, detail, all_details
            return None

        result = self.wait_for(grouped, 100)
        if result is None:
            raise ScenarioBlocked("Queue did not group all six scripted GPU questions within 100 seconds.")
        group, _detail, all_details = result
        expected_askers = 1 if len(self.viewers) == 1 else expected_viewers
        if group["message_count"] != 6 or group["asker_count"] != expected_askers:
            raise ScenarioFailed(f"Expected one six-message group from {expected_askers} viewer(s); observed {group['message_count']} messages and {group['asker_count']} askers.")
        negative_texts = {row["text"] for row in rows if "negative" in row["scenario_tag"]}
        if any(message.get("text") in negative_texts for message in all_details):
            raise ScenarioFailed("A greeting, emote-only post, or unrelated command entered the queue.")
        self.record(self.results, "S2", "PASS", "Six differently phrased English/Hinglish questions group into one queue item; negatives stay out.",
                    "One group contains exactly the six scripted GPU questions.",
                    {"group_id": group["id"], "message_count": group["message_count"],
                     "asker_count": group["asker_count"], "expected_askers": expected_askers})
        return int(group["id"])

    def _run_s3(self, group_id: int) -> None:
        facts = self.api_get("/api/settings/stream-facts").get("stream_facts", "")
        if not str(facts).strip():
            raise ScenarioBlocked("Add non-secret Stream facts in dashboard Settings to exercise the grounded Gemini reply path.")
        before = self.api_get("/api/chat/messages?limit=100")
        self._refresh_usage()
        reply_calls_before = sum(1 for row in self.usage_entries if row.get("operation") == "reply_suggestion")
        suggestion = self.api_post(f"/api/interactions/{group_id}/response/suggest")
        if suggestion.get("status") not in {"draft", "edited"} or not 1 <= len(suggestion.get("text", "")) <= 300:
            raise ScenarioFailed("Reply suggestion was missing, not a draft, or outside 1–300 characters.")
        self._refresh_usage()
        reply_rows = [row for row in self.usage_entries if row.get("operation") == "reply_suggestion"]
        if (len(reply_rows) <= reply_calls_before or reply_rows[-1].get("failure") != "none"
                or not reply_rows[-1].get("call_made", True)):
            raise ScenarioBlocked("Gemini reply generation failed or was not observed in the usage log; draft was not sent.")
        current = self.api_get("/api/chat/messages?limit=100")
        if len(current) != len(before):
            raise ScenarioFailed("Chat changed during suggestion generation; draft may have sent without approval.")
        if any(message.get("text") == suggestion["text"] for message in current):
            raise ScenarioFailed("Draft text appeared in Twitch chat before the explicit send call.")
        self._check_limits(reserve_message=True)
        sent = self.api_post(f"/api/responses/{suggestion['id']}/send")
        if sent.get("status") != "sent":
            raise ScenarioFailed("Explicit send endpoint did not mark the reviewed response sent.")
        visible = self.wait_for(lambda: next((message for message in self.api_get("/api/chat/messages?limit=100")
                                              if message.get("text") == suggestion["text"]), None), 30)
        if not visible:
            raise ScenarioBlocked("Approved reply was accepted but EventSub did not echo it into stored chat within 30 seconds.")
        self.sent += 1
        self.doc["totals"]["messages_sent"] = self.sent
        self.record(self.results, "S3", "PASS", "AI reply remains a draft until explicit `/send`; sent reply is observed via chat.",
                    "Draft was absent from chat before approval and present after the explicit send endpoint.",
                    {"group_id": group_id, "response_id": suggestion["id"], "status": sent["status"],
                     "message_length": len(suggestion["text"])})

    def _run_s4(self) -> None:
        prefs = self.rows("S4-")
        prefs = [row for row in prefs if "preference" in row["scenario_tag"]]
        distinct = {self.viewers[row["account_alias"]]["user_id"] for row in prefs
                    if row["account_alias"] in self.viewers}
        if len(distinct) < 3:
            self.record(self.results, "S4", "BLOCKED", "A poll suggestion is generated from >=3 distinct viewers with competing preferences.",
                        "Fewer than three distinct authorized synthetic accounts are available; no poll was approved or launched.",
                        {"distinct_viewers": len(distinct), "required": 3})
            return
        evidence = self.send_selected(prefs)
        suggestion = self.wait_for(lambda: next((row for row in self.api_get("/api/polls/suggestions")
                                                  if row.get("status") == "suggested"), None), 100)
        if not suggestion:
            raise ScenarioBlocked("No poll suggestion appeared within 100 seconds after the competing preferences.")
        approved = self.api_post(f"/api/polls/suggestions/{suggestion['id']}/approve", {
            "title": "Demo poll: ranked or casual?", "options": ["Ranked", "Casual"], "duration_seconds": 15,
        })
        challenge_id = approved.get("id")
        if not challenge_id:
            raise ScenarioFailed("Poll suggestion approval did not create the existing challenge draft.")
        self.api_post(f"/api/challenges/{challenge_id}/approve")
        launched = self.api_post(f"/api/challenges/{challenge_id}/launch")
        if launched.get("status") != "active":
            raise ScenarioFailed("Explicitly approved poll challenge did not launch.")
        vote_rows = self.rows("S4-vote-")
        self.send_selected(vote_rows, honor_offset=False)
        final = self.wait_for(lambda: next((row for row in self.api_get("/api/challenges")
                                            if row.get("id") == challenge_id and row.get("status") == "complete"), None), 40)
        if not final:
            raise ScenarioBlocked("Approved vote did not close through the existing timer within 40 seconds.")
        if final.get("total_votes") != 2:
            raise ScenarioFailed(f"Expected two current votes after one viewer changed their vote; observed {final.get('total_votes')}.")
        self.record(self.results, "S4", "PASS", "Competing preferences produce an editable poll draft; explicit approval and launch reuse the existing vote timer.",
                    "Poll draft was edited, approved, launched, received votes including a replacement, and closed by the timer.",
                    {"suggestion_id": suggestion["id"], "challenge_id": challenge_id,
                     "supporting_viewers": len(distinct), "preference_messages": len(evidence),
                     "total_votes_after_replacement": final.get("total_votes"), "status": final.get("status")})

    def _run_s5(self) -> None:
        rows = self.rows("S5-")
        before = {row["id"] for row in self.api_get("/api/challenges")}
        sent = self.send_rows("S5-")
        for row in rows:
            if not self.wait_chat_text(row["text"], timeout=20):
                raise ScenarioBlocked(f"A scripted S5 message was not observed through EventSub ({row['scenario_tag']}).")
        after = {row["id"] for row in self.api_get("/api/challenges")}
        if after != before:
            raise ScenarioFailed("Prompt-injection/input messages created an unapproved challenge or other action.")
        self.record(self.results, "S5", "PASS", "Injection, invalid vote, HTML, Unicode and long text remain plain chat data.",
                    "All scripted messages were ingested; no challenge was created as a side effect.",
                    {"scripted_messages": len(sent), "challenge_count_before": len(before), "challenge_count_after": len(after)})

    def _bounded_enter(self, text: str) -> bool:
        print(text, flush=True)
        deadline = time.monotonic() + self.remaining(120)
        if os.name == "nt":
            import msvcrt

            while time.monotonic() < deadline:
                self._check_limits()
                if msvcrt.kbhit():
                    key = msvcrt.getwch()
                    if key in ("\r", "\n"):
                        return True
                    if key == "\x03":
                        return False
                time.sleep(0.1)
            return False
        import select

        ready, _, _ = select.select([sys.stdin], [], [], max(0, deadline - time.monotonic()))
        return bool(ready and sys.stdin.readline() is not None)

    def _run_s7(self) -> None:
        row = self.rows("S7-delete-me")[0]
        sent = self.schedule_row(row)
        if not self.wait_chat_text(row["text"], timeout=30):
            raise ScenarioBlocked("Deletion target did not arrive through EventSub.")
        if not self._bounded_enter(
            f"In Twitch chat, delete the synthetic test message `{row['text']}` (message id {sent.get('message_id')}). Press Enter here after deletion."
        ):
            raise ScenarioBlocked("Timed out waiting for manual Twitch message deletion confirmation.")
        deleted = self.wait_for(lambda: not any(m.get("text") == row["text"]
                                                 for m in self.api_get("/api/chat/messages?limit=100")), 30)
        if deleted is None:
            raise ScenarioBlocked("Twitch chat deletion was not reflected by EventSub within 30 seconds.")
        if not self._bounded_enter("In Twitch chat, run /clear as the broadcaster, then press Enter here."):
            raise ScenarioBlocked("Timed out waiting for manual Twitch chat-clear confirmation.")
        cleared = self.wait_for(lambda: len(self.api_get("/api/chat/messages?limit=100")) == 0, 30)
        if cleared is None:
            raise ScenarioBlocked("Twitch chat clear was not reflected by EventSub within 30 seconds.")
        self.record(self.results, "S7", "PASS", "Delete and full-clear EventSub notifications remove test messages without stopping the worker.",
                    "The deleted test message disappeared and the chat-clear event emptied current-session raw chat.",
                    {"deleted_message_id": sent.get("message_id"), "raw_chat_after_clear": 0})

    def _run_s8(self) -> None:
        row = self.rows("S8-")[0]
        sent = self.schedule_row(row)
        if not self.wait_chat_text(row["text"], timeout=30):
            raise ScenarioBlocked("Post-clear analytics marker did not arrive through EventSub.")
        stats = self.api_get("/api/analytics/current")
        session_id = stats.get("session_id")
        if not session_id or stats.get("total_messages", 0) < 1:
            raise ScenarioFailed("Live aggregate statistics did not include the post-clear synthetic marker.")
        end_result = self.api_post("/api/analytics/current/end")
        if end_result.get("session_id") != session_id:
            raise ScenarioFailed("Session-end endpoint returned an unexpected session.")
        def completed_summary():
            response = self.api_get(f"/api/analytics/sessions/{session_id}/summary")
            return response if response.get("status") == "complete" else None

        summary = self.wait_for(completed_summary, 90)
        if not summary:
            raise ScenarioBlocked("Session recap did not reach complete status within 90 seconds.")
        before_calls = self._summary_call_count()
        cached = self.api_post(f"/api/analytics/sessions/{session_id}/summary", {"regenerate": False})
        after_calls = self._summary_call_count()
        if cached.get("generated_at") != summary.get("generated_at") or before_calls != after_calls:
            raise ScenarioFailed("Second recap request did not return the cached summary without another Gemini call.")
        self.record(self.results, "S8", "PASS", "Ending the session sets ended_at, generates one aggregate recap, and the next request uses cache.",
                    "Session recap completed and a second request returned the same generated timestamp without another summary call.",
                    {"session_id": session_id, "messages_before_end": stats.get("total_messages"),
                     "synthetic_marker_id": sent.get("message_id"), "summary_generated_at": summary.get("generated_at"),
                     "summary_call_count": after_calls})

    def _summary_call_count(self) -> int:
        self._refresh_usage()
        return sum(1 for row in self.usage_entries
                   if row.get("operation") == "session_summary" and row.get("call_made", True))

    def _run_scenario(self, scenario_id: str, operation: Callable, *args) -> bool:
        try:
            operation(*args)
            return True
        except LimitsReached as exc:
            self.record(self.results, scenario_id, "BLOCKED", SCENARIOS[scenario_id], str(exc))
            self.doc["stop_reason"] = str(exc)
            return False
        except ScenarioBlocked as exc:
            self.record(self.results, scenario_id, "BLOCKED", SCENARIOS[scenario_id], str(exc))
            return False
        except (ScenarioFailed, httpx.HTTPError, ValueError, KeyError) as exc:
            self.record(self.results, scenario_id, "FAIL", SCENARIOS[scenario_id],
                        f"{type(exc).__name__}: {str(exc)[:180]}")
            return False
        except Exception as exc:  # noqa: BLE001 - report only type; do not log provider payloads/secrets
            self.record(self.results, scenario_id, "FAIL", SCENARIOS[scenario_id],
                        f"Unexpected {type(exc).__name__}; details omitted from the report.")
            return False

    def _write_cost(self) -> None:
        self._refresh_usage()
        try:
            pricing = json.loads((ROOT / "config/gemini_pricing.json").read_text(encoding="utf-8"))
            from app.config import get_settings

            rates = pricing["model_rates"].get(get_settings().gemini_model, {})
            if rates:
                total_in = self.doc["totals"]["tokens_in"]
                total_out = self.doc["totals"]["tokens_out"]
                self.doc["totals"]["estimated_cost_usd"] = round(
                    (total_in * rates["input_usd_per_million_tokens"]
                     + total_out * rates["output_usd_per_million_tokens"]) / 1_000_000, 8)
        except (OSError, KeyError, ValueError, TypeError):
            self.doc["totals"]["estimated_cost_usd"] = None

    def run(self) -> int:
        run_scenarios, not_run = ("S0", "S2", "S3", "S4", "S5", "S7", "S8"), ("S1", "S6", "S9", "S10", "S11")
        for scenario_id in not_run:
            self.record(self.results, scenario_id, "NOT RUN", SCENARIOS[scenario_id],
                        "Explicitly deferred by this focused demo run.")
        try:
            preflight = self.preflight()
            self.doc["preflight"] = preflight
        except ScenarioBlocked as exc:
            self.doc["blockers"].append(str(exc))
            for scenario_id in run_scenarios:
                if not any(row["scenario_id"] == scenario_id for row in self.results):
                    self.record(self.results, scenario_id, "BLOCKED", SCENARIOS[scenario_id], str(exc))
            self.doc["stop_reason"] = "preflight_blocked"
            self.finish()
            return 2
        except Exception as exc:  # noqa: BLE001 - omit provider payloads, headers and credentials
            reason = f"Preflight failed ({type(exc).__name__}); details omitted from the report."
            self.doc["blockers"].append(reason)
            for scenario_id in run_scenarios:
                self.record(self.results, scenario_id, "BLOCKED", SCENARIOS[scenario_id], reason)
            self.doc["stop_reason"] = "preflight_blocked"
            self.finish()
            return 2

        ok_s0 = self._run_scenario("S0", self._run_s0)
        if not ok_s0:
            for scenario_id in ("S2", "S3", "S4", "S5", "S7", "S8"):
                self.record(self.results, scenario_id, "BLOCKED", SCENARIOS[scenario_id],
                            "Baseline EventSub canary failed; subsequent send/action scenarios were withheld.")
            self.doc["stop_reason"] = "baseline_eventsub_failed"
            self.finish()
            return 2

        group_id = None
        if self._run_scenario("S2", self._run_s2):
            group_id = next((r.get("evidence", {}).get("group_id") for r in reversed(self.results)
                             if r["scenario_id"] == "S2" and r["status"] == "PASS"), None)
        if group_id is not None:
            self._run_scenario("S3", self._run_s3, group_id)
        else:
            self.record(self.results, "S3", "BLOCKED", SCENARIOS["S3"], "S2 produced no grouped question to reply to.")
        self._run_scenario("S4", self._run_s4)
        self._run_scenario("S5", self._run_s5)
        self._run_scenario("S7", self._run_s7)
        self._run_scenario("S8", self._run_s8)
        self.finish()
        return 1 if any(row["status"] == "FAIL" for row in self.results) else 0

    def finish(self) -> None:
        self._write_cost()
        self.doc["finished_at"] = datetime.now(UTC).isoformat()
        self.doc["totals"]["messages_sent"] = self.sent
        self.doc["totals"]["gemini_calls"] = sum(1 for row in self.usage_entries if row.get("call_made", True))
        self.write_report(self.doc)
        print(f"Live scenario report: {ROOT / 'docs/LIVE_TEST_REPORT.md'}")
        print(f"Structured results: {ROOT / 'docs/live_test_results.json'}")
        print(f"Messages sent: {self.sent}; Gemini calls: {self.doc['totals']['gemini_calls']}; stop reason: {self.doc.get('stop_reason') or 'scenario sequence complete'}")


def run_live_scenarios(*, args, limits, script, doc, results, record, write_report,
                       api_client, twitch_main_credential, check_db_writable, websocket_rejection) -> int:
    driver = Driver(args, limits, script, doc, results, {
        "record": record,
        "write_report": write_report,
        "api_client": api_client,
        "twitch_main_credential": twitch_main_credential,
        "check_db_writable": check_db_writable,
        "websocket_rejection": websocket_rejection,
    })
    return driver.run()
