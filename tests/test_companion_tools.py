"""M1b tests for the companion tool registry and native tool loop."""

import tempfile
import time
import unittest
from pathlib import Path

from agent_framework.companion.access_control import (
    CAPABILITY_FS_READ,
    CAPABILITY_FS_SEARCH,
    CAPABILITY_FS_WRITE,
)
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

        self.assertEqual(
            runtime.tool_names(AgentMode.CHAT),
            ["current_time", "read_tool_result", "set_reminder"],
        )
        self.assertEqual(
            runtime.tool_names(AgentMode.QUIET),
            ["current_time", "read_tool_result", "set_reminder"],
        )
        self.assertEqual(
            runtime.tool_names(AgentMode.WORK),
            [
                "current_time",
                "find_files",
                "open_path",
                "read_file",
                "read_tool_result",
                "search_web",
                "set_reminder",
                "write_note",
            ],
        )
        self.assertEqual(
            runtime.tool_names(AgentMode.EXECUTE),
            [
                "current_time",
                "find_files",
                "open_path",
                "read_file",
                "read_tool_result",
                "search_web",
                "set_reminder",
                "write_note",
            ],
        )

    def test_open_app_needs_a_launchable_grant_to_appear(self):
        # 「登记即授权」的清单为空时把 open_app 留在模型面前，等于给它一个只能失败的
        # 入口：它会编一个应用名出来。与 write_file 同一套规矩。
        runtime = self.runtime(ScriptedLLM([]))
        for mode in (AgentMode.WORK, AgentMode.EXECUTE):
            with self.subTest(mode=mode):
                self.assertNotIn("open_app", runtime.tool_names(mode))
        target = Path(self.workspace) / "demo.exe"
        target.write_bytes(b"MZ\x90\x00")
        row = runtime.app_catalog.register_manual("演示", str(target))
        runtime.access.grant_app(str(target))
        self.assertIn("open_app", runtime.tool_names(AgentMode.WORK))
        registry = runtime._build_tool_registry(AgentMode.WORK, None)
        offered = {tool.name: tool.description for tool in registry.available(AgentMode.WORK)}
        # 清单要出现在描述里：没有它模型只能猜一个 app_id。
        self.assertIn("演示", offered["open_app"])

    def test_model_creates_reminder_through_tool(self):
        llm = ScriptedLLM(
            [
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            name="set_reminder",
                            arguments={
                                "content": "站起来活动",
                                "when": "45分钟后",
                            },
                        )
                    ]
                ),
                LLMResponse(content="好，45 分钟后我会提醒你站起来活动。"),
            ]
        )
        runtime = self.runtime(llm)

        result = runtime.handle_message(
            "等我忙完这阵提醒我活动一下",
            AgentMode.CHAT,
        )

        self.assertIn("提醒你站起来活动", result["answer"])
        pending = runtime.list_reminders("pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].content, "站起来活动")
        tool_text = self.tool_messages(llm.calls[1])[0].content
        self.assertIn("已创建提醒", tool_text)

    def test_reminder_tool_rejects_unparsable_time(self):
        llm = ScriptedLLM(
            [
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            name="set_reminder",
                            arguments={"content": "喝水", "when": "待会儿"},
                        )
                    ]
                ),
                LLMResponse(content="你想让我什么时候提醒你？"),
            ]
        )
        runtime = self.runtime(llm)

        runtime.handle_message("待会儿提醒我喝水", AgentMode.CHAT)

        self.assertEqual(runtime.list_reminders("pending"), [])
        self.assertIn(
            "没有听懂时间表达",
            self.tool_messages(llm.calls[1])[0].content,
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
        self.assertEqual(runtime.last_tool_sources[0]["title"], "Example result")

    def test_tool_events_carry_arguments_sources_and_result_handles(self):
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
                LLMResponse(content="搜索完成。"),
            ]
        )
        runtime = self.runtime(llm)

        events = list(runtime.stream_message("了解一下 Python Agent", AgentMode.WORK))

        tool_events = [event for event in events if event.get("kind") == "tool"]
        self.assertEqual(
            [event["state"] for event in tool_events],
            ["running", "done"],
        )
        running, done = tool_events
        self.assertEqual(running["arguments"]["query"], "Python Agent")
        self.assertEqual(done["arguments"]["query"], "Python Agent")
        self.assertEqual(done["sources"][0]["title"], "Example result")
        self.assertEqual(done["sources"][0]["url"], "https://example.com/result")
        self.assertTrue(done["ok"])
        self.assertEqual(done["error"], "")
        self.assertEqual(runtime.last_tool_sources[0]["title"], "Example result")

    def test_failed_tool_events_report_the_error(self):
        llm = ScriptedLLM(
            [
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            name="search_web",
                            arguments={"query": "network"},
                        )
                    ]
                ),
                LLMResponse(content="搜索暂时不可用。"),
            ]
        )
        runtime = self.runtime(llm, backend=FailingSearch())

        events = list(runtime.stream_message("了解一下最新动态", AgentMode.WORK))

        tool_events = [event for event in events if event.get("kind") == "tool"]
        self.assertEqual(tool_events[-1]["state"], "error")
        self.assertFalse(tool_events[-1]["ok"])
        self.assertIn("network unavailable", tool_events[-1]["error"])
        self.assertTrue(tool_events[-1]["code"])

    def test_model_write_tool_executes_by_default(self):
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
        runtime.apply_settings({"tool_permission": "balanced"})
        runtime.set_mode(AgentMode.WORK)

        result = runtime.handle_message("把这段讨论整理成笔记", AgentMode.WORK)

        self.assertIsNone(result["proposal"])
        self.assertEqual([], runtime.pending_proposals())
        self.assertIn("笔记已写入", result["answer"])
        receipts = runtime.latest_receipts(3)
        self.assertEqual(1, len(receipts))
        self.assertEqual("completed", receipts[0].status)
        self.assertIn("回执：rcp_", result["answer"])
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


    def test_tool_round_limit_still_allows_final_answer(self):
        llm = ScriptedLLM(
            [
                LLMResponse(
                    tool_calls=[ToolCall(name="current_time", arguments={})]
                )
                for _ in range(MAX_TOOL_ROUNDS)
            ]
            + [LLMResponse(content="根据工具结果，现在是 08:00。")]
        )
        runtime = self.runtime(llm)

        result = runtime.handle_message("现在几点", AgentMode.CHAT)

        self.assertIn("08:00", result["answer"])
        self.assertEqual(len(llm.calls), MAX_TOOL_ROUNDS + 1)
        self.assertIsNone(llm.calls[-1]["tools"])
        tool_messages = self.tool_messages(llm.calls[-1])
        self.assertEqual(len(tool_messages), MAX_TOOL_ROUNDS)

    def test_tool_round_limit_without_final_answer(self):
        llm = ScriptedLLM(
            [
                LLMResponse(
                    tool_calls=[ToolCall(name="current_time", arguments={})]
                )
                for _ in range(MAX_TOOL_ROUNDS)
            ]
            + [LLMResponse(content="")]
        )
        runtime = self.runtime(llm)

        result = runtime.handle_message("现在几点", AgentMode.CHAT)

        self.assertIn("轮次", result["answer"])
        self.assertEqual(len(llm.calls), MAX_TOOL_ROUNDS + 1)

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
                            name="open_the_fridge",
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
        runtime.apply_settings({"tool_permission": "strict"})
        runtime.set_mode(AgentMode.WORK)

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


