"""Qt desktop smoke tests, skipped when PySide6 is unavailable."""

import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_framework.companion.app import create_application_service
from agent_framework.companion.models import AgentMode, SearchResult
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.llm import LLMResponse, OfflineLLMClient, StreamEvent


try:
    from PySide6.QtCore import QPoint, QRect, QSize, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtGui import QInputMethodEvent, QKeySequence, QTextCursor
    from PySide6.QtWidgets import QApplication, QMessageBox
    from agent_framework.companion.ui.chat_view import EmptyState
    from agent_framework.companion.ui.qt_app import (
        RAIL_WIDTH,
        WINDOW_HEIGHT,
        WINDOW_WIDTH,
        CompanionWindow,
        side_dialog_geometry,
    )
    from agent_framework.companion.ui.theme import DEFAULT_THEME, theme
    from agent_framework.companion.ui.tray import TrayController, app_icon
    from agent_framework.companion.web.shell import Shell
except ImportError:
    QApplication = None
    CompanionWindow = None
    TrayController = None
    app_icon = None
    Shell = None
    QPoint = None
    QRect = None
    QSize = None
    QTest = None
    Qt = None
    QKeySequence = None
    QInputMethodEvent = None
    QTextCursor = None
    EmptyState = None
    RAIL_WIDTH = None
    WINDOW_WIDTH = None
    WINDOW_HEIGHT = None
    side_dialog_geometry = None
    DEFAULT_THEME = None
    theme = None


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
        self.runtime.apply_settings({"tool_permission": "strict"})
        self.runtime.set_mode(AgentMode.WORK)
        self.app = QApplication.instance() or QApplication([])
        self.window = CompanionWindow(create_application_service(self.runtime))
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
        self.runtime.apply_settings({"tool_permission": "balanced"})
        card = self.window.confirm_frame
        self.assertTrue(card.isVisible())
        self.assertEqual(card.status_pill.text(), "等待确认")
        self.assertIn("新建", card.summary_label.text())
        self.assertGreater(card.diff_layout.count(), 0)
        self.assertTrue(card.target_label.text())
        self.assertTrue(card.confirm_button.isEnabled())
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
        self.assertTrue(card.terminal)
        self.assertTrue(card.result_label.isVisible())
        self.assertTrue(card.undo_button.isVisible())
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
        self.assertEqual(card.status_pill.text(), "已撤销")
        self.assertTrue(card.result_label.isVisible())
        self.assertFalse(card.undo_button.isVisible())
        self.assertEqual(list(self.workspace.rglob("*.md")), [])
        self.assertEqual(
            self.store.get_receipt(receipt.receipt_id).undo_status,
            "undone",
        )

    def test_session_list_actions_and_shortcuts(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")

        self.store.add_message("chat_old", "user", "旧会话", AgentMode.CHAT)
        self.store.add_message("main", "user", "当前会话", AgentMode.CHAT)
        self.window.refresh_history()
        self.window.refresh_session_list()
        self.app.processEvents()

        self.assertTrue(self.window.session_group_rows)
        self.assertTrue(
            any(
                label.text() == "今天"
                for label, _rows in self.window.session_group_rows
            )
        )
        self.assertEqual(len(self.window.session_rows), 2)

        self.window.set_session_pinned("chat_old", True)
        self.assertEqual(self.window.session_rows[0].session_id, "chat_old")
        self.assertIn("置顶", self.window.session_rows[0].meta.text())
        self.assertIn("已置顶", self.window.status.text())
        self.assertEqual(
            self.window._summary_for("chat_old")["pinned_at"] != "",
            True,
        )

        self.window.set_session_unread("chat_old", True)
        row = self.window.session_rows[0]
        self.assertIn("未读", row.meta.text())
        self.assertIn("未读", row.accessibleName())
        self.window.activate_session("chat_old")
        self.assertEqual(self.runtime.session_id, "chat_old")
        self.assertEqual(
            self.window._summary_for("chat_old")["unread"],
            0,
        )

        with patch(
            "agent_framework.companion.ui.qt_app.QInputDialog.getText",
            return_value=("整理计划", True),
        ):
            self.window.rename_session("chat_old")
        self.assertEqual(
            self.window._summary_for("chat_old")["title"],
            "整理计划",
        )
        self.assertIn("整理计划", self.window.status.text())

        with patch.object(QMessageBox, "warning", return_value=QMessageBox.Ok):
            with patch(
                "agent_framework.companion.ui.qt_app.QInputDialog.getText",
                return_value=("   ", True),
            ):
                self.window.rename_session("chat_old")
        self.assertIn("不能为空", self.window.status.text())
        self.assertEqual(
            self.window._summary_for("chat_old")["title"],
            "整理计划",
        )

        self.assertEqual(
            self.window.search_shortcut.key(),
            QKeySequence("Ctrl+K"),
        )
        self.assertEqual(
            self.window.new_shortcut.key(),
            QKeySequence("Ctrl+N"),
        )
        self.window.activateWindow()
        self.app.processEvents()
        self.window.search_shortcut.activated.emit()
        self.app.processEvents()
        self.assertTrue(self.window.search_edit.isVisible())
        self.assertNotEqual(
            self.window.search_edit.focusPolicy(),
            Qt.NoFocus,
        )
        self.window.search_edit.setText("旧会话")
        self.app.processEvents()
        visible = [
            row.session_id
            for row in self.window.session_rows
            if row.isVisible()
        ]
        self.assertEqual(visible, ["chat_old"])
        self.assertGreaterEqual(
            sum(
                1
                for label, _rows in self.window.session_group_rows
                if label.isVisible()
            ),
            1,
        )
        self.window.search_edit.setText("整理")
        self.app.processEvents()
        self.assertEqual(
            [
                row.session_id
                for row in self.window.session_rows
                if row.isVisible()
            ],
            ["chat_old"],
        )
        QTest.keyClick(self.window.search_edit, Qt.Key_Escape)
        self.app.processEvents()
        self.assertFalse(self.window.search_edit.isVisible())

        self.window.new_shortcut.activated.emit()
        self.assertTrue(
            self.wait_until(
                lambda: self.runtime.session_id not in ("main", "chat_old")
            ),
            "Ctrl+N 没有新建会话",
        )
        created = self.runtime.session_id
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.window.delete_session("chat_old")
        self.assertIsNone(self.store.get_session_mode("chat_old"))
        self.assertIn("已删除", self.window.status.text())
        self.assertEqual(self.runtime.session_id, created)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
            self.window.delete_session(created)
        self.assertIsNotNone(self.store.get_session_mode(created))
        self.assertEqual(self.runtime.session_id, created)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.window.delete_session(created)
        self.assertIsNone(self.store.get_session_mode(created))
        self.assertNotEqual(self.runtime.session_id, created)
        self.assertEqual(
            self.window._session_summaries[0]["id"],
            self.runtime.session_id,
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
        self.ui_state = self.window.ui_state

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
        self.assertTrue(dialog.isWindow())

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
        self.assertEqual(
            (self.window.width(), self.window.height()),
            (WINDOW_WIDTH, WINDOW_HEIGHT),
        )
        self.assertGreaterEqual(self.window.minimumWidth(), 700)
        self.assertEqual(self.window.rail.width(), RAIL_WIDTH)
        self.assertTrue(self.window.rail.isVisible())
        self.assertTrue(self.window.inspector.isVisible())
        self.assertEqual(self.window.chat.objectName(), "chat")
        self.assertIs(self.window.application.runtime, self.runtime)
        self.assertIs(self.window.runtime, self.runtime)

    def test_layout_toggles_hide_rail_and_inspector(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.window.show()
        self.app.processEvents()

        self.window.toggle_sidebar()
        self.assertFalse(self.window.rail.isVisible())
        self.assertIs(self.window.ui_state.load().get("rail_visible"), False)
        self.window.toggle_sidebar()
        self.assertTrue(self.window.rail.isVisible())

        self.window.toggle_inspector(False)
        self.assertFalse(self.window.inspector.isVisible())
        self.assertIs(self.window.ui_state.load().get("inspector_visible"), False)
        self.window.toggle_inspector(True)
        self.assertTrue(self.window.inspector.isVisible())

    def test_responsive_layout_collapses_inspector_then_rail(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.window.show()
        self.window.resize(1100, WINDOW_HEIGHT)
        self.app.processEvents()
        self.assertTrue(self.window.inspector.isVisible())
        self.assertTrue(self.window.rail.isVisible())

        self.window.resize(900, WINDOW_HEIGHT)
        self.app.processEvents()
        self.assertFalse(self.window.inspector.isVisible())
        self.assertTrue(self.window.rail.isVisible())

        self.window.resize(780, WINDOW_HEIGHT)
        self.app.processEvents()
        self.assertFalse(self.window.inspector.isVisible())
        self.assertFalse(self.window.rail.isVisible())
        self.assertEqual(self.window.rail_host.width(), 0)

        self.window.toggle_sidebar()
        self.app.processEvents()
        self.assertTrue(self.window._rail_drawer_open)
        self.assertTrue(self.window.rail.isVisible())
        self.assertEqual(self.window.rail.width(), RAIL_WIDTH)
        self.window.toggle_sidebar()
        self.app.processEvents()
        self.assertFalse(self.window._rail_drawer_open)
        self.assertFalse(self.window.rail.isVisible())

        self.window.resize(1100, WINDOW_HEIGHT)
        self.app.processEvents()
        self.assertTrue(self.window.inspector.isVisible())
        self.assertTrue(self.window.rail.isVisible())
        self.assertEqual(self.window.rail_host.width(), RAIL_WIDTH)

    def test_expired_proposal_cannot_be_confirmed_from_the_card(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        workspace = Path(self.temp.name) / "workspace"
        workspace.mkdir()
        self.runtime.select_workspace(str(workspace))
        proposal = self.runtime.notes.create_proposal(
            session_id=self.runtime.session_id,
            query="过期写入",
            content="stale",
            mode=AgentMode.WORK,
            ttl_hours=-1,
        )
        self.runtime.store.create_proposal(proposal)

        self.window.show()
        self.window.show_proposal(proposal)
        self.app.processEvents()
        card = self.window.confirm_frame
        self.assertTrue(card.isVisible())
        self.assertEqual(card.status_pill.text(), "已过期")
        self.assertFalse(card.confirm_button.isEnabled())
        self.assertIn("过期", card.error_label.text())

        self.window.confirm_action()
        self.app.processEvents()
        self.assertEqual(list(workspace.rglob("*.md")), [])
        self.assertEqual(
            self.runtime.store.get_proposal(proposal.action_id).status.value,
            "awaiting_confirmation",
        )

    def test_theme_switch_restyles_window(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.assertEqual(self.window.theme_name, DEFAULT_THEME)
        accent = theme(self.window.theme_name)["accent"]
        self.assertIn(accent, self.window.styleSheet())

        self.window.toggle_theme()
        self.assertEqual(self.window.theme_name, "pearl-white")
        self.assertEqual(self.window.ui_state.theme(), "pearl-white")
        light = theme("pearl-white")
        self.assertIn(light["accent"], self.window.styleSheet())
        self.window.toggle_theme()
        self.assertEqual(self.window.theme_name, DEFAULT_THEME)

    def test_rail_lists_workspace_and_sessions(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.window.show()
        self.app.processEvents()
        workspace = Path(self.temp.name) / "workspace"
        workspace.mkdir()
        self.runtime.select_workspace(str(workspace))
        self.window.refresh_workspace()
        self.store.add_message("main", "user", "帮我整理书架", AgentMode.CHAT)
        self.store.add_message(
            "main", "assistant", "好，我们先把书按主题分组。", AgentMode.CHAT
        )
        self.window.refresh_session_list()
        self.app.processEvents()

        self.assertEqual(self.window.workspace_name.full_text(), "workspace")
        self.assertIn("workspace", self.window.workspace_name.toolTip())
        self.assertEqual(len(self.window.session_rows), 1)
        row = self.window.session_rows[0]
        self.assertEqual(row.session_id, "main")
        self.assertEqual(row.objectName(), "sessionActive")
        self.assertIn("帮我整理书架", row.title.full_text())
        self.assertIn("2 条消息", row.toolTip())
        self.assertIn("好，我们先把书", row.toolTip())
        for attribute in (
            "memory_button",
            "affect_button",
            "persona_button",
            "proactive_button",
            "task_button",
            "console_button",
        ):
            self.assertFalse(hasattr(self.window, attribute))
        self.assertTrue(self.window.settings_button.isVisible())
        self.assertEqual(self.window.settings_button.text(), "设置")

    def test_new_session_and_row_click_switch(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.store.add_message("main", "user", "旧会话的问题", AgentMode.CHAT)
        self.window.refresh_session_list()
        self.window.new_session()
        new_id = self.runtime.session_id
        self.assertNotEqual(new_id, "main")
        self.assertEqual(self.window.ui_state.active_session(), new_id)
        self.assertEqual(self.runtime.proactive_service.session_id, new_id)
        self.assertEqual(self.runtime.work.session_id, new_id)
        self.window.new_session()
        self.assertEqual(self.runtime.session_id, new_id)
        self.assertEqual(
            [row.session_id for row in self.window.session_rows],
            [new_id, "main"],
        )

        main_row = next(
            row for row in self.window.session_rows if row.session_id == "main"
        )
        QTest.mouseClick(main_row, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(self.runtime.session_id, "main")
        self.assertEqual(self.window.ui_state.active_session(), "main")
        active = next(
            row for row in self.window.session_rows if row.session_id == "main"
        )
        self.assertEqual(active.objectName(), "sessionActive")

    def test_search_filters_session_rows(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.window.show()
        self.app.processEvents()
        self.store.add_message("plan", "user", "帮我整理书架", AgentMode.CHAT)
        self.store.add_message("code", "user", "修复登录问题", AgentMode.CHAT)
        self.window.refresh_session_list()
        self.window.toggle_search()
        self.assertTrue(self.window.search_edit.isVisible())

        self.window.search_edit.setText("书架")
        self.app.processEvents()
        visible = [
            row.session_id for row in self.window.session_rows if not row.isHidden()
        ]
        self.assertEqual(visible, ["plan"])

        self.window.search_edit.setText("找不到的内容")
        self.app.processEvents()
        self.assertTrue(self.window.session_empty.isVisible())
        self.assertIn("没有匹配", self.window.session_empty.text())

        self.window.toggle_search()
        self.assertFalse(self.window.search_edit.isVisible())
        self.assertTrue(
            all(not row.isHidden() for row in self.window.session_rows)
        )

    def test_active_session_restores_in_new_window(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.store.add_message("main", "user", "主会话的问题", AgentMode.CHAT)
        self.store.add_message("chat_old", "user", "上次的会话", AgentMode.CHAT)
        self.window.activate_session("chat_old")
        self.assertEqual(self.window.ui_state.active_session(), "chat_old")

        root = Path(self.temp.name)
        runtime = CompanionRuntime(
            CompanionStore(root / "db.sqlite"),
            OfflineLLMClient(),
            SearchService(),
            root,
            offline=True,
        )
        restored = CompanionWindow(runtime)
        try:
            self.assertEqual(runtime.session_id, "chat_old")
            self.assertEqual(len(restored.session_rows), 2)
        finally:
            restored.close()
            self.app.processEvents()

    def test_switching_is_blocked_while_generating(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.store.add_message("other", "user", "另一个会话", AgentMode.CHAT)
        self.window.refresh_session_list()
        self.window.active_generation = "busy"
        try:
            self.window.activate_session("other")
        finally:
            self.window.active_generation = None
        self.assertEqual(self.runtime.session_id, "main")
        self.assertIn("稍后", self.window.status.text())

    def test_settings_hub_opens_panels_and_appearance(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.window.open_settings()
        dialog = self.window.settings_dialog
        self.app.processEvents()

        self.assertIsNotNone(dialog)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.parent(), self.window)
        for attribute in (
            "memory_button",
            "affect_button",
            "persona_button",
            "proactive_button",
            "task_button",
            "console_button",
        ):
            self.assertTrue(hasattr(dialog, attribute))

        self.assertTrue(dialog.theme_button.text().startswith("主题："))
        previous_theme = self.window.theme_name
        dialog.theme_button.click()
        self.assertNotEqual(self.window.theme_name, previous_theme)
        self.assertIn(theme(self.window.theme_name)["label"], dialog.theme_button.text())

        self.assertFalse(self.window.overlay_visible())
        dialog.overlay_button.click()
        self.app.processEvents()
        self.assertFalse(self.window.overlay_visible())
        self.assertFalse(dialog.overlay_button.isChecked())
        self.assertIn("--ui web", self.window.status.text())

        QTest.mouseClick(dialog.memory_button, Qt.LeftButton)
        self.assertTrue(
            self.wait_until(lambda: self.window.memory_dialog is not None),
            "设置页没有打开记忆面板",
        )
        self.app.processEvents()
        self.assertIsNone(self.window.settings_dialog)
        self.assertTrue(self.window.memory_dialog.isVisible())
        self.window.memory_dialog.close()
        self.app.processEvents()

    def test_settings_hub_has_categories_search_and_summary(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.window.open_settings()
        dialog = self.window.settings_dialog
        self.app.processEvents()
        self.assertEqual(
            set(dialog.category_buttons), {"companion", "work", "data"}
        )
        self.assertIs(dialog.stack.currentWidget(), dialog.pages["companion"])
        self.assertTrue(dialog.search_edit.accessibleName())
        for item in dialog.category_buttons.values():
            self.assertNotEqual(item.focusPolicy(), Qt.NoFocus)
            self.assertTrue(item.accessibleName())

        self.assertIn("记忆", dialog.summary.text.text())
        self.assertTrue(dialog.summary.detail.isHidden())
        self.assertEqual(dialog.summary.toggle_button.text(), "更多")
        dialog.summary.toggle_button.click()
        self.app.processEvents()
        self.assertEqual(dialog.summary.toggle_button.text(), "收起")
        self.assertFalse(dialog.summary.detail.isHidden())
        dialog.summary.toggle_button.click()

        QTest.mouseClick(dialog.category_buttons["work"], Qt.LeftButton)
        self.app.processEvents()
        self.assertIs(dialog.stack.currentWidget(), dialog.pages["work"])
        self.assertTrue(dialog.category_buttons["work"].property("active"))
        self.assertFalse(dialog.category_buttons["companion"].property("active"))
        self.assertIn("主动关心", dialog.summary.text.text())

        dialog.search_edit.setText("记忆")
        self.app.processEvents()
        self.assertIs(dialog.stack.currentWidget(), dialog.search_page)
        self.assertEqual(len(dialog.search_rows), 1)
        self.assertEqual(dialog.search_rows[0].title.text(), "记忆")
        self.assertIn("找到 1 项设置", dialog.summary.text.text())

        QTest.keyClick(dialog.search_edit, Qt.Key_Escape)
        self.app.processEvents()
        self.assertEqual(dialog.search_edit.text(), "")
        self.assertIs(dialog.stack.currentWidget(), dialog.pages["work"])

        dialog.search_edit.setText("浮窗")
        self.app.processEvents()
        self.assertEqual(len(dialog.search_rows), 1)
        self.assertEqual(dialog.search_rows[0].title.text(), "桌面浮窗")
        dialog.search_edit.clear()
        self.app.processEvents()

    def test_font_scale_and_reduce_motion_persist_from_settings(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.window.show()
        self.app.processEvents()
        self.window.open_settings()
        dialog = self.window.settings_dialog
        self.app.processEvents()

        index = dialog.font_combo.findData(200)
        self.assertGreaterEqual(index, 0)
        dialog.font_combo.setCurrentIndex(index)
        self.app.processEvents()
        self.assertEqual(self.window.font_scale, 200)
        self.assertEqual(self.ui_state.font_scale(), 200)
        self.assertIn("font-size: 26px", self.window.styleSheet())
        self.assertEqual(self.window.send_button.width(), 156)
        line = self.window.input.fontMetrics().lineSpacing()
        self.assertGreaterEqual(self.window.input.height(), line * 2)
        self.assertGreaterEqual(
            self.window.send_button.height(),
            self.window.send_button.fontMetrics().height(),
        )
        self.assertGreaterEqual(
            dialog.nav.width(),
            int(180 * 2),
        )
        shot = self.window.grab()
        self.assertFalse(shot.isNull())

        dialog.motion_check.setChecked(True)
        self.app.processEvents()
        self.assertTrue(self.window.reduce_motion)
        self.assertTrue(self.window.chat._reduce_motion)
        self.assertTrue(self.ui_state.reduce_motion())
        self.assertFalse(dialog.motion_check.isHidden())

        dialog.motion_check.setChecked(False)
        self.app.processEvents()
        self.assertFalse(self.window.reduce_motion)
        self.assertFalse(self.window.chat._reduce_motion)

    def test_primary_controls_expose_names_tooltips_and_shortcut(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        controls = (
            self.window.sidebar_button,
            self.window.inspector_button,
            self.window.minimize_button,
            self.window.workspace_more,
            self.window.workspace_add,
            self.window.send_button,
            self.window.settings_button,
            self.window.new_task_button,
            self.window.search_button,
        )
        for button in controls:
            self.assertTrue(button.accessibleName(), button.text())
        for button in (
            self.window.sidebar_button,
            self.window.workspace_more,
            self.window.workspace_add,
            self.window.settings_button,
        ):
            self.assertTrue(button.toolTip())
        self.assertIn("Ctrl+,", self.window.settings_button.toolTip())
        self.assertEqual(
            self.window.settings_shortcut.key(),
            QKeySequence("Ctrl+,"),
        )
        self.assertNotEqual(self.window.input.focusPolicy(), Qt.NoFocus)
        self.assertTrue(self.window.empty_state_actions_keyboard_reachable())

    def test_offscreen_screenshots_cover_main_desktop_sizes(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.window.show()
        self.app.processEvents()
        for width, height in ((1366, 768), (1920, 1080)):
            self.window.resize(width, height)
            self.app.processEvents()
            shot = self.window.grab()
            self.assertFalse(shot.isNull())
            self.assertEqual(shot.width(), self.window.width())
            self.assertEqual(shot.height(), self.window.height())
            path = Path(self.temp.name) / "shot-{}.png".format(width)
            self.assertTrue(shot.save(str(path)))
            self.assertGreater(path.stat().st_size, 1000)
            self.assertTrue(self.window.chat.isVisible())
            self.assertTrue(self.window.send_button.isVisible())
            self.assertLessEqual(
                self.window.send_button.geometry().right(),
                self.window.width(),
            )

    def test_side_dialog_geometry_prefers_right_then_left(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        parent = QRect(100, 100, 1000, 700)
        screen = QRect(0, 0, 1920, 1080)
        x, y, width, height = side_dialog_geometry(QSize(420, 500), parent, screen)
        self.assertEqual((x, y, width, height), (1116, 100, 420, 500))
        self.assertGreaterEqual(x, parent.x() + parent.width())

        left_parent = QRect(900, 100, 1000, 700)
        x, _y, width, _height = side_dialog_geometry(QSize(420, 500), left_parent, screen)
        self.assertEqual((x, width), (464, 420))
        self.assertLessEqual(x + width, left_parent.x())

        tight = QRect(0, 0, 1200, 1080)
        x, y, width, height = side_dialog_geometry(QSize(420, 500), parent, tight)
        self.assertLessEqual(x, 1200 - width)
        self.assertLessEqual(y + height, 1080)

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
        self.assertIn("DeepSeek 娘", info)
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

    def test_qt_entry_has_no_desktop_overlay_and_points_to_web(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        stored = self.window.ui_state.overlay_visible()
        self.assertFalse(self.window.overlay_visible())
        self.window.set_overlay_visible(True)
        self.app.processEvents()
        self.assertFalse(self.window.overlay_visible())
        self.assertIn("--ui web", self.window.status.text())
        self.assertEqual(self.window.ui_state.overlay_visible(), stored)

    def test_shell_overlay_gates_on_pet_availability(self):
        if Shell is None:
            self.skipTest("PySide6 unavailable")
        namespace = SimpleNamespace
        self.assertFalse(
            Shell.pet_available(
                namespace(adapter=namespace(assets=lambda: {"pet": {"available": False}}))
            )
        )
        self.assertTrue(
            Shell.pet_available(
                namespace(adapter=namespace(assets=lambda: {"pet": {"available": True}}))
            )
        )
        self.assertTrue(
            Shell.pet_available(
                namespace(
                    adapter=namespace(
                        assets=lambda: {"pet": {"available": True, "renderer": "mesh"}}
                    )
                )
            )
        )
        self.assertFalse(
            Shell.pet_available(
                namespace(
                    adapter=namespace(
                        assets=lambda: (_ for _ in ()).throw(RuntimeError("boom"))
                    )
                )
            )
        )

    def test_window_and_tray_icons_never_draw_the_old_figure(self):
        if app_icon is None:
            self.skipTest("PySide6 unavailable")
        self.assertFalse(app_icon().isNull())
        folder = Path(self.temp.name) / "assets" / "remielle"
        folder.mkdir(parents=True)
        (folder / "avatar.png").write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        )
        self.assertFalse(app_icon(self.runtime).isNull())

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
            self.assertFalse(controller.overlay_action.isEnabled())
            self.assertIn("--ui web", controller.overlay_action.toolTip())
        finally:
            controller.close()

    def test_header_minimize_keeps_window_recoverable(self):
        if CompanionWindow is None or TrayController is None:
            self.skipTest("PySide6 unavailable")
        window = self.window
        window.show()
        self.app.processEvents()

        # A Tool window never reaches the taskbar, so hiding it left no way back
        # whenever the system tray was unavailable or collapsed.
        self.assertNotEqual(window.windowFlags() & Qt.Tool, Qt.Tool)
        self.assertTrue(window.windowFlags() & Qt.WindowMinimizeButtonHint)
        # The Qt entry has no floating window; the panel must not cover the
        # window the user is actually working in.
        self.assertFalse(window.windowFlags() & Qt.WindowStaysOnTopHint)
        self.assertFalse(window.windowFlags() & Qt.WindowStaysOnBottomHint)
        self.assertFalse(window.windowIcon().isNull())

        with patch.object(window, "quit_application") as quit_application:
            window.minimize_button.click()
            self.app.processEvents()
        quit_application.assert_not_called()
        self.assertTrue(window.isMinimized())
        self.assertTrue(window.isVisible())

        with patch(
            "agent_framework.companion.ui.tray."
            "QSystemTrayIcon.isSystemTrayAvailable",
            return_value=True,
        ):
            controller = TrayController(window, self.runtime)
        try:
            self.assertEqual(controller.window_action.text(), "还原主窗口")

            window.toggle_window()
            self.app.processEvents()
            self.assertFalse(window.isMinimized())
            self.assertTrue(window.isVisible())
            controller.refresh()
            self.assertEqual(controller.window_action.text(), "隐藏主窗口")

            window.toggle_window()
            controller.refresh()
            self.assertEqual(controller.window_action.text(), "显示主窗口")
            window.toggle_window()
            self.app.processEvents()
            self.assertTrue(window.isVisible())
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
        self.assertIn("已停止", self.window.status.text())
        transcript = self.window.chat.plain_text()
        self.assertNotIn("迟到内容", transcript)
        self.assertIn("已停止生成", transcript)

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

    def test_composer_grows_with_text_and_reports_length(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        field = self.window.input
        minimum = field.height()
        field.setPlainText("第一行\n第二行\n第三行")
        self.app.processEvents()
        grown = field.height()
        self.assertGreater(grown, minimum)

        field.setPlainText("\n".join("第 {} 行".format(i) for i in range(40)))
        self.app.processEvents()
        capped = field.height()
        self.assertGreaterEqual(capped, grown)
        self.assertLessEqual(capped, grown * 3)
        self.assertIn("字", self.window.composer_hint.text())

        field.setPlainText("字" * 4001)
        self.assertTrue(self.window.composer_hint.property("warning"))
        self.assertIn("较长", self.window.composer_hint.text())
        field.setPlainText("字" * 8001)
        self.assertTrue(self.window.composer_hint.property("error"))
        self.assertIn("上限", self.window.composer_hint.text())

        self.window.send()
        self.assertIsNone(self.window.active_generation)
        self.assertIn("上限", self.window.status.text())
        self.assertEqual(len(field.toPlainText()), 8001)

    def test_drafts_are_scoped_per_session(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.store.add_message("other", "user", "另一个会话", AgentMode.CHAT)
        self.window.refresh_session_list()

        self.window.input.setPlainText("主会话草稿")
        self.window._save_draft_now()
        self.assertEqual(self.store.get_draft("main"), "主会话草稿")

        self.window.activate_session("other")
        self.assertEqual(self.window.input.toPlainText(), "")
        self.window.input.setPlainText("另一会话草稿")
        self.window._save_draft_now()

        self.window.activate_session("main")
        self.assertEqual(self.window.input.toPlainText(), "主会话草稿")
        self.assertEqual(self.store.get_draft("other"), "另一会话草稿")

        self.window.input.setPlainText("即将发送")
        self.window._save_draft_now()
        self.window.send()
        self.assertEqual(self.window.input.toPlainText(), "")
        self.assertEqual(self.store.get_draft("main"), "")
        self.assertTrue(
            self.wait_until(lambda: self.window.stream_worker is None)
        )

    def test_shift_enter_and_ime_composition_do_not_send(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        field = self.window.input
        field.setPlainText("第一行")
        field.moveCursor(QTextCursor.End)

        QTest.keyClick(field, Qt.Key_Return, Qt.ShiftModifier)
        self.app.processEvents()
        self.assertIn("\n", field.toPlainText())
        self.assertIsNone(self.window.active_generation)

        preedit = QInputMethodEvent("ni", [])
        QApplication.sendEvent(field, preedit)
        self.assertTrue(self.window._composing)
        QTest.keyClick(field, Qt.Key_Return)
        self.app.processEvents()
        self.assertIsNone(self.window.active_generation)

        commit = QInputMethodEvent()
        commit.setCommitString("你")
        QApplication.sendEvent(field, commit)
        self.assertFalse(self.window._composing)
        self.assertIn("你", field.toPlainText())
        QTest.keyClick(field, Qt.Key_Return)
        self.assertIsNotNone(self.window.active_generation)
        self.assertEqual(self.window.send_button.text(), "停止")
        self.assertTrue(
            self.wait_until(lambda: self.window.stream_worker is None)
        )
        self.assertEqual(self.window.send_button.text(), "发送")

    def test_composer_pills_follow_mode_and_workspace(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.assertEqual(self.window.mode_pill.text(), "陪伴模式")
        self.assertEqual(self.window.workspace_pill.text(), "未选择工作区")
        self.assertIn("工作目录", self.window.workspace_pill.toolTip())
        self.assertEqual(self.window.context_pill.text(), "上下文待命")
        self.assertTrue(self.window.mode_pill.accessibleName())
        self.assertTrue(self.window.context_pill.accessibleName())

        self.window.application.select_workspace(self.temp.name)
        self.window.refresh_workspace()
        self.assertIn("工作区", self.window.workspace_pill.text())
        self.assertEqual(
            self.window.workspace_pill.toolTip(),
            self.temp.name,
        )

        self.window.work_button.click()
        self.app.processEvents()
        self.assertEqual(self.window.mode_pill.text(), "工作模式")
        self.window.set_mode(AgentMode.CHAT)
        self.assertEqual(self.window.mode_pill.text(), "陪伴模式")

    def test_context_pill_renders_the_projection_numbers(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        runtime = self.window.runtime
        runtime.max_context_chars = 4000
        runtime.settings = {"max_tokens": 1000}
        self.window.runtime.last_context = SimpleNamespace(
            budget_chars=4000, used_chars=1500, compressed=True
        )
        self.window.refresh_composer_hint()

        self.assertEqual(self.window.context_pill.text(), "上下文 38%")
        self.assertIn("上下文 1500 / 4000 字符", self.window.context_pill.toolTip())
        self.assertIn("历史已压缩", self.window.context_pill.toolTip())
        self.assertEqual(
            self.window.context_pill.accessibleName(),
            "上下文：" + self.window.context_pill.toolTip(),
        )

        # The width is the window in force now, not the one the last prompt was
        # trimmed against, so saving a bigger 上下文窗口 moves the reading at once.
        runtime.max_context_chars = 30000
        self.window.refresh_composer_hint()
        self.assertEqual(self.window.context_pill.text(), "上下文 5%")
        self.assertIn("上下文 1500 / 30000 字符", self.window.context_pill.toolTip())

        # 待命 means there is nothing to measure: neither the live window nor the
        # prompt that was last assembled carries a budget.
        runtime.max_context_chars = 0
        self.window.runtime.last_context = SimpleNamespace(
            budget_chars=0, used_chars=0, compressed=False
        )
        self.window.refresh_composer_hint()
        self.assertEqual(self.window.context_pill.text(), "上下文待命")

    def test_empty_state_offers_mode_aware_quick_start(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        self.window.set_mode(AgentMode.CHAT)
        self.app.processEvents()
        self.assertIn("今天想聊点什么", self.window.chat.plain_text())
        empty = self.window.chat.findChild(EmptyState)
        self.assertIsNotNone(empty)
        self.assertEqual(empty.property("mode"), "chat")
        self.assertTrue(empty.recent_label.isHidden())

        empty.action_buttons[0].click()
        self.app.processEvents()
        prompt = "整理今天的想法"
        self.assertEqual(self.window.input.toPlainText(), prompt)
        self.assertEqual(self.window.input.textCursor().position(), len(prompt))

        self.window.set_mode(AgentMode.WORK)
        self.app.processEvents()
        self.assertIn("把一件事交给我", self.window.chat.plain_text())
        empty = self.window.chat.findChild(EmptyState)
        self.assertEqual(empty.property("mode"), "work")

    def test_empty_state_shows_the_latest_work_task(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        task = SimpleNamespace(
            state="EXECUTING",
            card=SimpleNamespace(title="写一份周报"),
        )
        self.window.runtime.work_tasks = lambda limit=20: [task]
        self.window.set_mode(AgentMode.WORK)
        self.app.processEvents()
        empty = self.window.chat.findChild(EmptyState)
        self.assertFalse(empty.recent_label.isHidden())
        self.assertIn("最近任务：写一份周报", empty.recent_label.text())
        self.assertIn("执行中", empty.recent_label.text())

        self.window.set_mode(AgentMode.CHAT)
        self.app.processEvents()
        empty = self.window.chat.findChild(EmptyState)
        self.assertTrue(empty.recent_label.isHidden())

    def test_run_bar_follows_the_stream_stages(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        llm = GatedStreamLLM()
        self.runtime.llm_client = llm
        self.runtime.offline = False
        self.runtime.tool_calling_supported = True
        run_bar = self.window.run_bar
        try:
            self.window.input.setPlainText("讲讲今天的天气")
            self.window.send_button.click()
            self.assertTrue(
                self.wait_until(
                    lambda: llm.started.is_set()
                    and run_bar.stage_label.text() == "生成回答"
                ),
                "运行条没有进入生成回答阶段",
            )
            self.assertFalse(run_bar.isHidden())
            self.assertEqual(run_bar.run_id, self.window.active_generation)
            self.assertTrue(run_bar.stop_button.isHidden())
            self.assertEqual(self.window.send_button.text(), "停止")
            self.assertTrue(self.window.send_button.isEnabled())

            llm.release.set()
            self.assertTrue(
                self.wait_until(
                    lambda: run_bar.stage_label.text() == "已完成"
                    and self.window.stream_worker is None
                ),
                "运行条没有收束到已完成",
            )
            self.assertEqual(run_bar.detail_label.text(), "回复完成")
            self.assertTrue(run_bar.stop_button.isHidden())
            self.assertEqual(self.window.send_button.text(), "发送")
            self.assertGreaterEqual(self.window.chat.date_divider_count(), 1)
        finally:
            llm.release.set()
            self.wait_until(
                lambda: self.window.stream_worker is None,
                timeout=5,
            )

    def test_run_bar_timeline_is_the_projection_step_list(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        llm = GatedStreamLLM()
        self.runtime.llm_client = llm
        self.runtime.offline = False
        self.runtime.tool_calling_supported = True
        run_bar = self.window.run_bar
        try:
            self.window.input.setPlainText("讲讲今天的天气")
            self.window.send_button.click()
            self.assertTrue(
                self.wait_until(
                    lambda: llm.started.is_set()
                    and len(run_bar._steps) >= 2
                ),
                "运行条没有拿到投影的步骤",
            )
            self.assertFalse(run_bar.steps_toggle.isHidden())
            self.assertTrue(run_bar.steps_scroll.isHidden())
            run_bar.steps_toggle.click()
            self.app.processEvents()
            rows = run_bar.steps_label.text().splitlines()
            self.assertEqual(len(rows), len(run_bar._steps))
            self.assertIn("思考中", rows[0])
            self.assertIn("生成回答", rows[-1])

            llm.release.set()
            self.assertTrue(
                self.wait_until(
                    lambda: self.window.stream_worker is None,
                )
            )
            self.app.processEvents()
            self.assertEqual(run_bar.stage_label.text(), "已完成")
            self.assertEqual(
                len(run_bar.steps_label.text().splitlines()),
                len(run_bar._steps),
            )
            self.assertGreaterEqual(len(run_bar._steps), 2)
        finally:
            llm.release.set()
            self.wait_until(
                lambda: self.window.stream_worker is None,
                timeout=5,
            )

    def test_window_stop_marks_the_run_bar_as_cancelled(self):
        if CompanionWindow is None:
            self.skipTest("PySide6 unavailable")
        llm = GatedStreamLLM()
        self.runtime.llm_client = llm
        self.runtime.offline = False
        self.runtime.tool_calling_supported = True
        try:
            self.window.input.setPlainText("停一下")
            self.window.send_button.click()
            self.assertTrue(self.wait_until(lambda: llm.started.is_set()))
            self.window.stop_button.click()
            llm.release.set()
            self.assertTrue(
                self.wait_until(
                    lambda: self.window.run_bar.stage_label.text() == "已取消"
                ),
                "停止后运行条没有标记为已取消",
            )
            self.assertEqual(
                self.window.run_bar.detail_label.text(),
                "已停止生成",
            )
        finally:
            llm.release.set()
            self.wait_until(
                lambda: self.window.stream_worker is None,
                timeout=5,
            )

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
