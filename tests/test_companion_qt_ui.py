"""Qt desktop smoke tests, skipped when PySide6 is unavailable."""

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_framework.companion.models import AgentMode, SearchResult
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.llm import OfflineLLMClient


try:
    from PySide6.QtWidgets import QApplication
    from agent_framework.companion.ui.qt_app import CompanionWindow
except ImportError:
    QApplication = None
    CompanionWindow = None


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