class PermissionTierRegistryTest(unittest.TestCase):
    """The 高级 panel's tier has to reach the registry, not just the settings row."""

    def build(self, tier):
        self.ran = []

        def handler(name):
            def run(arguments):
                self.ran.append(name)
                # 直接执行、不生成提案：ASK 决策必须拒绝这个结果，ALLOW 才放行。
                return ToolResult.success("已执行 {}".format(name))

            return run

        registry = CompanionToolRegistry(policy_tier=tier)
        for name, risk in (
            ("write_note", RiskLevel.FS_WRITE),
            ("run_command", RiskLevel.SHELL),
            ("type_into_app", RiskLevel.INPUT_CONTROL),
            ("read_file", RiskLevel.FS_READ),
        ):
            registry.register(
                CompanionTool(
                    name=name,
                    description=name,
                    parameters={"type": "object", "properties": {}},
                    risk=risk,
                    handler=handler(name),
                )
            )
        return registry

    def test_strict_removes_the_dangerous_tools_from_the_list(self):
        registry = self.build("strict")
        names = [tool.name for tool in registry.available(AgentMode.EXECUTE)]
        self.assertNotIn("write_note", names)
        self.assertNotIn("run_command", names)
        self.assertNotIn("type_into_app", names)
        result = registry.execute("run_command", {}, AgentMode.EXECUTE)
        self.assertFalse(result.ok)
        self.assertEqual("mode_denied", result.code)
        # 被拒绝的工具连处理函数都不该跑到，这是档位唯一真正生效的地方。
        self.assertEqual([], self.ran)

    def test_relaxed_runs_writes_but_still_asks_for_the_rest(self):
        registry = self.build("relaxed")
        done = registry.execute("write_note", {}, AgentMode.EXECUTE)
        self.assertTrue(done.ok)

        for name in ("run_command", "type_into_app"):
            with self.subTest(name=name):
                asked = registry.execute(name, {}, AgentMode.EXECUTE)
                self.assertFalse(asked.ok)
                self.assertEqual("confirmation_missing", asked.code)

    def test_balanced_runs_writes_and_still_asks_for_dangerous_tools(self):
        registry = self.build("balanced")
        done = registry.execute("write_note", {}, AgentMode.EXECUTE)
        self.assertTrue(done.ok)
        for name in ("run_command", "type_into_app"):
            with self.subTest(name=name):
                result = registry.execute(name, {}, AgentMode.EXECUTE)
                self.assertFalse(result.ok)
                self.assertEqual("confirmation_missing", result.code)

    def test_no_tier_opens_a_tool_the_mode_already_closed(self):
        for tier in ("strict", "balanced", "relaxed"):
            registry = self.build(tier)
            with self.subTest(tier=tier):
                # 陪伴模式里这四个工具全部关着：档位不能把它们放出来。
                self.assertEqual([], [tool.name for tool in registry.available(AgentMode.CHAT)])
                denied = registry.execute("write_note", {}, AgentMode.CHAT)
                self.assertFalse(denied.ok)
                self.assertEqual("mode_denied", denied.code)

    def test_an_unknown_tier_is_the_shipped_matrix(self):
        for tier in (None, "", "信任"):
            registry = self.build(tier)
            with self.subTest(tier=tier):
                self.assertEqual(
                    [tool.name for tool in registry.available(AgentMode.EXECUTE)],
                    [tool.name for tool in self.build("balanced").available(AgentMode.EXECUTE)],
                )


