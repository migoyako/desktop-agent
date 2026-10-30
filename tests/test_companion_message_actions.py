"""Regeneration and message-level memory commands (M12.3-f)."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_framework.companion.app import (
    CompanionApplicationService,
    MessageUnavailableError,
    SessionBusyError,
)
from agent_framework.companion.models import (
    AgentMode,
    TaskCard,
    WorkTask,
    new_id,
)
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.llm import LLMResponse


class StubSearch:
    def search(self, query, limit=5):
        return []


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append({"messages": list(messages), "tools": tools})
        if not self.responses:
            raise AssertionError("unexpected LLM call")
        return self.responses.pop(0)


class MessageActionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.data = root / "data"
        self.store = CompanionStore(self.data / "companion.db")
        self.llm = ScriptedLLM([])
        self.runtime = CompanionRuntime(
            store=self.store,
            llm_client=self.llm,
            search_service=SearchService(StubSearch()),
            data_dir=self.data,
            session_id="main",
            offline=False,
        )
        self.runtime.select_workspace(str(self.workspace))
        self.service = CompanionApplicationService(self.runtime)

    def tearDown(self):
        self.temp.cleanup()

    # -- helpers --------------------------------------------------------

    def answer(self, *contents):
        self.llm.responses.extend(LLMResponse(content=text) for text in contents)

    def rows(self):
        return self.store.list_messages("main", limit=20)

    def seed_turn(self, prompt="你好", answer="初版回答"):
        self.answer(answer)
        return self.service.run_sync(prompt)

    # -- regeneration ---------------------------------------------------

    def test_regenerate_replaces_the_last_reply_without_resending_the_user(self):
        self.seed_turn("你好", "初版回答")
        before = self.rows()
        old = before[-1]

        self.answer("重写回答")
        run = self.service.regenerate(old["id"])
        events = list(self.service.stream_turn(run))

        self.assertEqual("done", events[-1].kind)
        self.assertTrue(events[-1].payload["result"]["persisted"])
        after = self.rows()
        self.assertEqual(2, len(after))
        self.assertEqual(["user", "assistant"], [row["role"] for row in after])
        self.assertEqual("你好", after[0]["content"])
        self.assertEqual("重写回答", after[1]["content"])
        self.assertNotEqual(old["id"], after[1]["id"])
        self.assertEqual(after[1]["id"], events[-1].payload["result"]["message_id"])

    def test_only_the_trailing_reply_can_be_regenerated(self):
        self.answer("第一答", "第二答")
        self.service.run_sync("第一问")
        self.service.run_sync("第二问")
        first_reply = self.rows()[1]

        with self.assertRaises(MessageUnavailableError):
            self.service.regenerate(first_reply["id"])

    def test_a_reply_without_a_direct_user_parent_is_refused(self):
        self.seed_turn("你好", "回答")
        self.store.add_message("main", "assistant", "追加的说明", AgentMode.CHAT)
        last = self.rows()[-1]

        with self.assertRaises(MessageUnavailableError):
            self.service.regenerate(last["id"])

    def test_a_turn_with_side_effects_is_not_regenerated(self):
        self.seed_turn("帮我记住喝水", "好的")
        user = self.rows()[0]
        self.runtime.reminder_service.create(
            "main",
            "喝水",
            datetime.now(timezone.utc) + timedelta(hours=1),
            source_message_id=user["id"],
        )

        with self.assertRaises(MessageUnavailableError) as raised:
            self.service.regenerate(self.rows()[-1]["id"])
        self.assertIn("提醒", str(raised.exception))

    def test_side_effect_labels_come_from_every_durable_record(self):
        user_id = self.store.add_message("main", "user", "开工", AgentMode.WORK)
        self.assertEqual("", self.store.message_side_effect("main", user_id))

        self.store.create_work_task(
            WorkTask(
                task_id=new_id("task"),
                session_id="main",
                state="PLAN_REVIEW",
                card=TaskCard(
                    title="整理周报",
                    reason="用户要求",
                    spoken_summary="整理周报",
                    goal="整理周报",
                    executor="local",
                    project="",
                ),
                source_message_id=user_id,
            )
        )
        self.assertEqual("工作任务", self.store.message_side_effect("main", user_id))

        other_id = self.store.add_message("main", "user", "写笔记", AgentMode.WORK)
        proposal = self.runtime.notes.create_proposal(
            session_id="main",
            query="测试",
            content="内容",
            mode=AgentMode.WORK,
            source_message_id=other_id,
        )
        self.store.create_proposal(proposal)
        self.assertEqual(
            "待确认写入", self.store.message_side_effect("main", other_id)
        )

    def test_a_failed_replacement_restores_the_previous_reply(self):
        self.seed_turn("你好", "初版回答")
        old = self.rows()[-1]
        original = self.runtime.stream_regenerate

        def broken(*args, **kwargs):
            raise RuntimeError("stream exploded")
            yield  # pragma: no cover

        self.runtime.stream_regenerate = broken
        try:
            run = self.service.regenerate(old["id"])
            with self.assertRaises(RuntimeError):
                list(self.service.stream_turn(run))
        finally:
            self.runtime.stream_regenerate = original

        after = self.rows()
        self.assertEqual(2, len(after))
        self.assertEqual("初版回答", after[-1]["content"])
        self.assertEqual("assistant", after[-1]["role"])

    def test_regenerate_and_remember_wait_for_the_active_run(self):
        self.seed_turn("你好", "回答")
        assistant = self.rows()[-1]
        run = self.service.begin_turn("另一条消息")
        try:
            with self.assertRaises(SessionBusyError):
                self.service.regenerate(assistant["id"])
            with self.assertRaises(SessionBusyError):
                self.service.remember_message(assistant["id"])
        finally:
            self.service.abandon_run(run.run_id)

    # -- message actions projection -------------------------------------

    def test_projection_points_at_the_trailing_reply_only(self):
        self.seed_turn("你好", "回答")
        last = self.rows()[-1]

        actions = self.service.ui_projection().message_actions
        self.assertEqual(last["id"], actions.regenerate_id)
        self.assertEqual(0, actions.source_id)

        self.runtime.last_tool_sources = [
            {"title": "示例", "url": "https://example.com"}
        ]
        actions = self.service.ui_projection().message_actions
        self.assertEqual(last["id"], actions.source_id)

    def test_projection_withdraws_regeneration_for_a_side_effect_turn(self):
        self.seed_turn("帮我记住喝水", "好的")
        self.runtime.reminder_service.create(
            "main",
            "喝水",
            datetime.now(timezone.utc) + timedelta(hours=1),
            source_message_id=self.rows()[0]["id"],
        )

        self.assertEqual(
            0, self.service.ui_projection().message_actions.regenerate_id
        )

    # -- save as memory -------------------------------------------------

    def test_remember_assistant_reply_is_idempotent_and_has_no_evidence(self):
        self.seed_turn("你好", "值得保存的回答")
        assistant = self.rows()[-1]

        first = self.service.remember_message(assistant["id"])
        second = self.service.remember_message(assistant["id"])

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["memory_id"], second["memory_id"])
        memory = self.store.get_memory(first["memory_id"])
        self.assertEqual("confirmed", memory.status)
        self.assertEqual("值得保存的回答", memory.content)
        self.assertEqual(assistant["id"], memory.source_message_id)
        self.assertEqual(0, memory.evidence_count)

    def test_remember_user_message_keeps_evidence(self):
        message_id = self.store.add_message(
            "main", "user", "我的工位在靠窗的位置", AgentMode.CHAT
        )

        result = self.runtime.remember_message(message_id)

        memory = self.store.get_memory(result["memory_id"])
        self.assertEqual(message_id, memory.source_message_id)
        self.assertEqual(1, memory.evidence_count)
        self.assertEqual("confirmed", memory.status)


if __name__ == "__main__":
    unittest.main()
