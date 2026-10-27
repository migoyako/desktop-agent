"""M6 regression tests: streaming, cancellation, markers, UI state and status."""

import tempfile
import threading
import unittest
from pathlib import Path

from agent_framework.companion.models import (
    APPROVAL_APPROVED,
    AgentMode,
    ActionResult,
    FORWARD_FORWARDING,
    FORWARD_UNKNOWN,
    SearchResult,
    WORK_EXECUTING,
    WORK_PLAN_REVIEW,
    utc_now,
)
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.companion.streaming import (
    MarkerFilter,
    strip_control_markers,
)
from agent_framework.companion.ui.state import UiStateStore, clamp_position
from agent_framework.llm import (
    LLMClient,
    LLMResponse,
    OfflineLLMClient,
    StreamEvent,
    ToolCall,
)


class StubSearch:
    def search(self, query, limit=5):
        return [
            SearchResult(
                title="Example result",
                url="https://example.com/result",
                snippet="A short search summary.",
            )
        ]


class ScriptedStreamLLM(LLMClient):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, messages, tools=None):
        return LLMResponse(content="普通回答。")

    def stream_chat(self, messages, tools=None):
        self.calls += 1
        if not self.responses:
            raise AssertionError("unexpected stream call")
        response = self.responses.pop(0)
        for chunk in response.get("chunks", []):
            yield StreamEvent(kind="delta", text=chunk)
        yield StreamEvent(kind="done", response=response["response"])


class ChatOnlyLLM(LLMClient):
    def chat(self, messages, tools=None):
        return LLMResponse(content="普通回答 [[mood:开心]]。")


class CancelAfterFirstChunkLLM(LLMClient):
    def __init__(self, cancel_event):
        self.cancel_event = cancel_event

    def chat(self, messages, tools=None):
        return LLMResponse(content="不该走到这里。")

    def stream_chat(self, messages, tools=None):
        yield StreamEvent(kind="delta", text="第一段")
        self.cancel_event.set()
        yield StreamEvent(kind="delta", text="不该出现")
        yield StreamEvent(
            kind="done",
            response=LLMResponse(content="第一段不该出现"),
        )


class ErrorStreamLLM(LLMClient):
    def chat(self, messages, tools=None):
        return LLMResponse(content="普通回答。")

    def stream_chat(self, messages, tools=None):
        yield StreamEvent(kind="error", error="boom")
        yield StreamEvent(kind="done", response=LLMResponse(content=""))


class ToolThenAnswerLLM(LLMClient):
    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None):
        return LLMResponse(content="普通回答。")

    def stream_chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            yield StreamEvent(
                kind="done",
                response=LLMResponse(
                    tool_calls=[ToolCall(name="current_time", arguments={})]
                ),
            )
            return
        yield StreamEvent(kind="delta", text="现在是下午。")
        yield StreamEvent(
            kind="done",
            response=LLMResponse(content="现在是下午。"),
        )


class MarkerFilterTest(unittest.TestCase):
    def test_marker_split_across_chunks_is_removed(self):
        marker_filter = MarkerFilter()
        first = marker_filter.feed("你好[[mo")
        second = marker_filter.feed("od:开心]]呀")
        tail = marker_filter.finish()

        self.assertEqual(first, "你好")
        self.assertEqual(second, "呀")
        self.assertEqual(tail, "")
        self.assertEqual(
            marker_filter.markers,
            [{"kind": "mood", "value": "开心"}],
        )

    def test_all_marker_kinds_are_parsed(self):
        marker_filter = MarkerFilter()
        visible = marker_filter.feed(
            "[[mood:平静]][[action:挥手]][[sticker:咖啡]]收到"
        )

        self.assertEqual(visible, "收到")
        self.assertEqual(
            [item["kind"] for item in marker_filter.markers],
            ["mood", "action", "sticker"],
        )

    def test_unknown_marker_stays_visible(self):
        marker_filter = MarkerFilter()
        visible = marker_filter.feed("a[[camera:left]]b")

        self.assertEqual(visible, "a[[camera:left]]b")
        self.assertEqual(marker_filter.markers, [])

    def test_unfinished_marker_is_flushed_on_finish(self):
        marker_filter = MarkerFilter()
        visible = marker_filter.feed("结尾[[mood")

        self.assertEqual(visible, "结尾")
        self.assertEqual(marker_filter.finish(), "[[mood")

    def test_strip_control_markers(self):
        self.assertEqual(
            strip_control_markers("好[[mood:开心]]的[[action:点头]]一天"),
            "好的一天",
        )


class StreamingRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.data = root / "data"
        self.store = CompanionStore(self.data / "companion.db")

    def tearDown(self):
        self.temp.cleanup()

    def runtime(self, llm, offline=False):
        runtime = CompanionRuntime(
            self.store,
            llm,
            SearchService(StubSearch()),
            self.data,
            offline=offline,
        )
        runtime.select_workspace(str(self.workspace))
        return runtime

    def test_events_carry_generation_id_and_answer_is_persisted(self):
        llm = ScriptedStreamLLM(
            [
                {
                    "chunks": ["你", "好[[mo", "od:开心]]", "呀"],
                    "response": LLMResponse(content="你好[[mood:开心]]呀"),
                }
            ]
        )
        runtime = self.runtime(llm)
        events = list(
            runtime.stream_message("你好", AgentMode.CHAT, generation_id="g1")
        )

        self.assertTrue(all(item["generation_id"] == "g1" for item in events))
        delta = "".join(
            item["text"] for item in events if item["kind"] == "delta"
        )
        self.assertEqual(delta, "你好呀")
        self.assertNotIn("[[", delta)
        done = events[-1]
        self.assertEqual(done["kind"], "done")
        self.assertEqual(done["result"]["answer"], "你好呀")

        history = runtime.history()
        self.assertEqual(history[-1]["role"], "assistant")
        self.assertEqual(history[-1]["content"], "你好呀")

    def test_tool_events_are_emitted_between_deltas(self):
        runtime = self.runtime(ToolThenAnswerLLM())
        events = list(runtime.stream_message("现在几点", AgentMode.CHAT))

        kinds = [item["kind"] for item in events]
        self.assertIn("tool", kinds)
        tool_states = [
            item["state"] for item in events if item["kind"] == "tool"
        ]
        self.assertEqual(tool_states, ["running", "done"])
        self.assertEqual(events[-1]["result"]["answer"], "现在是下午。")

    def test_cancel_keeps_partial_and_drops_late_chunks(self):
        cancel_event = threading.Event()
        runtime = self.runtime(CancelAfterFirstChunkLLM(cancel_event))
        events = list(
            runtime.stream_message(
                "讲讲天气",
                AgentMode.CHAT,
                generation_id="g2",
                cancel_event=cancel_event,
            )
        )

        delta = "".join(
            item["text"] for item in events if item["kind"] == "delta"
        )
        self.assertEqual(delta, "第一段")
        self.assertNotIn("不该出现", delta)
        done = events[-1]
        self.assertEqual(done["kind"], "done")
        self.assertTrue(done["result"]["cancelled"])
        self.assertIn("已停止生成", done["result"]["answer"])

        history = runtime.history()
        self.assertIn("第一段", history[-1]["content"])
        self.assertIn("已停止生成", history[-1]["content"])
        self.assertNotIn("不该出现", history[-1]["content"])

    def test_stream_error_emits_error_event(self):
        runtime = self.runtime(ErrorStreamLLM())
        events = list(runtime.stream_message("你好", AgentMode.CHAT))

        errors = [item for item in events if item["kind"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("boom", errors[0]["message"])
        self.assertEqual(events[-1]["kind"], "done")

    def test_client_without_stream_chat_is_chunked_and_stripped(self):
        runtime = self.runtime(ChatOnlyLLM())
        events = list(runtime.stream_message("你好", AgentMode.CHAT))

        delta = "".join(
            item["text"] for item in events if item["kind"] == "delta"
        )
        self.assertEqual(delta, "普通回答 。")
        self.assertEqual(events[-1]["result"]["answer"], "普通回答 。")
        self.assertNotIn("[[", runtime.history()[-1]["content"])

    def test_offline_runtime_streams_deltas(self):
        runtime = self.runtime(OfflineLLMClient(), offline=True)
        events = list(runtime.stream_message("你好", AgentMode.CHAT))

        deltas = [item for item in events if item["kind"] == "delta"]
        self.assertGreater(len(deltas), 1)
        self.assertEqual(events[-1]["kind"], "done")
        self.assertTrue(events[-1]["result"]["answer"])

    def test_search_proposal_branch_streams_answer(self):
        runtime = self.runtime(OfflineLLMClient(), offline=True)
        events = list(
            runtime.stream_message(
                "搜索 Python Agent 并保存为笔记",
                AgentMode.WORK,
            )
        )

        kinds = [item["kind"] for item in events]
        self.assertEqual(kinds, ["delta", "done"])
        self.assertIsNotNone(events[-1]["result"]["proposal"])
        self.assertEqual(list(self.workspace.rglob("*.md")), [])

    def test_empty_input_finishes_with_prompt(self):
        runtime = self.runtime(OfflineLLMClient(), offline=True)
        events = list(runtime.stream_message("   ", AgentMode.CHAT))

        self.assertEqual(events[-1]["kind"], "done")
        self.assertEqual(events[-1]["result"]["answer"], "请输入内容。")


class UiStateStoreTest(unittest.TestCase):
    def test_position_round_trip_and_visibility_default(self):
        with tempfile.TemporaryDirectory() as temp:
            store = UiStateStore(temp)
            self.assertIsNone(store.overlay_position())
            self.assertTrue(store.overlay_visible())

            store.set_overlay_position(120, 80)
            self.assertEqual(store.overlay_position(), (120, 80))

            store.set_overlay_visible(False)
            self.assertFalse(store.overlay_visible())

            restored = UiStateStore(temp)
            self.assertEqual(restored.overlay_position(), (120, 80))
            self.assertFalse(restored.overlay_visible())

    def test_corrupt_state_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            store = UiStateStore(temp)
            store.path.write_text("{not json", encoding="utf-8")

            self.assertEqual(store.load(), {})
            self.assertIsNone(store.overlay_position())

    def test_clamp_inside_screen_is_unchanged(self):
        self.assertEqual(
            clamp_position(100, 100, 200, 100, [(0, 0, 800, 600)]),
            (100, 100),
        )

    def test_clamp_negative_and_overflow(self):
        screens = [(0, 0, 800, 600)]
        self.assertEqual(clamp_position(-50, -20, 200, 100, screens), (0, 0))
        self.assertEqual(clamp_position(700, 550, 200, 100, screens), (600, 500))

    def test_clamp_uses_nearest_screen(self):
        screens = [(0, 0, 800, 600), (1600, 0, 1280, 1024)]
        self.assertEqual(
            clamp_position(900, 700, 200, 150, screens),
            (600, 450),
        )
        self.assertEqual(
            clamp_position(2000, 100, 200, 100, screens),
            (2000, 100),
        )

    def test_clamp_without_screens_is_noop(self):
        self.assertEqual(clamp_position(42, 43, 100, 100, []), (42, 43))


class DesktopStatusTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.data = root / "data"
        self.store = CompanionStore(self.data / "companion.db")
        self.runtime = CompanionRuntime(
            self.store,
            OfflineLLMClient(),
            SearchService(StubSearch()),
            self.data,
            offline=True,
        )
        self.runtime.select_workspace(str(self.workspace))

    def tearDown(self):
        self.temp.cleanup()

    def test_idle_without_work(self):
        status = self.runtime.desktop_status()
        self.assertEqual(status["state"], "idle")
        self.assertEqual(status["source"], "idle")

    def test_pending_proposal_then_receipt_states(self):
        result = self.runtime.handle_message(
            "搜索 Python Agent 并保存为笔记",
            AgentMode.WORK,
        )
        proposal = result["proposal"]
        self.assertIsNotNone(proposal)

        status = self.runtime.desktop_status()
        self.assertEqual(status["state"], "awaiting_confirmation")
        self.assertEqual(status["label"], "等待确认写入")
        self.assertEqual(status["source"], "proposal")
        self.assertEqual(status["proposal_id"], proposal.action_id)

        confirmed = self.runtime.confirm_proposal(proposal.action_id)
        status = self.runtime.desktop_status()
        self.assertEqual(status["state"], "completed")
        self.assertEqual(status["source"], "receipt")
        self.assertEqual(status["receipt_id"], confirmed["receipt"].receipt_id)

        self.runtime.undo_receipt(confirmed["receipt"].receipt_id)
        status = self.runtime.desktop_status()
        self.assertEqual(status["state"], "undone")
        self.assertEqual(status["label"], "已撤销")

    def test_unknown_receipt_is_reported(self):
        result = self.runtime.handle_message(
            "搜索 Python Agent 并保存为笔记",
            AgentMode.WORK,
        )
        proposal = result["proposal"]
        self.runtime.cancel_proposal(proposal.action_id)

        now = utc_now()
        self.store.save_receipt(
            ActionResult(
                receipt_id="rc_unknown",
                action_id=proposal.action_id,
                session_id="main",
                status="unknown",
                summary="写入过程中连接中断",
                result={},
                started_at=now,
                finished_at=now,
            )
        )

        status = self.runtime.desktop_status()
        self.assertEqual(status["state"], "unknown")
        self.assertEqual(status["label"], "结果不确定")
        self.assertEqual(status["receipt_id"], "rc_unknown")

    def test_work_task_states_are_reported(self):
        result = self.runtime.create_work_task(
            "把周报发给张三并说明周五发送",
            force=True,
        )
        task = result["task"]
        self.assertEqual(task.state, WORK_PLAN_REVIEW)

        status = self.runtime.desktop_status()
        self.assertEqual(status["state"], "awaiting_approval")
        self.assertEqual(status["label"], "等待批准计划")
        self.assertEqual(status["source"], "task")
        self.assertEqual(status["task_id"], task.task_id)

    def test_executing_work_receipts_are_reported(self):
        result = self.runtime.create_work_task(
            "把周报发给张三并说明周五发送",
            force=True,
        )
        task = result["task"]
        receipt = self.runtime.work_receipt(task.task_id)
        self.assertIsNotNone(receipt)
        self.store.transition_work_state(
            task.task_id,
            WORK_PLAN_REVIEW,
            WORK_EXECUTING,
            approval_status=APPROVAL_APPROVED,
        )
        self.store.update_work_receipt(
            receipt.receipt_id,
            FORWARD_FORWARDING,
            summary="正在发送周报",
        )

        status = self.runtime.desktop_status()
        self.assertEqual(status["state"], "forwarding")
        self.assertEqual(status["source"], "work_receipt")

        self.store.update_work_receipt(
            receipt.receipt_id,
            FORWARD_UNKNOWN,
            summary="发送后没有收到回执",
        )
        status = self.runtime.desktop_status()
        self.assertEqual(status["state"], "unknown")
        self.assertEqual(status["label"], "转发结果不确定")
        self.assertEqual(status["source"], "work_receipt")


if __name__ == "__main__":
    unittest.main()