class PermissionTierTurnTest(unittest.TestCase):
    """An authorized write runs directly in balanced and relaxed tiers."""

    CONTENT = "# 会议摘要\n\n- 讨论了权限档位"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.data = self.root / "data"

    def run_write(self, tier):
        llm = ScriptedLLM(
            [
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            name="write_note",
                            arguments={"query": "会议摘要", "content": self.CONTENT},
                        )
                    ]
                ),
                LLMResponse(content="写入被保守档禁用。"),
            ]
        )
        runtime = CompanionRuntime(
            CompanionStore(self.data / "companion.db"),
            llm,
            SearchService(StubSearch()),
            self.data,
            offline=False,
        )
        runtime.select_workspace(str(self.workspace))
        runtime.apply_settings({"tool_permission": tier})
        return runtime, runtime.handle_message("把这段讨论整理成笔记", AgentMode.WORK)

    def assistant_messages(self, runtime):
        return [row for row in runtime.history() if row.get("role") == "assistant"]

    def test_balanced_and_relaxed_write_without_a_confirmation_card(self):
        for tier in ("balanced", "relaxed"):
            with self.subTest(tier=tier):
                self.setUp()
                runtime, result = self.run_write(tier)

                self.assertIsNone(result["proposal"])
                self.assertEqual([], runtime.pending_proposals())
                files = list(self.workspace.rglob("*.md"))
                self.assertEqual(1, len(files))
                self.assertEqual(
                    self.CONTENT, files[0].read_text(encoding="utf-8")
                )
                receipts = runtime.latest_receipts(3)
                self.assertEqual(1, len(receipts))
                self.assertEqual("completed", receipts[0].status)
                self.assertIn("回执", result["answer"])
                self.assertEqual(1, len(self.assistant_messages(runtime)))
                self.assertEqual(
                    result["answer"],
                    self.assistant_messages(runtime)[0]["content"],
                )

    def test_the_auto_write_is_undone_through_the_same_receipt(self):
        runtime, _ = self.run_write("relaxed")
        receipt = runtime.latest_receipts(3)[0]

        runtime.undo_receipt(receipt.receipt_id)

        self.assertEqual([], list(self.workspace.rglob("*.md")))

    def test_strict_disables_write_without_creating_a_card(self):
        self.setUp()
        runtime, result = self.run_write("strict")
        self.assertIsNone(result["proposal"])
        self.assertEqual([], runtime.pending_proposals())
        self.assertEqual([], list(self.workspace.rglob("*.md")))

    def test_explicit_file_write_request_forces_write_file_tool(self):
        runtime = CompanionRuntime(
            CompanionStore(self.data / "companion.db"),
            ScriptedLLM([]),
            SearchService(StubSearch()),
            self.data,
            offline=True,
        )
        runtime.access.grant_directory(
            self.workspace,
            [
                CAPABILITY_FS_READ,
                CAPABILITY_FS_SEARCH,
                CAPABILITY_FS_WRITE,
            ],
        )
        choice = runtime._forced_write_tool_choice(
            "在123.txt恭维我两句话",
            AgentMode.WORK,
        )
        self.assertEqual(
            choice,
            {
                "type": "function",
                "function": {"name": "write_file"},
            },
        )
        runtime.apply_settings({"tool_permission": "strict"})
        self.assertIsNone(
            runtime._forced_write_tool_choice(
                "在123.txt恭维我两句话",
                AgentMode.WORK,
            )
        )

    def test_explicit_filename_wins_over_a_historical_parallel_write(self):
        runtime = CompanionRuntime(
            CompanionStore(self.data / "companion.db"),
            ScriptedLLM(
                [
                    LLMResponse(
                        tool_calls=[
                            ToolCall(
                                name="write_file",
                                arguments={
                                    "relative_path": "123.txt",
                                    "content": "wrong target",
                                },
                            ),
                            ToolCall(
                                name="write_file",
                                arguments={
                                    "relative_path": "new-result.txt",
                                    "content": "right target",
                                },
                            ),
                        ]
                    )
                ]
            ),
            SearchService(StubSearch()),
            self.data,
            offline=False,
        )
        runtime.access.grant_directory(
            self.workspace,
            [
                CAPABILITY_FS_READ,
                CAPABILITY_FS_SEARCH,
                CAPABILITY_FS_WRITE,
            ],
        )
        runtime.apply_settings({"tool_permission": "balanced"})
        runtime.set_mode(AgentMode.WORK)

        result = runtime.handle_message(
            "在 new-result.txt 中写入：right target",
            AgentMode.WORK,
        )

        self.assertFalse((self.workspace / "123.txt").exists())
        self.assertEqual(
            "right target",
            (self.workspace / "new-result.txt").read_text(encoding="utf-8"),
        )
        self.assertIn("回执：rcp_", result["answer"])


if __name__ == "__main__":
    unittest.main()
