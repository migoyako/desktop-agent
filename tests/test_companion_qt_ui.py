"""Qt desktop smoke tests, skipped when PySide6 is unavailable."""

import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_framework.companion.models import AgentMode, SearchResult
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.companion.ui.state import UiStateStore
from agent_framework.llm import LLMResponse, OfflineLLMClient, StreamEvent


try:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtWidgets import QApplication
    from agent_framework.companion.ui.qt_app import AvatarOverlay, CompanionWindow
    from agent_framework.companion.ui.tray import TrayController
except ImportError:
    QApplication = None
    CompanionWindow = None
    AvatarOverlay = None
    TrayController = None
    QPoint = None
    Qt = None


class GatedStreamLLM:
    """Streams one chunk, then blocks until the test lets the rest through."""

    def __init__(self):
        self.release = threading.Event()
        self.started = threading.Event()

    def chat(self, messages, tools=None):
        return LLMResponse(content="普通回答。")

    def stream_chat(self, messages, tools=None):
        yield StreamEvent(kind="delta", text="第一段")
        self.started.set()
        self.release.wait(5)
        yield StreamEvent(kind="delta", text="迟到内容")
        yield StreamEvent(
            kind="done",
            response=LLMResponse(content="第一段迟到内容"),
        )


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


REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_PERSONA = REPO_ROOT / "data" / "personas" / "remielle"


