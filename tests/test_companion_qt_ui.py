"""Qt desktop smoke tests, skipped when PySide6 is unavailable."""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
