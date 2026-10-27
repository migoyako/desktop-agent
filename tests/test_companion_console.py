"""M8 Qt management console smoke tests, skipped when PySide6 is unavailable."""

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
    from agent_framework.companion.ui.console import ManagementConsoleDialog
    from agent_framework.companion.ui.console.console import (
        ExecutionPage,
        FailurePage,
        MemoryPage,
        RecallPage,
        SettingsPage,
    )
except ImportError:
    QApplication = None
    ManagementConsoleDialog = None
    ExecutionPage = None
    FailurePage = None
    MemoryPage = None
    RecallPage = None
    SettingsPage = None


class StubSearch:
    def search(self, query, limit=5):
        return [
            SearchResult(
                title="Example result",
                url="https://example.com/result",
                snippet="A short search summary.",
            )
        ]


class ManagementConsoleTest(unittest.TestCase):
    def setUp(self):
        if ManagementConsoleDialog is None:
            return
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.data = root / "data"
        self.store = CompanionStore(self.data / "companion.db")
        self.runtime = CompanionRuntime(
            self.store,
            OfflineLLMClient(),
            SearchService(StubSearch()),
            self.data,
            offline=True,
        )
        self.app = QApplication.instance() or QApplication([])
        self.runtime.handle_message("我喜欢喝茶", AgentMode.CHAT)
        memories = self.store.list_memories(self.runtime.session_id)
        self.assertTrue(memories)
        self.memory = memories[0]
        self.store.log_recall(
            self.runtime.session_id,
            "hash-console",
            [self.memory.memory_id],
            [0.9],
            4.0,
            source="test",
        )
        self.store.log_skill_call(
            self.runtime.session_id,
            "demo-skill",
            "read",
            "chat",
            "denied",
            "token=sk-abcdef1234567890",
        )
        self.dialog = ManagementConsoleDialog(self.runtime)
        self.dialog.show()
        self._wait()

    def tearDown(self):
        if ManagementConsoleDialog is None:
            return
        self.dialog.close()
        self.app.processEvents()
        self.temp.cleanup()

    def _wait(self, predicate=None, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.app.processEvents()
            if predicate is None or predicate():
                return
            time.sleep(0.01)

    def test_console_lists_all_pages_with_records(self):
        if ManagementConsoleDialog is None:
            self.skipTest("PySide6 unavailable")

        self.assertEqual(self.dialog.tabs.count(), 7)

        memory_page = self.dialog.page(MemoryPage)
        self.assertGreaterEqual(memory_page.table.rowCount(), 1)
        memory_page.table.setCurrentCell(0, 0)
        self._wait()
        self.assertIn("茶", memory_page.detail.toPlainText())

        recall_page = self.dialog.page(RecallPage)
        self.assertEqual(recall_page.table.rowCount(), 1)

        failure_page = self.dialog.page(FailurePage)
        self.assertGreaterEqual(failure_page.table.rowCount(), 1)

    def test_execution_page_masks_secret_detail(self):
        if ManagementConsoleDialog is None:
            self.skipTest("PySide6 unavailable")

        page = self.dialog.page(ExecutionPage)
        page.kind.setCurrentText("技能审计")
        self._wait()
        self.assertEqual(page.table.rowCount(), 1)
        page.table.setCurrentCell(0, 0)
        self._wait()
        detail = page.detail.toPlainText()
        self.assertNotIn("sk-abcdef1234567890", detail)
        self.assertIn("[REDACTED]", detail)

    def test_settings_page_reports_schema_and_manages_data(self):
        if ManagementConsoleDialog is None:
            self.skipTest("PySide6 unavailable")

        page = self.dialog.page(SettingsPage)
        text = page.info.toPlainText()
        self.assertIn("schema", text)
        self.assertIn("integrity", text)

        backup = page.backup_now()
        self.assertTrue(Path(backup["path"]).is_file())
        self.assertEqual(len(page.backup_entries), 1)

        export_path = Path(self.temp.name) / "console-export.zip"
        result = page.export_to(str(export_path))
        self.assertTrue(export_path.is_file())
        self.assertGreaterEqual(result["counts"]["messages"], 1)

        self.dialog.mark_restart("需要重启")
        self.dialog.refresh_all()
        self.assertIn("重启", self.dialog.restart_label.text())


if __name__ == "__main__":
    unittest.main()
