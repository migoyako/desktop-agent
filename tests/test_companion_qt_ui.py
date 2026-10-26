"""Qt desktop smoke tests, skipped when PySide6 is unavailable."""

import os
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
        runtime = CompanionRuntime(
            CompanionStore(root / "db.sqlite"),
            OfflineLLMClient(),
            SearchService(),
            root,
            offline=True,
        )
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
        else:
            self.window.open_affect()
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

    def test_memory_dialog_closes_without_closing_main_window(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.assert_dialog_closes_independently("memory_dialog")


if __name__ == "__main__":
    unittest.main()
