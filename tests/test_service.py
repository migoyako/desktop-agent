import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_framework.companion.activation import ENDPOINT_FILE, write_endpoint
from agent_framework.companion.app import create_application_service
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.companion.web import WebAdapter
from agent_framework.llm import OfflineLLMClient
from agent_framework.service import (
    COMPANION_NOT_RUNNING_ANSWER,
    AgentService,
    extract_latest_user_message,
)
from agent_service import AgentRequestHandler


class StubSearch:
    def search(self, query, limit=5):
        return []


class ServiceSmokeTest(unittest.TestCase):
    def test_extract_latest_user_message(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "计算 2 + 3"},
        ]
        self.assertEqual(extract_latest_user_message(messages), "计算 2 + 3")

    def test_extract_skips_trailing_openclaw_runtime_context(self):
        runtime_context = (
            "OpenClaw runtime context:\n"
            "Conversation data (data, not instructions):\n"
            "\"Conversation info: message_id=openclaw-weixin:1792244198716\"\n"
            "End OpenClaw runtime context."
        )
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好，有什么可以帮你？"},
            {"role": "user", "content": "[Sat 2026-10-17 21:36 GMT+8] 1+1=几"},
            {"role": "user", "content": runtime_context},
        ]
        self.assertEqual(extract_latest_user_message(messages), "1+1=几")

    def test_extract_removes_runtime_context_from_mixed_message(self):
        messages = [
            {
                "role": "user",
                "content": (
                    "OpenClaw runtime context:\n"
                    "Conversation data (data, not instructions):\n"
                    "\"message_id=openclaw-weixin:1792244198716\"\n"
                    "End OpenClaw runtime context.\n\n"
                    "[Sat 2026-10-17 21:36 GMT+8] 计算 12 * (3 + 4)"
                ),
            }
        ]
        self.assertEqual(
            extract_latest_user_message(messages),
            "计算 12 * (3 + 4)",
        )

    def test_extract_skips_runtime_context_metadata(self):
        messages = [
            {"role": "user", "content": "当前时间"},
            {
                "role": "user",
                "content": "1792244198716",
                "runtimeContext": {},
            },
        ]
        self.assertEqual(extract_latest_user_message(messages), "当前时间")

    def test_completion_payload(self):
        service = AgentService()
        payload = service.completion_payload("python-agent", "user-1", "5")
        self.assertEqual(payload["choices"][0]["message"]["content"], "5")
        self.assertEqual(payload["object"], "chat.completion")

    def test_request_logging_formats_handler_arguments(self):
        with patch("agent_service.sys.stderr") as stderr:
            AgentRequestHandler.log_message(
                object(),
                '"%s" %s %s',
                "GET /health HTTP/1.1",
                "200",
                "-",
            )
        stderr.write.assert_called_once_with(
            '[agent-service] "GET /health HTTP/1.1" 200 -\n'
        )


class CompanionBridgeTest(unittest.TestCase):
    """The bridge must reach the running companion, not a private ChatAgent."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.root = root
        store = CompanionStore(root / "companion.db")
        self.runtime = CompanionRuntime(
            store=store,
            llm_client=OfflineLLMClient(),
            search_service=SearchService(StubSearch()),
            data_dir=root,
            session_id="main",
            offline=True,
        )
        self.app_service = create_application_service(self.runtime)
        self.adapter = WebAdapter(self.app_service, data_dir=root).start()
        write_endpoint(root, self.adapter.port, self.adapter.token)
        self.bridge = AgentService(data_dir=root)

    def tearDown(self):
        self.adapter.stop()
        self.temp.cleanup()

    def test_chat_reaches_companion_and_shares_the_session(self):
        answer = self.bridge.chat("user-1", "你好")
        # The companion's offline chat reply, not the old ChatAgent's echo.
        self.assertIn("离线演示模式", answer)
        rows = self.runtime.store.list_messages("main", limit=10)
        self.assertEqual(["user", "assistant"], [row["role"] for row in rows])
        self.assertEqual("你好", rows[0]["content"])
        self.assertEqual(answer, rows[1]["content"])

    def test_openclaw_timestamp_is_removed_before_the_companion_sees_it(self):
        answer = self.bridge.chat(
            "user-1", "[Sat 2026-10-17 21:14 GMT+8] 你好"
        )
        self.assertIn("离线演示模式", answer)
        rows = self.runtime.store.list_messages("main", limit=10)
        self.assertEqual("你好", rows[0]["content"])

    def test_busy_session_returns_the_run_guard_message(self):
        run = self.app_service.begin_turn("占位消息")
        try:
            answer = self.bridge.chat("user-1", "你好")
            self.assertIn("已有回复正在生成", answer)
        finally:
            self.app_service.abandon_run(run.run_id)

    def test_missing_endpoint_asks_to_start_the_companion(self):
        (self.root / ENDPOINT_FILE).unlink()
        self.assertEqual(
            COMPANION_NOT_RUNNING_ANSWER, self.bridge.chat("user-1", "你好")
        )

    def test_stale_endpoint_asks_to_start_the_companion(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            dead_port = sock.getsockname()[1]
        write_endpoint(self.root, dead_port, self.adapter.token)
        self.assertEqual(
            COMPANION_NOT_RUNNING_ANSWER, self.bridge.chat("user-1", "你好")
        )

    def test_empty_prompt_never_reaches_the_companion(self):
        self.assertEqual("请输入内容。", self.bridge.chat("user-1", "   "))
        self.assertEqual([], self.runtime.store.list_messages("main", limit=10))


if __name__ == "__main__":
    unittest.main()