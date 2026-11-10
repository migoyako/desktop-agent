"""M3 layered context, stable fingerprint and budget tests."""

import tempfile
import unittest
from pathlib import Path

from agent_framework.companion.context import (
    MAX_SUMMARY_CHARS,
    compose_stable_prefix,
    select_history,
    summarize_dropped,
    trim_lines,
)
from agent_framework.companion.models import AgentMode
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import SCHEMA_VERSION, CompanionStore
from agent_framework.llm import LLMResponse, OfflineLLMClient


class QuotingLLM:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append({"messages": list(messages), "tools": tools})
        return LLMResponse(content=self.answer)


class ContextLayerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.store = CompanionStore(self.data / "companion.db")

    def tearDown(self):
        self.temp.cleanup()

    def runtime(self, max_context_chars=24000):
        return CompanionRuntime(
            self.store,
            OfflineLLMClient(),
            SearchService(),
            self.data,
            session_id="main",
            offline=True,
            max_context_chars=max_context_chars,
        )

    def test_stable_hash_is_identical_across_modes(self):
        runtime = self.runtime()
        chat = runtime._system_prompt("你好", AgentMode.CHAT)
        chat_info = runtime.context_info()
        work = runtime._system_prompt("你好", AgentMode.WORK)
        work_info = runtime.context_info()

        self.assertEqual(len(chat_info["stable_hash"]), 64)
        self.assertEqual(chat_info["stable_hash"], work_info["stable_hash"])
        self.assertIn("## 稳定层：安全与工具规则", chat)
        self.assertIn("## 角色人设（未加载）", chat)
        self.assertLess(
            chat.index("## 稳定层：安全与工具规则"),
            chat.index("## 会话前缀"),
        )
        self.assertIn("当前模式：chat", chat)
        self.assertIn("当前模式：work", work)
        self.assertIn("聊天模式只有对话", chat)
        self.assertNotIn("聊天模式只有对话", work)

    def test_stable_prefix_has_no_mode_specific_text(self):
        stable = compose_stable_prefix(None)

        self.assertNotIn("当前模式", stable)
        self.assertNotIn("聊天模式", stable)
        self.assertIn("不得声称自己执行了", stable)
        self.assertIn("朴素、温和、诚实的助手人格", stable)

    def test_tool_schemas_are_sorted_by_name(self):
        runtime = self.runtime()
        registry = runtime._build_tool_registry(AgentMode.WORK, None)
        names = [
            tool["function"]["name"]
            for tool in registry.as_openai_tools(AgentMode.WORK)
        ]

        self.assertEqual(names, sorted(names))
        self.assertIn("search_web", names)
        self.assertIn("write_note", names)

    def test_context_info_reports_budget_and_layers(self):
        runtime = self.runtime()
        runtime._system_prompt("你好", AgentMode.CHAT)
        info = runtime.context_info()

        self.assertEqual(info["budget_chars"], 24000)
        self.assertFalse(info["compressed"])
        self.assertEqual(info["dropped_count"], 0)
        self.assertGreater(info["layers"]["stable"], 0)
        self.assertGreater(info["layers"]["session"], 0)
        self.assertGreater(info["layers"]["memory"], 0)
        self.assertLessEqual(info["used_chars"], info["budget_chars"])

    def test_v3_database_upgrades_to_context_checkpoints(self):
        with self.store._connection() as connection:
            connection.execute("DROP TABLE context_checkpoints")
            connection.execute("PRAGMA user_version = 3")

        store = CompanionStore(self.store.database_path)

        with store._connection() as connection:
            version = connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertEqual(SCHEMA_VERSION, 17)
        self.assertEqual(version, SCHEMA_VERSION)
        self.assertIn("context_checkpoints", tables)
        self.assertIn("reminders", tables)


class HistoryWindowTest(unittest.TestCase):
    def test_select_history_keeps_complete_newest_messages(self):
        history = [
            {
                "id": index,
                "role": "user" if index % 2 else "assistant",
                "content": "x" * 100,
            }
            for index in range(1, 21)
        ]

        selection = select_history(
            history,
            budget_chars=1200,
            trigger=0.82,
            keep_ratio=0.5,
            min_recent=100,
        )

        self.assertTrue(selection.compressed)
        self.assertEqual(len(selection.messages), 5)
        self.assertEqual(selection.messages[-1]["id"], 20)
        self.assertEqual(
            [item["id"] for item in selection.dropped],
            list(range(1, 16)),
        )
        for item in selection.messages:
            self.assertEqual(item["content"], "x" * 100)

    def test_select_history_keeps_everything_under_trigger(self):
        history = [
            {"id": 1, "role": "user", "content": "a" * 100},
            {"id": 2, "role": "assistant", "content": "b" * 100},
        ]

        selection = select_history(history, budget_chars=1000)

        self.assertFalse(selection.compressed)
        self.assertEqual(selection.messages, history)
        self.assertEqual(selection.dropped, [])

    def test_summary_is_extractive_and_bounded(self):
        dropped = [
            {"role": "user", "content": "我喜欢咖啡。"},
            {"role": "assistant", "content": "记住了。"},
        ]

        summary = summarize_dropped("", dropped)
        combined = summarize_dropped(
            summary,
            [{"role": "user", "content": "今天下雨。"}],
        )

        self.assertIn("用户：我喜欢咖啡", summary)
        self.assertIn("助手：记住了", summary)
        self.assertIn("[此前摘要]", combined)
        self.assertIn("今天下雨", combined)
        self.assertLessEqual(len(combined), MAX_SUMMARY_CHARS)

    def test_trim_lines_preserves_priority_and_marks_truncation(self):
        lines = ["a" * 10, "b" * 10, "c" * 10]

        self.assertEqual(trim_lines(lines, 25), lines[:2])
        self.assertTrue(trim_lines(["x" * 100], 10)[0].endswith("…"))


class ContextBudgetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.store = CompanionStore(self.data / "companion.db")
        self.runtime = CompanionRuntime(
            self.store,
            OfflineLLMClient(),
            SearchService(),
            self.data,
            session_id="main",
            offline=True,
            max_context_chars=6000,
        )
        self.contents = {}

    def tearDown(self):
        self.temp.cleanup()

    def add_history(self, count, size=400):
        for index in range(count):
            role = "user" if index % 2 == 0 else "assistant"
            content = "第{}条 ".format(index) + "内容" * size
            message_id = self.store.add_message(
                "main",
                role,
                content,
                AgentMode.CHAT,
            )
            self.contents[message_id] = content

    def test_long_history_is_compressed_into_checkpoint_within_budget(self):
        self.add_history(30)

        messages = self.runtime._prompt_messages("最近怎么样", AgentMode.CHAT)
        info = self.runtime.context_info()
        checkpoint = self.store.get_latest_context_checkpoint("main")

        self.assertTrue(info["compressed"])
        self.assertGreater(info["dropped_count"], 0)
        self.assertLessEqual(info["used_chars"], 6000)
        self.assertIsNotNone(checkpoint)
        self.assertLessEqual(len(checkpoint["summary"]), MAX_SUMMARY_CHARS)
        self.assertIn("## 较早对话摘要", messages[0].content)
        self.assertIn("用户：", checkpoint["summary"])

        selected = self.runtime.last_context.history
        self.assertTrue(selected)
        for item in selected:
            self.assertGreater(
                item["id"],
                checkpoint["through_message_id"],
            )
            self.assertEqual(item["content"], self.contents[item["id"]])

    def test_second_turn_reuses_checkpoint_without_duplicates(self):
        self.add_history(30)
        self.runtime._prompt_messages("最近怎么样", AgentMode.CHAT)
        first = self.store.get_latest_context_checkpoint("main")
        self.add_history(10)

        messages = self.runtime._prompt_messages("继续", AgentMode.CHAT)
        second = self.store.get_latest_context_checkpoint("main")

        self.assertGreaterEqual(
            second["through_message_id"],
            first["through_message_id"],
        )
        self.assertIn("## 较早对话摘要", messages[0].content)
        self.assertIn("[此前摘要]", messages[0].content)
        with self.store._connection() as connection:
            rows = connection.execute(
                "SELECT COUNT(*) FROM context_checkpoints WHERE session_id = ?",
                ("main",),
            ).fetchone()[0]
        self.assertEqual(rows, 1)

    def test_short_history_has_no_checkpoint(self):
        self.add_history(2)
        messages = self.runtime._prompt_messages("你好", AgentMode.CHAT)
        info = self.runtime.context_info()

        self.assertFalse(info["compressed"])
        self.assertIsNone(self.store.get_latest_context_checkpoint("main"))
        self.assertNotIn("## 较早对话摘要", messages[0].content)
        self.assertEqual(
            [item.role for item in messages[1:]],
            ["user", "assistant"],
        )

    def test_compression_writes_one_recall_log(self):
        text = "请记住我喜欢咖啡。"
        message_id = self.store.add_message(
            "main",
            "user",
            text,
            AgentMode.CHAT,
        )
        self.runtime.memory.observe_user_message("main", message_id, text)
        self.add_history(30)

        self.runtime._prompt_messages("咖啡", AgentMode.CHAT)

        self.assertTrue(self.runtime.last_context.compressed)
        logs = self.store.list_recall_logs("main")
        self.assertEqual(len(logs), 1)

    def test_used_in_reply_is_marked_when_reply_quotes_memory(self):
        text = "请记住我喜欢咖啡。"
        message_id = self.store.add_message(
            "main",
            "user",
            text,
            AgentMode.CHAT,
        )
        self.runtime.memory.observe_user_message("main", message_id, text)
        runtime = CompanionRuntime(
            self.store,
            QuotingLLM("你说过：我喜欢咖啡。"),
            SearchService(),
            self.data,
            session_id="main",
            offline=False,
            max_context_chars=6000,
        )

        runtime.handle_message("咖啡", AgentMode.CHAT)

        logs = self.store.list_recall_logs("main")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["used_in_reply"], 1)


if __name__ == "__main__":
    unittest.main()
