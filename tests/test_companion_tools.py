"""M1b tests for the companion tool registry and native tool loop."""

import tempfile
import time
import unittest
from pathlib import Path

from agent_framework.companion.models import AgentMode, RiskLevel, SearchResult
from agent_framework.companion.runtime import (
    MAX_TOOL_CALLS,
    MAX_TOOL_ROUNDS,
    CompanionRuntime,
)
from agent_framework.companion.search import SearchError, SearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.companion.tools import (
    CompanionTool,
    CompanionToolRegistry,
    ToolResult,
)
from agent_framework.llm import LLMResponse, ToolCall


class StubSearch:
    def search(self, query, limit=5):
        self.last_query = query
        return [
            SearchResult(
                title="Example result",
                url="https://example.com/result",
                snippet="A short search summary.",
            )
        ]


class FailingSearch:
    def search(self, query, limit=5):
        raise SearchError("network unavailable")


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append({"messages": list(messages), "tools": tools})
        if not self.responses:
            raise AssertionError("unexpected LLM call")
        return self.responses.pop(0)


class UnsupportedToolsLLM:
    def __init__(self):
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append(tools)
        if tools:
            raise RuntimeError("This model does not support tools")
        return LLMResponse(content="普通对话回答。")


class BrokenLLM:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        raise RuntimeError("connection reset by peer")


class CompanionToolTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.data = self.root / "data"

    def tearDown(self):
        self.temp.cleanup()

    def runtime(self, llm, backend=None, workspace=True):
        runtime = CompanionRuntime(
            CompanionStore(self.data / "companion.db"),
            llm,
            SearchService(backend or StubSearch()),
            self.data,
            offline=False,
        )
        if workspace:
            runtime.select_workspace(str(self.workspace))
        return runtime

    def tool_messages(self, call):
        return [
            message
            for message in call["messages"]
            if message.role == "tool"
        ]

    def test_tools_are_scoped_by_mode(self):
        runtime = self.runtime(ScriptedLLM([]))

        self.assertEqual(runtime.tool_names(AgentMode.CHAT), ["current_time"])
        self.assertEqual(runtime.tool_names(AgentMode.QUIET), ["current_time"])
        self.assertEqual(
            runtime.tool_names(AgentMode.WORK),
            ["current_time", "search_web", "write_note"],
        )
        self.assertEqual(
            runtime.tool_names(AgentMode.EXECUTE),
            ["current_time", "search_web", "write_note"],
        )

    def test_model_can_search_and_answer(self):
        llm = ScriptedLLM(
            [
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            name="search_web",
                            arguments={"query": "Python Agent"},
                        )
                    ]
                ),
                LLMResponse(content="根据搜索结果，Python Agent 是一个项目。"),
            ]
        )
        runtime = self.runtime(llm)

        result = runtime.handle_message(
            "帮我了解一下 Python Agent 的最新动态",
            AgentMode.WORK,
        )

        self.assertIn("Python Agent 是一个项目", result["answer"])
        self.assertIsNone(result["proposal"])
        self.assertEqual(len(llm.calls), 2)
        names = [
            item["function"]["name"]
            for item in llm.calls[0]["tools"]
        ]
        self.assertIn("search_web", names)
        messages = self.tool_messages(llm.calls[1])
        self.assertTrue(messages)
        self.assertIn("Example result", messages[0].content)
        self.assertEqual(list(self.workspace.rglob("*.md")), [])

    def test_model_write_tool_creates_proposal_without_writing(self):
        content = "# 会议摘要\n\n- 讨论了工具循环"
        llm = ScriptedLLM(
            [
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            name="write_note",
                            arguments={
                                "query": "会议摘要",
                                "content": content,
                            },
                        )
                    ]
                )
            ]
        )
        runtime = self.runtime(llm)

        result = runtime.handle_message("把这段讨论整理成笔记", AgentMode.WORK)

        proposal = result["proposal"]
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.intent, "保存笔记")
        self.assertIn("内容预览", proposal.user_visible_plan)
        self.assertIn("讨论了工具循环", proposal.user_visible_plan)
        self.assertEqual(list(self.workspace.rglob("*.md")), [])
        self.assertEqual(
            [item.action_id for item in runtime.pending_proposals()],
            [proposal.action_id],
        )

        confirmed = runtime.confirm_proposal(proposal.action_id, AgentMode.WORK)
        self.assertEqual(confirmed["receipt"].status, "completed")
        files = list(self.workspace.rglob("*.md"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_text(encoding="utf-8"), content)

    def test_chat_mode_denies_write_tool_call(self):
        llm = ScriptedLLM(
            [
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            name="write_note",
                            arguments={"query": "x", "content": "y"},
                        )
                    ]
                ),
                LLMResponse(content="陪伴模式不能写文件。"),
            ]
        )
        runtime = self.runtime(llm)

        result = runtime.handle_message("保存成笔记", AgentMode.CHAT)

        self.assertIsNone(result["proposal"])
        self.assertIn("陪伴模式", result["answer"])
        messages = self.tool_messages(llm.calls[1])
        self.assertIn("不允许", messages[0].content)
        names = [
            item["function"]["name"]
            for item in llm.calls[0]["tools"]
        ]
        self.assertNotIn("write_note", names)
        self.assertEqual(list(self.workspace.rglob("*.md")), [])

    def test_tool_call_limit(self):
        responses = [
            LLMResponse(
                tool_calls=[
                    ToolCall(name="current_time", arguments={}),
                    ToolCall(name="current_time", arguments={}),
                ]
            )
            for _ in range(MAX_TOOL_ROUNDS)
        ]
        llm = ScriptedLLM(responses)
        runtime = self.runtime(llm)

        result = runtime.handle_message("现在几点", AgentMode.CHAT)

        self.assertIn("次数", result["answer"])
        self.assertEqual(len(llm.calls), MAX_TOOL_ROUNDS)


    def test_tool_round_limit(self):
        llm = ScriptedLLM(
            [
                LLMResponse(
                    tool_calls=[ToolCall(name="current_time", arguments={})]
                )
                for _ in range(MAX_TOOL_ROUNDS)
            ]
        )
        runtime = self.runtime(llm)

        result = runtime.handle_message("现在几点", AgentMode.CHAT)

        self.assertIn("轮次", result["answer"])
        self.assertEqual(len(llm.calls), MAX_TOOL_ROUNDS)

    def test_search_failure_is_fed_back(self):
        llm = ScriptedLLM(
            [
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            name="search_web",
                            arguments={"query": "x"},
                        )
                    ]
                ),
                LLMResponse(content="搜索暂时不可用。"),
            ]
        )
        runtime = self.runtime(llm, backend=FailingSearch())

        result = runtime.handle_message(
            "帮我了解一下 x 的最新情况",
            AgentMode.WORK,
        )

        self.assertIn("搜索暂时不可用", result["answer"])
        self.assertIsNone(result["proposal"])
        messages = self.tool_messages(llm.calls[1])
        self.assertIn("network unavailable", messages[0].content)

    def test_unknown_tool_is_fed_back(self):
        llm = ScriptedLLM(
            [
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            name="read_file",
                            arguments={"path": "x"},
                        )
                    ]
                ),
                LLMResponse(content="我不能读取这个文件。"),
            ]
        )
        runtime = self.runtime(llm)

        result = runtime.handle_message("读取 x", AgentMode.WORK)

        self.assertIn("我不能读取这个文件", result["answer"])
        messages = self.tool_messages(llm.calls[1])
        self.assertIn("未知工具", messages[0].content)

    def test_write_without_workspace_is_rejected(self):
        llm = ScriptedLLM(
            [
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            name="write_note",
                            arguments={"query": "笔记", "content": "内容"},
                        )
                    ]
                ),
                LLMResponse(content="请先选择一个授权工作目录。"),
            ]
        )
        runtime = self.runtime(llm, workspace=False)

        result = runtime.handle_message("保存成笔记", AgentMode.WORK)

        self.assertIsNone(result["proposal"])
        self.assertIn("请先选择", result["answer"])
        messages = self.tool_messages(llm.calls[1])
        self.assertIn("工作目录", messages[0].content)

    def test_explicit_search_uses_regex_fast_path(self):
        llm = ScriptedLLM([])
        runtime = self.runtime(llm)

        result = runtime.handle_message(
            "搜索 Python 并保存为笔记",
            AgentMode.WORK,
        )

        self.assertIsNotNone(result["proposal"])
        self.assertEqual(llm.calls, [])
        self.assertEqual(list(self.workspace.rglob("*.md")), [])

    def test_unsupported_tool_calling_falls_back(self):
        llm = UnsupportedToolsLLM()
        runtime = self.runtime(llm)

        result = runtime.handle_message("帮我规划今天的工作", AgentMode.WORK)

        self.assertIn("普通对话回答", result["answer"])
        self.assertIn("不支持工具调用", result["answer"])
        self.assertIsNone(result["proposal"])
        self.assertEqual(len(llm.calls), 2)
        self.assertTrue(llm.calls[0])
        self.assertIsNone(llm.calls[1])

        second = runtime.handle_message("继续聊聊", AgentMode.WORK)

        self.assertIn("普通对话回答", second["answer"])
        self.assertNotIn("不支持工具调用", second["answer"])
        self.assertEqual(len(llm.calls), 3)
        self.assertIsNone(llm.calls[2])

    def test_regex_search_still_works_without_tool_calling(self):
        llm = UnsupportedToolsLLM()
        runtime = self.runtime(llm)
        runtime.tool_calling_supported = False

        result = runtime.handle_message(
            "搜索 Python 并保存为笔记",
            AgentMode.WORK,
        )

        self.assertIsNotNone(result["proposal"])
        self.assertEqual(llm.calls, [])

    def test_unrelated_llm_error_is_not_masked(self):
        llm = BrokenLLM()
        runtime = self.runtime(llm)

        result = runtime.handle_message("随便聊聊", AgentMode.WORK)

        self.assertIn("connection reset", result["answer"])
        self.assertNotIn("不支持工具调用", result["answer"])
        self.assertEqual(llm.calls, 1)

    def test_slow_tool_times_out(self):
        registry = CompanionToolRegistry()
        registry.register(
            CompanionTool(
                name="slow",
                description="test",
                parameters={"type": "object", "properties": {}},
                risk=RiskLevel.SAFE,
                timeout=0.05,
                handler=lambda arguments: (
                    time.sleep(0.3),
                    ToolResult.success("finished"),
                )[1],
            )
        )

        result = registry.execute("slow", {}, AgentMode.WORK)

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "tool_timeout")
        self.assertIn("超时", result.content)


if __name__ == "__main__":
    unittest.main()
