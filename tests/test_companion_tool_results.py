"""M3 tests for tool-result spill, read-back and the read guard."""

import re
import tempfile
import unittest
from pathlib import Path

from agent_framework.companion.models import AgentMode, SearchResult
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.store import CompanionStore
from agent_framework.companion.tools import ToolResult
from agent_framework.llm import LLMResponse, OfflineLLMClient, ToolCall


RESULT_ID = re.compile(r"tr_[0-9a-f]{32}")


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append({"messages": list(messages), "tools": tools})
        return self.responses.pop(0)


class LargeSearch:
    def search(self, query, limit=5):
        return [
            SearchResult(
                title="R{} ".format(index) + "T" * 3000,
                url="https://example.com/{}".format(index),
                snippet="short",
            )
            for index in range(10)
        ]


class ToolResultSpillTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.store = CompanionStore(self.data / "companion.db")

    def tearDown(self):
        self.temp.cleanup()

    def runtime(self, llm=None, offline=True):
        return CompanionRuntime(
            self.store,
            llm or OfflineLLMClient(),
            LargeSearch(),
            self.data,
            session_id="main",
            offline=offline,
        )

    def result_id_of(self, text):
        match = RESULT_ID.search(text)
        self.assertIsNotNone(match, text[:200])
        return match.group(0)

    def test_small_result_is_returned_unchanged(self):
        runtime = self.runtime()
        result = ToolResult.success("short")

        self.assertIs(runtime._maybe_spill(result, "search_web"), result)

    def test_large_result_spills_and_previews_only_head(self):
        runtime = self.runtime()
        content = "HEAD" + "x" * 20000 + "TAIL_END"

        spilled = runtime._maybe_spill(
            ToolResult.success(content),
            "search_web",
        )

        self.assertTrue(spilled.ok)
        self.assertIn("[工具结果已落盘]", spilled.content)
        self.assertIn("不算已读", spilled.content)
        self.assertNotIn("TAIL_END", spilled.content)
        result_id = self.result_id_of(spilled.content)
        self.assertTrue(
            (self.data / "tool_results" / "{}.json".format(result_id)).exists()
        )

        head = runtime._tool_read_result({"result_id": result_id})
        self.assertTrue(head.ok)
        self.assertIn("HEAD", head.content)
        self.assertIn("剩余未返回", head.content)
        self.assertNotIn("TAIL_END", head.content)

        tail = runtime._tool_read_result(
            {
                "result_id": result_id,
                "offset": len(content) - 50,
                "limit": 100,
            }
        )
        self.assertTrue(tail.ok)
        self.assertIn("TAIL_END", tail.content)
        self.assertIn("剩余未返回：0", tail.content)

    def test_threshold_counts_utf8_bytes(self):
        runtime = self.runtime()
        content = "中" * 6000

        spilled = runtime._maybe_spill(
            ToolResult.success(content),
            "search_web",
        )

        self.assertIn("result_id", spilled.content)
        self.assertNotEqual(spilled.content, content)

    def test_read_back_is_session_scoped(self):
        runtime = self.runtime()
        descriptor = runtime.tool_results.save(
            "other-session",
            "search_web",
            "secret",
        )

        result = runtime._tool_read_result(
            {"result_id": descriptor["result_id"]}
        )

        self.assertFalse(result.ok)
        self.assertIn("不属于当前会话", result.content)

    def test_invalid_handle_and_offset_are_rejected(self):
        runtime = self.runtime()
        spilled = runtime._maybe_spill(
            ToolResult.success("x" * 20000),
            "search_web",
        )
        result_id = self.result_id_of(spilled.content)

        traversal = runtime._tool_read_result(
            {"result_id": "../../companion.db"}
        )
        out_of_range = runtime._tool_read_result(
            {"result_id": result_id, "offset": 10**9}
        )

        self.assertFalse(traversal.ok)
        self.assertIn("句柄无效", traversal.content)
        self.assertFalse(out_of_range.ok)
        self.assertIn("offset 超出范围", out_of_range.content)

    def test_result_survives_runtime_restart(self):
        runtime = self.runtime()
        spilled = runtime._maybe_spill(
            ToolResult.success("RESTART_HEAD" + "y" * 20000),
            "search_web",
        )
        result_id = self.result_id_of(spilled.content)

        reopened = self.runtime()

        result = reopened._tool_read_result({"result_id": result_id})
        self.assertTrue(result.ok)
        self.assertIn("RESTART_HEAD", result.content)

    def test_read_tool_result_is_safe_in_chat_mode(self):
        runtime = self.runtime()

        self.assertIn("read_tool_result", runtime.tool_names(AgentMode.CHAT))

    def test_system_prompt_states_unread_content_cannot_be_claimed(self):
        runtime = self.runtime()

        prompt = runtime._system_prompt("你好", AgentMode.CHAT)

        self.assertIn("未返回部分不得假设", prompt)
        self.assertIn("也不得声称已经读取", prompt)


class ToolLoopSpillTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.store = CompanionStore(self.data / "companion.db")
        self.llm = ScriptedLLM(
            [
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            name="search_web",
                            arguments={"query": "量子计算"},
                        )
                    ]
                ),
                LLMResponse(content="已完成。"),
            ]
        )
        self.runtime = CompanionRuntime(
            self.store,
            self.llm,
            LargeSearch(),
            self.data,
            session_id="main",
            offline=False,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_large_tool_output_is_spilled_before_entering_context(self):
        result = self.runtime.handle_message(
            "量子计算的最新进展",
            AgentMode.WORK,
        )

        self.assertEqual(result["answer"], "已完成。")
        self.assertIn("read_tool_result", [
            item["function"]["name"]
            for item in self.llm.calls[0]["tools"]
        ])
        tool_messages = [
            message
            for message in self.llm.calls[1]["messages"]
            if message.role == "tool"
        ]
        self.assertEqual(len(tool_messages), 1)
        content = tool_messages[0].content
        self.assertIn("[工具结果已落盘]", content)
        self.assertIn("tr_", content)
        self.assertNotIn("T" * 3000, content)
        self.assertLess(len(content), 8000)


if __name__ == "__main__":
    unittest.main()
