import unittest
from unittest.mock import patch

from agent_framework.service import AgentService, extract_latest_user_message
from agent_service import AgentRequestHandler


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

    def test_service_calls_agent(self):
        service = AgentService(offline=True)
        self.assertEqual(service.chat("user-1", "计算 2 + 3"), "5")
        self.assertEqual(service.chat("user-2", "计算 4 * 5"), "20")

    def test_openclaw_timestamp_is_removed(self):
        service = AgentService(offline=True)
        prompt = "[Sat 2026-10-17 21:14 GMT+8] 计算 12 * (3 + 4)"
        self.assertEqual(service.chat("user-3", prompt), "84")

    def test_completion_payload(self):
        service = AgentService(offline=True)
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


if __name__ == "__main__":
    unittest.main()
