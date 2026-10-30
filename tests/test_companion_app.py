"""Application-layer contracts shared by desktop and future transports."""

import tempfile
import unittest
from pathlib import Path

from agent_framework.companion.app import (
    ApprovalUnavailableError,
    CompanionApplicationService,
    RunAlreadyActiveError,
    SessionBusyError,
    TurnPersistenceError,
    create_application_service,
)
from agent_framework.companion.context import PromptBuild
from agent_framework.companion.models import AgentMode
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.llm import OfflineLLMClient


def context_build(used_chars, budget_chars, compressed=False):
    return PromptBuild(
        system="",
        history=[],
        stable_hash="",
        layers={},
        budget_chars=budget_chars,
        used_chars=used_chars,
        history_chars=used_chars,
        compressed=compressed,
        dropped_count=0,
        checkpoint_through=0,
    )


class MemoryMessageStore:
    def __init__(self):
        self.rows = []
        self.proposals = {}

    def add_assistant(self, content, session_id="main"):
        message_id = len(self.rows) + 1
        self.rows.append(
            {
                "id": message_id,
                "session_id": session_id,
                "role": "assistant",
                "content": content,
                "mode": AgentMode.CHAT.value,
                "created_at": "2026-10-28T12:00:00",
            }
        )
        return message_id

    def list_messages(self, session_id, limit=30):
        rows = [row for row in self.rows if row["session_id"] == session_id]
        return rows[-max(1, int(limit)) :]

    def get_proposal(self, action_id):
        return self.proposals.get(action_id)

    def ensure_session(self, session_id, mode):
        return session_id


class FakeRuntime:
    def __init__(self, root):
        self.session_id = "main"
        self.store = MemoryMessageStore()
        self.mode = AgentMode.CHAT
        self.workspace_path = str(root)
        self.stream_factory = None
        self.cancel_event = None
        self.mode_calls = []

    def stream_message(
        self,
        text,
        mode=AgentMode.CHAT,
        generation_id="",
        cancel_event=None,
    ):
        self.cancel_event = cancel_event
        yield from self.stream_factory(self, cancel_event)

    def set_mode(self, mode):
        self.mode = AgentMode(mode)
        self.mode_calls.append(self.mode)

    def current_mode(self):
        return self.mode

    def select_workspace(self, path):
        self.workspace_path = str(path)
        return Path(path)

    def clear_workspace(self):
        self.workspace_path = ""

    def switch_session(self, session_id):
        if session_id == self.session_id:
            return False
        self.session_id = session_id
        return True

    def rename_session(self, session_id, title):
        return bool(str(title or "").strip())

    def set_session_pinned(self, session_id, pinned=True):
        return True

    def set_session_unread(self, session_id, unread=True):
        return True

    def delete_session(self, session_id):
        return False

    def draft(self, session_id=None):
        return ""

    def save_draft(self, session_id, content):
        return True

    def workspace(self):
        return self.workspace_path

    def history(self, limit=60):
        return self.store.list_messages(self.session_id, limit)

    def session_summaries(self):
        return []

    def pending_proposals(self):
        return []

    def latest_receipts(self, limit=20):
        return []


class ApplicationServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = FakeRuntime(self.root)
        self.service = CompanionApplicationService(
            self.runtime,
            run_id_factory=iter(("run-1", "run-2")).__next__,
        )

    def tearDown(self):
        self.temp.cleanup()

    def persist_default_done(self, answer):
        def script(runtime, cancel_event):
            yield {"kind": "delta", "text": answer[:1]}
            runtime.store.add_assistant(answer)
            yield {
                "kind": "done",
                "result": {
                    "answer": answer,
                    "proposal": None,
                    "receipt": None,
                    "task": None,
                    "task_receipt": None,
                },
            }

        self.runtime.stream_factory = script

    def test_stream_turn_orders_events_and_confirms_persistence(self):
        self.persist_default_done("你好")

        run = self.service.begin_turn("测试", AgentMode.CHAT)
        events = [event.as_dict() for event in self.service.stream_turn(run)]

        self.assertEqual(
            [event["kind"] for event in events],
            ["started", "delta", "done"],
        )
        self.assertEqual([event["sequence"] for event in events], [0, 1, 2])
        self.assertTrue(
            all(event["run_id"] == "run-1" for event in events)
        )
        self.assertTrue(
            all(event["generation_id"] == "run-1" for event in events)
        )
        self.assertTrue(events[-1]["result"]["persisted"])
        self.assertEqual(events[-1]["result"]["message_id"], 1)

    def test_second_run_in_same_session_is_rejected(self):
        self.persist_default_done("第一条")
        run = self.service.begin_turn("第一条消息")

        with self.assertRaises(RunAlreadyActiveError):
            self.service.begin_turn("第二条消息")

        self.service.abandon_run(run.run_id)

    def test_mutations_are_rejected_while_a_run_is_active(self):
        self.persist_default_done("忙碌")
        run = self.service.begin_turn("开始")

        with self.assertRaises(SessionBusyError):
            self.service.set_mode(AgentMode.WORK)
        with self.assertRaises(SessionBusyError):
            self.service.select_workspace(str(self.root))
        with self.assertRaises(SessionBusyError):
            self.service.clear_workspace()
        with self.assertRaises(SessionBusyError):
            self.service.switch_session("other")
        with self.assertRaises(SessionBusyError):
            self.service.create_session()
        with self.assertRaises(SessionBusyError):
            self.service.rename_session("main", "新标题")
        with self.assertRaises(SessionBusyError):
            self.service.set_session_pinned("main", True)
        with self.assertRaises(SessionBusyError):
            self.service.set_session_unread("main", True)
        with self.assertRaises(SessionBusyError):
            self.service.delete_session("main")
        self.assertTrue(self.service.save_draft("main", "运行中的草稿"))
        self.assertEqual(self.service.draft("main"), "")

        self.service.abandon_run(run.run_id)

    def test_cancel_is_propagated_and_done_remains_authoritative(self):
        def script(runtime, cancel_event):
            yield {"kind": "delta", "text": "第一段"}
            answer = "第一段\n（已停止生成）"
            runtime.store.add_assistant(answer)
            yield {
                "kind": "done",
                "result": {
                    "answer": answer,
                    "proposal": None,
                    "receipt": None,
                    "task": None,
                    "task_receipt": None,
                    "cancelled": True,
                },
            }

        self.runtime.stream_factory = script
        run = self.service.begin_turn("停止测试")
        self.assertTrue(self.service.request_cancel(run.run_id))
        events = list(self.service.stream_turn(run))

        self.assertIs(self.runtime.cancel_event, run.cancel_event)
        self.assertTrue(run.cancel_event.is_set())
        self.assertEqual(events[-1].kind, "done")
        self.assertEqual(events[-1].stage, "cancelled")
        self.assertEqual(run.stage, "cancelled")
        self.assertTrue(events[-1].payload["result"]["cancelled"])
        self.assertIn("已停止生成", events[-1].payload["result"]["answer"])

    def test_stream_without_done_fails_instead_of_reporting_completion(self):
        def script(runtime, cancel_event):
            yield {"kind": "delta", "text": "未完成"}

        self.runtime.stream_factory = script
        run = self.service.begin_turn("没有完成事件")

        with self.assertRaises(TurnPersistenceError):
            list(self.service.stream_turn(run))

        self.assertEqual(run.status.value, "failed")
        self.assertEqual(run.stage, "failed")
        self.assertFalse(self.service.has_active_run(run.session_id))

    def test_done_without_persisted_assistant_is_rejected(self):
        def script(runtime, cancel_event):
            yield {
                "kind": "done",
                "result": {
                    "answer": "尚未落库",
                    "proposal": None,
                    "receipt": None,
                    "task": None,
                    "task_receipt": None,
                },
            }

        self.runtime.stream_factory = script
        run = self.service.begin_turn("持久化检查")

        with self.assertRaises(TurnPersistenceError):
            list(self.service.stream_turn(run))

        self.assertEqual(run.status.value, "failed")

    def test_workspace_snapshot_is_frozen_when_run_begins(self):
        first = self.root / "first"
        second = self.root / "second"
        first.mkdir()
        second.mkdir()
        self.runtime.workspace_path = str(first)

        run = self.service.begin_turn("冻结工作区")
        self.runtime.workspace_path = str(second)

        self.assertEqual(run.workspace.root, str(first.resolve()))
        self.assertTrue(run.workspace.device)
        self.assertTrue(run.workspace.file_id)
        self.service.abandon_run(run.run_id)

    def test_events_carry_the_stage_and_sequence_of_the_run(self):
        def script(runtime, cancel_event):
            yield {"kind": "delta", "text": "先想"}
            yield {
                "kind": "tool",
                "tool_name": "read_note",
                "state": "running",
            }
            yield {
                "kind": "tool",
                "tool_name": "read_note",
                "state": "done",
                "summary": "1 个文件",
            }
            yield {"kind": "reset"}
            yield {"kind": "delta", "text": "答"}
            runtime.store.add_assistant("先想答")
            yield {
                "kind": "done",
                "result": {
                    "answer": "先想答",
                    "proposal": None,
                    "receipt": None,
                    "task": None,
                    "task_receipt": None,
                },
            }

        self.runtime.stream_factory = script
        run = self.service.begin_turn("阶段检查")
        events = [event.as_dict() for event in self.service.stream_turn(run)]

        self.assertEqual(
            [event["stage"] for event in events],
            [
                "thinking",
                "answering",
                "tool",
                "tool",
                "thinking",
                "answering",
                "completed",
            ],
        )
        self.assertEqual(
            [event["sequence"] for event in events],
            list(range(len(events))),
        )
        self.assertEqual(run.stage, "completed")
        self.assertEqual(run.sequence, len(events) - 1)
        self.assertEqual(run.tool, "")

    def test_run_steps_collapse_repeated_stage_and_detail(self):
        captured = []

        def script(runtime, cancel_event):
            yield {"kind": "delta", "text": "一"}
            yield {"kind": "delta", "text": "二"}
            yield {"kind": "delta", "text": "三"}
            yield {"kind": "tool", "tool_name": "read_note", "state": "running"}
            yield {
                "kind": "tool",
                "tool_name": "read_note",
                "state": "done",
                "summary": "1 个文件",
            }
            view = self.service.run_view()
            if view is not None:
                captured.append(list(view.steps))
            runtime.store.add_assistant("一二三")
            yield {
                "kind": "done",
                "result": {
                    "answer": "一二三",
                    "proposal": None,
                    "receipt": None,
                    "task": None,
                    "task_receipt": None,
                },
            }

        self.runtime.stream_factory = script
        run = self.service.begin_turn("时间线")
        events = [event.as_dict() for event in self.service.stream_turn(run)]

        self.assertEqual(
            [(step.stage, step.detail) for step in captured[0]],
            [
                ("thinking", ""),
                ("answering", ""),
                ("tool", "read_note"),
                ("tool", "read_note：1 个文件"),
            ],
        )
        self.assertEqual(len(events), 7)
        self.assertEqual(len(run.steps), 5)
        self.assertEqual((run.steps[-1].stage, run.steps[-1].detail), ("completed", ""))
        sequences = [step.sequence for step in run.steps]
        self.assertEqual(sequences, sorted(set(sequences)))
        self.assertTrue(all(step.at for step in run.steps))
        self.assertIsNone(self.service.run_view())

    def test_run_steps_record_the_failure_detail(self):
        def script(runtime, cancel_event):
            yield {"kind": "delta", "text": "半句"}
            raise RuntimeError("模型断开")

        self.runtime.stream_factory = script
        run = self.service.begin_turn("失败时间线")
        with self.assertRaises(RuntimeError):
            list(self.service.stream_turn(run))

        steps = run.steps
        self.assertEqual(steps[-1].stage, "failed")
        self.assertEqual(steps[-1].detail, "模型断开")
        self.assertEqual([step.stage for step in steps], ["thinking", "answering", "failed"])
        self.assertIsNone(self.service.run_view())

    def test_ui_projection_exposes_transcript_and_the_active_run(self):
        self.runtime.store.add_assistant("旧回复")
        projection = self.service.ui_projection()

        self.assertEqual(projection.session_id, "main")
        self.assertEqual([item.text for item in projection.messages], ["旧回复"])
        self.assertIsNone(projection.run)
        sections = {item.title: item.lines for item in projection.inspector}
        self.assertEqual(sections["待确认提案"], ("没有等待确认的写入",))

        seen = []

        def script(runtime, cancel_event):
            seen.append(self.service.ui_projection())
            runtime.store.add_assistant("新回复")
            yield {
                "kind": "done",
                "result": {
                    "answer": "新回复",
                    "proposal": None,
                    "receipt": None,
                    "task": None,
                    "task_receipt": None,
                },
            }

        self.runtime.stream_factory = script
        run = self.service.begin_turn("进行中")
        list(self.service.stream_turn(run))

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].run.run_id, run.run_id)
        self.assertEqual(seen[0].run.stage, "thinking")
        self.assertEqual(seen[0].run.sequence, 0)

    def test_ui_projection_has_no_context_view_before_a_build(self):
        self.assertIsNone(self.service.ui_projection().context)
        titles = {item.title for item in self.service.ui_projection().inspector}
        self.assertNotIn("上下文", titles)

    def test_ui_projection_context_carries_numbers_for_every_adapter(self):
        self.runtime.last_context = context_build(1500, 4000, True)
        projection = self.service.ui_projection()

        context = projection.context
        self.assertEqual((context.context_used, context.context_limit), (1500, 4000))
        self.assertTrue(context.compressed)
        self.assertEqual(context.percent, 38)
        sections = {item.title: item.lines for item in projection.inspector}
        self.assertEqual(
            sections["上下文"],
            ("用量：1500 / 4000 字符", "压缩：已压缩"),
        )

    def test_ui_projection_context_is_skipped_when_the_budget_is_unusable(self):
        for budget in (0, -1):
            self.runtime.last_context = context_build(10, budget)
            projection = self.service.ui_projection()

            self.assertIsNone(projection.context, budget)
            self.assertNotIn("上下文", {item.title for item in projection.inspector})

    def test_ui_projection_context_percent_never_exceeds_the_ring(self):
        self.runtime.last_context = context_build(9000, 4000)

        self.assertEqual(self.service.ui_projection().context.percent, 100)


class ApprovalApplicationServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.runtime = CompanionRuntime(
            store=CompanionStore(self.root / "data" / "companion.db"),
            llm_client=OfflineLLMClient(),
            search_service=SearchService(),
            data_dir=self.root / "data",
            session_id="main",
            offline=True,
        )
        self.runtime.select_workspace(str(self.workspace))
        self.service = create_application_service(self.runtime)
        self.proposal = self.runtime.notes.create_proposal(
            session_id="main",
            query="审批视图",
            content="application approval",
            mode=AgentMode.WORK,
        )
        self.runtime.store.create_proposal(self.proposal)

    def tearDown(self):
        self.temp.cleanup()

    def test_approval_view_contains_digest_and_expiry(self):
        view = self.service.approval_view(self.proposal.action_id)

        self.assertEqual(view.action_id, self.proposal.action_id)
        self.assertEqual(len(view.confirmation_digest), 64)
        self.assertTrue(view.expires_at)
        self.assertEqual(view.status, "awaiting_confirmation")

    def test_ui_projection_carries_the_pending_approval(self):
        projection = self.service.ui_projection()

        self.assertEqual(len(projection.approvals), 1)
        self.assertEqual(
            projection.approvals[0].action_id,
            self.proposal.action_id,
        )
        sections = {item.title: item.lines for item in projection.inspector}
        self.assertEqual(sections["待确认提案"][0], "1 条")

    def test_processed_approval_cannot_be_confirmed_again(self):
        self.service.cancel_action(self.proposal.action_id)

        with self.assertRaises(ApprovalUnavailableError):
            self.service.confirm_action(
                self.proposal.action_id,
                AgentMode.WORK,
            )

    def test_approval_view_previews_the_pending_change(self):
        view = self.service.approval_view(self.proposal.action_id)

        self.assertEqual(view.tool, "write_note")
        self.assertFalse(view.expired)
        self.assertTrue(view.workspace.endswith("workspace"))
        self.assertIsNotNone(view.preview)
        self.assertEqual(view.preview.change, "create")
        self.assertFalse(view.preview.error)
        self.assertIn(("add", "application approval"), view.preview.diff)
        self.assertEqual(view.preview.added, 1)
        self.assertFalse(Path(self.proposal.target_summary).exists())

    def test_projection_serializes_approval_preview(self):
        payload = self.service.ui_projection().approvals[0].as_dict()

        self.assertEqual(payload["tool"], "write_note")
        self.assertEqual(payload["preview"]["change"], "create")
        self.assertTrue(payload["preview"]["workspace"].endswith("workspace"))

    def test_expired_approval_cannot_be_confirmed(self):
        expired = self.runtime.notes.create_proposal(
            session_id="main",
            query="过期提案",
            content="stale",
            mode=AgentMode.WORK,
            ttl_hours=-1,
        )
        self.runtime.store.create_proposal(expired)

        view = self.service.approval_view(expired.action_id)

        self.assertTrue(view.expired)
        with self.assertRaises(ApprovalUnavailableError):
            self.service.confirm_action(expired.action_id, AgentMode.WORK)
        self.assertEqual(
            self.runtime.store.get_proposal(expired.action_id).status.value,
            "awaiting_confirmation",
        )
        self.assertFalse(Path(expired.target_summary).exists())

    def test_cross_session_approval_is_unavailable(self):
        self.runtime.store.ensure_session("other", AgentMode.WORK)
        self.assertTrue(self.runtime.switch_session("other"))

        with self.assertRaises(ApprovalUnavailableError):
            self.service.approval_view(self.proposal.action_id)
        with self.assertRaises(ApprovalUnavailableError):
            self.service.confirm_action(self.proposal.action_id, AgentMode.WORK)


if __name__ == "__main__":
    unittest.main()