class CompanionQtEndToEndTest(unittest.TestCase):
    def setUp(self):
        if CompanionWindow is None:
            return
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
        self.app = QApplication.instance() or QApplication([])
        self.window = CompanionWindow(self.runtime)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        if CompanionWindow is None:
            return
        self.window.close()
        self.app.processEvents()
        self.temp.cleanup()

    def wait_until(self, predicate, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def test_search_confirm_undo_through_window(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")

        self.window.work_button.click()
        self.assertEqual(self.runtime.current_mode(), AgentMode.WORK)
        self.window.input.setPlainText("搜索 Python Agent 并保存为笔记")
        self.window.send_button.click()

        self.assertTrue(
            self.wait_until(lambda: self.window.current_proposal is not None),
            "提案没有出现在窗口中",
        )
        proposal = self.window.current_proposal
        self.assertTrue(self.window.confirm_frame.isVisible())
        self.assertEqual(list(self.workspace.rglob("*.md")), [])

        self.window.confirm_button.click()
        self.assertTrue(
            self.wait_until(
                lambda: self.window.current_proposal is None
                and self.window.undo_button.isVisible()
            ),
            "确认后没有显示撤销按钮",
        )
        files = list(self.workspace.rglob("*.md"))
        self.assertEqual(len(files), 1)
        self.assertIn("Example result", files[0].read_text(encoding="utf-8"))

        receipt = self.store.get_receipt_for_action(proposal.action_id)
        self.assertEqual(receipt.status, "completed")
        self.assertEqual(receipt.undo_status, "available")
        self.assertEqual(
            self.store.get_execution(proposal.action_id)["status"],
            "completed",
        )

        self.window.undo_button.click()
        self.assertTrue(
            self.wait_until(lambda: "已撤销" in self.window.status.text()),
            "撤销没有完成",
        )
        self.assertEqual(list(self.workspace.rglob("*.md")), [])
        self.assertEqual(
            self.store.get_receipt(receipt.receipt_id).undo_status,
            "undone",
        )


class CompanionQtUiSmokeTest(unittest.TestCase):
    def setUp(self):
        if CompanionWindow is None:
            return
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        persona_target = root / "personas" / "remielle"
        persona_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(BUNDLED_PERSONA, persona_target)
        runtime = CompanionRuntime(
            CompanionStore(root / "db.sqlite"),
            OfflineLLMClient(),
            SearchService(),
            root,
            offline=True,
        )
        self.runtime = runtime
        self.store = runtime.store
        self.app = QApplication.instance() or QApplication([])
        self.window = CompanionWindow(runtime)

    def tearDown(self):
        if CompanionWindow is None:
            return
        self.window.close()
        self.app.processEvents()
        self.temp.cleanup()

    def wait_until(self, predicate, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def assert_dialog_closes_independently(self, attribute):
        self.window.resize(380, 500)
        self.window.move(0, 0)
        self.window.show()
        self.app.processEvents()
        self.assertFalse(self.app.quitOnLastWindowClosed())

        if attribute == "memory_dialog":
            self.window.open_memory()
        elif attribute == "affect_dialog":
            self.window.open_affect()
        else:
            self.window.open_persona()
        dialog = getattr(self.window, attribute)
        self.app.processEvents()

        self.assertIsNotNone(dialog)
        self.assertTrue(dialog.isVisible())
        self.assertIs(dialog.parent(), self.window)
        self.assertFalse(
            dialog.frameGeometry().intersects(self.window.frameGeometry())
        )

        dialog.close_button.click()
        self.app.processEvents()
        self.assertFalse(dialog.isVisible())
        self.assertIsNone(getattr(self.window, attribute))
        self.assertTrue(self.window.isVisible())

    def test_qt_window_can_be_created(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")

        self.window.show()
        self.app.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertEqual((self.window.width(), self.window.height()), (500, 760))

    def test_affect_dialog_closes_without_closing_main_window(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.assert_dialog_closes_independently("affect_dialog")

    def test_persona_dialog_closes_without_closing_main_window(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.assert_dialog_closes_independently("persona_dialog")

    def test_persona_dialog_saves_address_and_updates_prompt(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.window.open_persona()
        dialog = self.window.persona_dialog
        self.app.processEvents()

        info = dialog.info.toPlainText()
        self.assertIn("蕾米埃尔·丹", info)
        self.assertIn("世界书", info)

        dialog.address.setText("搭档")
        dialog.save()
        self.app.processEvents()

        self.assertEqual(
            self.runtime.persona_preferences()["address"],
            "搭档",
        )
        prompt = self.runtime._system_prompt("你好", AgentMode.CHAT)
        self.assertIn("称呼：搭档", prompt)
        self.assertEqual(dialog.address.text(), "搭档")

    def test_memory_dialog_closes_without_closing_main_window(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.assert_dialog_closes_independently("memory_dialog")

    def test_work_dialog_shows_plan_and_closes_independently(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.runtime.create_work_task("把周报发给张三并说明周五发送", force=True)
        self.window.open_work()
        dialog = self.window.work_dialog
        self.app.processEvents()

        self.assertIsNotNone(dialog)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertEqual(dialog.table.item(0, 2).text(), "等待审批")
        self.assertEqual(dialog.table.item(0, 3).text(), "等待确认")
        self.assertIn("周五发送", dialog.detail.toPlainText())
        self.assertTrue(dialog.approve_button.isEnabled())
        self.assertTrue(dialog.revise_button.isEnabled())
        self.assertTrue(dialog.reject_button.isEnabled())

        dialog.close_button.click()
        self.app.processEvents()
        self.assertFalse(dialog.isVisible())
        self.assertIsNone(self.window.work_dialog)

    def test_proactive_dialog_saves_settings_and_shows_events(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.window.open_proactive()
        dialog = self.window.proactive_dialog
        self.app.processEvents()

        self.assertIsNotNone(dialog)
        self.assertTrue(dialog.isVisible())
        self.assertIn("今日主动", dialog.summary.text())

        dialog.quiet_start.setValue(0)
        dialog.quiet_end.setValue(0)
        dialog.daily_limit.setValue(5)
        dialog.save()
        self.app.processEvents()

        settings = self.runtime.proactive_status()["settings"]
        self.assertEqual(settings["daily_limit"], 5)
        self.assertEqual(settings["quiet_start"], 0)
        self.assertEqual(settings["quiet_end"], 0)

        self.store.add_proactive_event(
            session_id="main",
            source_type="reminder",
            source_id="rem_ui",
            trigger_reason="提醒到点",
            content="提醒：喝水",
            status="missed",
            reason="missed_window",
        )
        dialog.refresh()
        self.app.processEvents()
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertIn("未读", dialog.table.item(0, 2).text())
        self.assertIn("提醒到点", dialog.table.item(0, 3).text())

        dialog.mark_all_read()
        self.app.processEvents()
        self.assertEqual(self.runtime.proactive_status()["unread"], 0)

        dialog.close()
        self.app.processEvents()
        self.assertIsNone(self.window.proactive_dialog)

    def test_overlay_is_click_through_with_status_bubble(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        overlay = self.window.overlay
        self.assertIsNotNone(overlay)
        self.assertTrue(overlay.isVisible())
        self.assertTrue(overlay.testAttribute(Qt.WA_TranslucentBackground))
        self.assertTrue(overlay.testAttribute(Qt.WA_TransparentForMouseEvents))
        flags = overlay.windowFlags()
        self.assertTrue(flags & Qt.FramelessWindowHint)
        self.assertTrue(flags & Qt.WindowStaysOnTopHint)
        self.assertTrue(flags & Qt.Tool)

        workspace = Path(self.temp.name) / "workspace"
        workspace.mkdir(exist_ok=True)
        self.runtime.select_workspace(str(workspace))
        self.runtime.search = SearchService(StubSearch())
        result = self.runtime.handle_message(
            "搜索 Python Agent 并保存为笔记",
            AgentMode.WORK,
        )
        proposal = result["proposal"]
        self.assertIsNotNone(proposal)
        overlay.refresh_status()
        self.app.processEvents()

        self.assertEqual(overlay.avatar.status, "等待确认写入")
        self.assertTrue(overlay.bubble.isVisible())
        self.assertTrue(overlay.bubble.text())
        self.assertEqual(
            self.runtime.desktop_status()["proposal_id"],
            proposal.action_id,
        )

        self.runtime.cancel_proposal(proposal.action_id)
        overlay.refresh_status()
        self.app.processEvents()
        self.assertFalse(overlay.bubble.isVisible())
        self.assertEqual(overlay.avatar.status, "待机")

    def test_overlay_hit_test_toggles_click_through(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        overlay = self.window.overlay
        center = overlay.avatar.mapToGlobal(overlay.avatar.rect().center())
        outside = center + QPoint(2000, 2000)

        with patch(
            "agent_framework.companion.ui.qt_app.QCursor.pos",
            return_value=center,
        ):
            overlay.update_hit_test()
        self.assertFalse(
            overlay.testAttribute(Qt.WA_TransparentForMouseEvents)
        )

        with patch(
            "agent_framework.companion.ui.qt_app.QCursor.pos",
            return_value=outside,
        ):
            overlay.update_hit_test()
        self.assertTrue(
            overlay.testAttribute(Qt.WA_TransparentForMouseEvents)
        )

    def test_overlay_position_is_remembered_and_clamped(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        overlay = self.window.overlay
        overlay.move(120, 80)
        overlay.save_position()
        self.assertEqual(self.window.ui_state.overlay_position(), (120, 80))

        restored = AvatarOverlay(
            self.runtime,
            UiStateStore(self.runtime.data_dir),
        )
        try:
            self.assertEqual((restored.x(), restored.y()), (120, 80))
        finally:
            restored.close()

        self.window.ui_state.set_overlay_position(10000, 10000)
        clamped = AvatarOverlay(
            self.runtime,
            UiStateStore(self.runtime.data_dir),
        )
        try:
            screen = QApplication.primaryScreen().availableGeometry()
            self.assertGreaterEqual(clamped.x(), screen.x())
            self.assertGreaterEqual(clamped.y(), screen.y())
            self.assertLessEqual(
                clamped.x() + clamped.width(),
                screen.x() + screen.width(),
            )
            self.assertLessEqual(
                clamped.y() + clamped.height(),
                screen.y() + screen.height(),
            )
        finally:
            clamped.close()

    def test_tray_controller_guards_unavailable_tray(self):
        if CompanionWindow is None or TrayController is None:
            self.skipTest("PySide6 unavailable")
        with patch(
            "agent_framework.companion.ui.tray."
            "QSystemTrayIcon.isSystemTrayAvailable",
            return_value=False,
        ):
            controller = TrayController(self.window, self.runtime)

        self.assertIsNone(controller.tray)
        controller.refresh()
        controller.close()

    def test_tray_menu_tracks_mode_and_proactive(self):
        if CompanionWindow is None or TrayController is None:
            self.skipTest("PySide6 unavailable")
        with patch(
            "agent_framework.companion.ui.tray."
            "QSystemTrayIcon.isSystemTrayAvailable",
            return_value=True,
        ):
            controller = TrayController(self.window, self.runtime)
        try:
            self.assertIsNotNone(controller.tray)
            self.assertEqual(controller.window_action.text(), "显示主窗口")
            self.assertTrue(controller.mode_actions[AgentMode.CHAT].isChecked())

            self.window.set_mode(AgentMode.WORK)
            controller.refresh()
            self.assertTrue(controller.mode_actions[AgentMode.WORK].isChecked())
            self.assertFalse(
                controller.mode_actions[AgentMode.CHAT].isChecked()
            )

            controller.pause_action.trigger()
            self.assertFalse(self.runtime.proactive_status()["enabled"])
            self.assertTrue(controller.pause_action.isChecked())

            controller.pause_action.trigger()
            self.assertTrue(self.runtime.proactive_status()["enabled"])
        finally:
            controller.close()

    def test_stop_stream_discards_late_chunks(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        llm = GatedStreamLLM()
        self.runtime.llm_client = llm
        self.runtime.offline = False
        self.runtime.tool_calling_supported = True

        try:
            self.window.input.setPlainText("讲讲今天的天气")
            self.window.send_button.click()

            self.assertTrue(
                self.wait_until(
                    lambda: llm.started.is_set()
                    and self.window.stream_text == "第一段"
                ),
                "首段没有显示出来",
            )
            self.window.stop_button.click()
            self.assertIsNone(self.window.active_generation)

            llm.release.set()
            self.assertTrue(
                self.wait_until(
                    lambda: self.window.stream_worker is None
                    and self.window.send_button.isEnabled()
                ),
                "停止后没有恢复输入",
            )
        finally:
            llm.release.set()
            self.wait_until(
                lambda: self.window.stream_worker is None,
                timeout=5,
            )

        self.assertEqual(self.window.stream_text, "")
        html = self.window.chat.toHtml()
        self.assertNotIn("迟到内容", html)
        self.assertIn("已停止生成", html)

        last = self.store.list_messages("main", limit=1)[0]
        self.assertEqual(last["role"], "assistant")
        self.assertIn("已停止生成", last["content"])
        self.assertNotIn("迟到内容", last["content"])

    def test_stale_generation_events_are_ignored(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.window.active_generation = "2"
        self.window.stream_text = "已显示"
        self.window.on_stream_event(
            {"kind": "delta", "text": "迟到", "generation_id": "1"}
        )
        self.assertEqual(self.window.stream_text, "已显示")

    def test_memory_dialog_shows_evidence_and_protected_state(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        text = "请记住我喜欢咖啡。"
        message_id = self.store.add_message(
            "main",
            "user",
            text,
            AgentMode.CHAT,
        )
        self.runtime.memory.observe_user_message("main", message_id, text)
        self.runtime.memory.recall("main", "咖啡")

        self.window.open_memory()
        dialog = self.window.memory_dialog
        self.app.processEvents()

        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertEqual(dialog.table.item(0, 4).text(), "")
        dialog.table.selectRow(0)
        self.app.processEvents()
        detail = dialog.detail.toPlainText()
        self.assertIn("来源证据", detail)
        self.assertIn(text, detail)
        self.assertIn("最近召回", detail)

        memory_id = dialog.table.item(0, 0).text()
        self.runtime.set_memory_protected(memory_id, True)
        dialog.refresh()
        self.assertEqual(dialog.table.item(0, 4).text(), "是")


if __name__ == "__main__":
    unittest.main()
