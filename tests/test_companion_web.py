"""HTTP adapter that hands the Pixso design to the web front end."""

import json
import queue
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from agent_framework.companion.app import create_application_service
from agent_framework.companion.context import PromptBuild
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.companion.web import WebAdapter
from agent_framework.llm import OfflineLLMClient


class StubSearch:
    def search(self, query, limit=5):
        return []


class WebAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        store = CompanionStore(root / "companion.db")
        runtime = CompanionRuntime(
            store=store,
            llm_client=OfflineLLMClient(),
            search_service=SearchService(StubSearch()),
            data_dir=root,
            session_id="main",
            offline=True,
        )
        runtime.select_workspace(str(root))
        self.service = create_application_service(runtime)
        self.adapter = WebAdapter(self.service, data_dir=root).start()
        self.base = "http://" + self.adapter.address

    def tearDown(self):
        self.adapter.stop()
        self.temp.cleanup()

    def request(self, path, body=None, token=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={
                "X-Companion-Token": self.adapter.token if token is None else token,
                "Content-Type": "application/json",
            },
            method="POST" if body is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8") or "{}")

    def wait_for(self, predicate, timeout=15):
        deadline = time.time() + timeout
        payload = None
        while time.time() < deadline:
            _, payload = self.request("/api/bootstrap")
            if predicate(payload):
                return payload
            time.sleep(0.15)
        self.fail("condition not met: {}".format(payload))

    def test_pages_and_token(self):
        for page in ("/", "/settings", "/console", "/overlay"):
            with urllib.request.urlopen(
                "{}{}?t={}".format(self.base, page, self.adapter.token), timeout=10
            ) as response:
                self.assertEqual(200, response.status)
                self.assertIn(b"<!DOCTYPE html>", response.read())
        status, payload = self.request("/api/bootstrap")
        self.assertEqual(200, status)
        for key in ("persona", "sessions", "messages", "ui", "status", "proactive"):
            self.assertIn(key, payload)
        status, _ = self.request("/api/bootstrap", token="wrong-token")
        self.assertEqual(403, status)

    def test_ui_update_persists_and_broadcasts(self):
        channel = self.adapter.subscribe()
        try:
            status, payload = self.request(
                "/api/ui", {"theme": "pearl-white", "font_scale": 150}
            )
        finally:
            self.adapter.unsubscribe(channel)
        self.assertEqual(200, status)
        self.assertEqual("pearl-white", payload["theme"])
        self.assertEqual(150, payload["font_scale"])
        message = json.loads(channel.get(timeout=2))
        self.assertEqual("ui", message["type"])
        _, bootstrap = self.request("/api/bootstrap")
        self.assertEqual("pearl-white", bootstrap["ui"]["theme"])
        self.assertEqual(150, bootstrap["ui"]["font_scale"])

    def test_console_pages_are_shaped(self):
        expected = {
            "memory": "rows",
            "recall": "rows",
            "tasks": "rows",
            "affect": "snapshot",
            "proactive": "status",
            "audit": "rows",
            "settings": "workspace",
            "skills": "skills",
            "failures": "receipts",
        }
        for page, key in expected.items():
            status, payload = self.request("/api/console/" + page)
            self.assertEqual(200, status, page)
            self.assertIn(key, payload, page)

    def test_mood_correction_accepts_chinese_label(self):
        status, payload = self.request("/api/console/affect", {"mood": "开心"})
        self.assertEqual(200, status)
        self.assertEqual("positive", payload["user"]["mood_label"])
        self.assertEqual("开心", payload["user"]["mood_text"])
        _, bootstrap = self.request("/api/bootstrap")
        self.assertEqual("开心", bootstrap["settings_summary"]["mood_text"])

    def test_task_cards_expose_actions_through_adapter(self):
        self.service.runtime.create_work_task("把今天的总结发给项目群", force=True)
        _, data = self.request("/api/bootstrap")
        task = data["tasks"][0]
        self.assertEqual("PLAN_REVIEW", task["state"])
        self.assertEqual("把今天的总结发给项目群", task["title"])
        self.assertEqual("awaiting_confirmation", task["receipt"]["status"])
        status, payload = self.request(
            "/api/console/tasks",
            {"action": "reject", "task_id": task["task_id"], "reason": "测试拒绝"},
        )
        self.assertEqual(200, status)
        self.assertTrue(payload)
        _, after = self.request("/api/bootstrap")
        self.assertEqual("NORMAL", after["tasks"][0]["state"])

    def test_console_memory_purge_removes_record(self):
        memory_id = self.service.runtime.remember_persona("测试记忆")
        _, before = self.request("/api/console/memory")
        self.assertIn(memory_id, [row["memory_id"] for row in before["rows"]])
        status, payload = self.request(
            "/api/console/memory", {"action": "purge", "memory_id": memory_id}
        )
        self.assertEqual(200, status)
        self.assertTrue(payload["ok"])
        _, after = self.request("/api/console/memory")
        self.assertNotIn(memory_id, [row["memory_id"] for row in after["rows"]])

    def test_turn_streams_through_the_adapter(self):
        channel = self.adapter.subscribe()
        try:
            status, payload = self.request(
                "/api/turn", {"text": "你好", "mode": "chat"}
            )
            self.assertEqual(202, status)
            self.assertTrue(payload["run_id"])
            kinds = []
            while True:
                message = json.loads(channel.get(timeout=20))
                if message["type"] == "turn":
                    kinds.append(message["event"]["kind"])
                if message["type"] == "refresh":
                    break
        finally:
            self.adapter.unsubscribe(channel)
        self.assertIn("delta", kinds)
        self.assertEqual("done", kinds[-1])
        bootstrap = self.wait_for(
            lambda data: any(item["role"] == "assistant" for item in data["messages"])
        )
        self.assertIsNone(bootstrap["run"])
        self.assertEqual("user", bootstrap["messages"][-2]["role"])
        self.assertEqual("assistant", bootstrap["messages"][-1]["role"])

    def test_event_stream_frames_every_sse_write(self):
        """Chromium drops an event stream whose first write is not a chunk, so the
        preamble has to be framed exactly like the events that follow it."""
        preamble = b"retry: 1000\n\n"
        framed = ("%X\r\n" % len(preamble)).encode("ascii") + preamble + b"\r\n"
        host, port = self.adapter.address.rsplit(":", 1)
        sock = socket.create_connection((host, int(port)), timeout=10)
        try:
            sock.sendall(
                (
                    "GET /api/events?t=%s HTTP/1.1\r\n"
                    "Host: %s\r\n"
                    "Accept: text/event-stream\r\n"
                    "Connection: close\r\n\r\n"
                    % (quote(self.adapter.token), self.adapter.address)
                ).encode("ascii")
            )
            head = b""
            deadline = time.time() + 10
            while framed not in head and time.time() < deadline:
                more = sock.recv(4096)
                if not more:
                    break
                head += more
        finally:
            sock.close()

        self.assertIn(b"Transfer-Encoding: chunked", head)
        self.assertIn(framed, head)
        self.assertEqual(head.index(b"\r\n\r\n") + 4, head.index(framed))

    def test_bootstrap_run_carries_the_collapsed_step_timeline(self):
        release = threading.Event()

        def slow_stream(text, mode, generation_id="", cancel_event=None):
            yield {"kind": "delta", "text": "一"}
            yield {"kind": "delta", "text": "二"}
            yield {
                "kind": "tool",
                "tool_name": "search_notes",
                "state": "running",
            }
            yield {
                "kind": "tool",
                "tool_name": "search_notes",
                "state": "done",
                "summary": "2 条",
            }
            release.wait(10)
            self.adapter.runtime.store.add_assistant("一二")
            yield {
                "kind": "done",
                "result": {
                    "answer": "一二",
                    "proposal": None,
                    "receipt": None,
                    "task": None,
                    "task_receipt": None,
                },
            }

        self.adapter.runtime.stream_message = slow_stream
        status, payload = self.request("/api/turn", {"text": "时间线", "mode": "chat"})
        self.assertEqual(202, status)
        try:
            data = self.wait_for(
                lambda item: item["run"] and len(item["run"]["steps"]) >= 4
            )
            steps = data["run"]["steps"]
            self.assertEqual(
                [(step["stage"], step["detail"]) for step in steps],
                [
                    ("thinking", ""),
                    ("answering", ""),
                    ("tool", "search_notes"),
                    ("tool", "search_notes：2 条"),
                ],
            )
            self.assertEqual(
                sorted(steps[0]), ["at", "detail", "sequence", "stage", "tool"]
            )
            self.assertTrue(all(step["at"] for step in steps))
            self.assertEqual([step["sequence"] for step in steps], [0, 1, 3, 4])
        finally:
            release.set()
        self.wait_for(lambda data: data["run"] is None)

    def test_bootstrap_context_carries_the_gauge_numbers(self):
        _, before = self.request("/api/bootstrap")
        self.assertIsNone(before["context"])

        self.adapter.runtime.last_context = PromptBuild(
            system="",
            history=[],
            stable_hash="",
            layers={},
            budget_chars=4000,
            used_chars=1500,
            history_chars=1500,
            compressed=True,
            dropped_count=0,
            checkpoint_through=0,
        )
        bootstrap = self.wait_for(lambda data: data["context"])

        context = bootstrap["context"]
        self.assertEqual(1500, context["context_used"])
        self.assertEqual(4000, context["context_limit"])
        self.assertTrue(context["compressed"])
        self.assertNotIn("percent", context)
        lines = "\n".join(
            line
            for section in bootstrap["inspector"]
            if section["title"] == "上下文"
            for line in section["lines"]
        )
        self.assertIn("用量：1500 / 4000 字符", lines)

    def test_context_gauge_stays_hidden_until_a_prompt_is_built(self):
        status, _ = self.request("/api/turn", {"text": "你好", "mode": "chat"})
        self.assertEqual(202, status)
        self.wait_for(
            lambda data: any(item["role"] == "assistant" for item in data["messages"])
        )
        self.assertIsNone(self.request("/api/bootstrap")[1]["context"])

    def test_overlay_payload_carries_font_scale(self):
        self.adapter.ui_state.set_font_scale(150)
        payload = self.adapter.overlay()
        self.assertEqual(150, payload["font_scale"])
        self.assertEqual("charcoal-pink", payload["theme"])

    def test_activate_endpoint_calls_shell_and_requires_token(self):
        called = []
        self.adapter.set_activate_handler(lambda: called.append(True))
        status, payload = self.request("/api/activate", {})
        self.assertEqual(200, status)
        self.assertTrue(payload["shown"])
        self.assertEqual([True], called)
        status, _ = self.request("/api/activate", {}, token="wrong-token")
        self.assertEqual(403, status)

    def test_activate_without_shell_returns_page_url(self):
        status, payload = self.request("/api/activate", {})
        self.assertEqual(200, status)
        self.assertFalse(payload["shown"])
        self.assertEqual(self.adapter.url, payload["url"])

    def test_second_launch_activates_running_adapter(self):
        from agent_framework.companion.activation import (
            activate,
            clear_endpoint,
            write_endpoint,
        )

        called = []
        self.adapter.set_activate_handler(lambda: called.append(True))
        write_endpoint(self.temp.name, self.adapter.port, self.adapter.token)
        try:
            self.assertTrue(activate(self.temp.name, timeout=2))
        finally:
            clear_endpoint(self.temp.name)
        self.assertEqual([True], called)

    def test_activation_ignores_stale_endpoint(self):
        import socket

        from agent_framework.companion.activation import (
            activate,
            clear_endpoint,
            write_endpoint,
        )

        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        write_endpoint(self.temp.name, port, "stale-token")
        try:
            self.assertFalse(activate(self.temp.name, timeout=0.3))
        finally:
            clear_endpoint(self.temp.name)

    def test_assets_and_traversal(self):
        with urllib.request.urlopen(
            self.base + "/assets/tokens.css?t=" + self.adapter.token, timeout=10
        ) as response:
            self.assertEqual(200, response.status)
            self.assertIn(b"--accent", response.read())
        status, _ = self.request("/assets/../server.py")
        self.assertIn(status, (403, 404))


if __name__ == "__main__":
    unittest.main()
