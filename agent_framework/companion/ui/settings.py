"""Settings hub: category navigation, search and per-section status summaries."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .theme import FONT_SCALES, spacing

PANEL_ENTRIES = (
    ("memory_button", "记忆", "账本、来源与遗忘", "open_memory"),
    ("affect_button", "情绪", "状态估计与纠正", "open_affect"),
    ("persona_button", "人设", "称呼、边界与人设包", "open_persona"),
    ("proactive_button", "主动", "开关、频率与事件", "open_proactive"),
    ("task_button", "任务", "任务卡与审批", "open_work"),
    ("console_button", "管理", "数据、备份与审计", "open_console"),
)
ENTRY_MAP = {
    attribute: (name, detail, method)
    for attribute, name, detail, method in PANEL_ENTRIES
}
CATEGORIES = (
    (
        "companion",
        "陪伴",
        "记忆、情绪与人设",
        ("memory_button", "affect_button", "persona_button"),
    ),
    (
        "work",
        "工作",
        "主动关心与任务卡",
        ("proactive_button", "task_button"),
    ),
    (
        "data",
        "数据与外观",
        "管理、主题与浮窗",
        ("console_button",),
    ),
)
APPEARANCE_ENTRIES = (
    ("主题", "切换深色或珍珠白外观", "theme"),
    ("桌面浮窗", "在桌面上显示或隐藏角色浮窗", "overlay"),
)


def _clip(value, limit):
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class SettingsNavItem(QFrame):
    """Focusable category chip used by the settings rail."""

    clicked = Signal(str)

    def __init__(self, key, title, summary, parent=None):
        super().__init__(parent)
        self.key = str(key)
        self.setObjectName("settingsNavItem")
        self.setProperty("active", False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName("{}：{}".format(title, summary))
        self.setToolTip("{}（{}）".format(title, summary))
        box = QVBoxLayout(self)
        box.setContentsMargins(10, 7, 10, 8)
        box.setSpacing(1)
        self.title = QLabel(title)
        self.title.setObjectName("settingsNavTitle")
        self.summary = QLabel(summary)
        self.summary.setObjectName("settingsNavMeta")
        for label in (self.title, self.summary):
            label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        box.addWidget(self.title)
        box.addWidget(self.summary)

    def set_active(self, active):
        self.setProperty("active", bool(active))
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.key)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit(self.key)
            event.accept()
            return
        super().keyPressEvent(event)


class SettingsRow(QFrame):
    clicked = Signal()

    def __init__(self, title, detail, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsRow")
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("{}：{}".format(title, detail))
        self.setToolTip("{}（{}）".format(title, detail))
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 7, 12, 8)
        row.setSpacing(8)
        column = QVBoxLayout()
        column.setSpacing(1)
        self.title = QLabel(title)
        self.title.setObjectName("settingsTitle")
        self.detail = QLabel(detail)
        self.detail.setObjectName("faint")
        column.addWidget(self.title)
        column.addWidget(self.detail)
        row.addLayout(column, 1)
        arrow = QLabel("›")
        arrow.setObjectName("settingsArrow")
        row.addWidget(arrow)
        for label in (self.title, self.detail, arrow):
            label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class SettingsSummary(QFrame):
    """Status card with a collapsed detail line for progressive disclosure."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsSummaryCard")
        box = QVBoxLayout(self)
        box.setContentsMargins(12, 9, 12, 10)
        box.setSpacing(spacing("sm"))
        head = QHBoxLayout()
        head.setSpacing(8)
        self.text = QLabel()
        self.text.setObjectName("settingsSummaryText")
        self.text.setWordWrap(True)
        head.addWidget(self.text, 1)
        self.toggle_button = QPushButton("更多")
        self.toggle_button.setObjectName("settingsExpand")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setCursor(Qt.PointingHandCursor)
        self.toggle_button.setAccessibleName("展开设置状态详情")
        self.toggle_button.toggled.connect(self._toggle)
        head.addWidget(self.toggle_button, 0, Qt.AlignTop)
        box.addLayout(head)
        self.detail = QLabel()
        self.detail.setObjectName("settingsSummaryDetail")
        self.detail.setWordWrap(True)
        self.detail.hide()
        box.addWidget(self.detail)

    def apply(self, text, detail=""):
        self.text.setText(str(text or ""))
        self.detail.setText(str(detail or ""))
        self.toggle_button.setVisible(bool(detail))
        if not detail and self.toggle_button.isChecked():
            self.toggle_button.setChecked(False)
        self.detail.setVisible(bool(detail) and self.toggle_button.isChecked())

    def _toggle(self, checked):
        self.detail.setVisible(bool(checked) and bool(self.detail.text()))
        self.toggle_button.setText("收起" if checked else "更多")
        self.toggle_button.setAccessibleName(
            "收起设置状态详情" if checked else "展开设置状态详情"
        )


class SettingsDialog(QDialog):
    def __init__(self, runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.parent_window = parent
        self.active_category = "companion"
        self.entry_rows = {}
        self.search_rows = []
        self.setWindowTitle("设置")
        self.setMinimumSize(480, 400)
        self.resize(680, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(spacing("md"))

        head = QHBoxLayout()
        title = QLabel("设置")
        title.setObjectName("heading")
        head.addWidget(title, 1)
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("ghost")
        self.close_button.setAccessibleName("关闭设置")
        self.close_button.clicked.connect(self.close)
        head.addWidget(self.close_button)
        layout.addLayout(head)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("settingsSearch")
        self.search_edit.setPlaceholderText("搜索设置（Esc 返回）")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setAccessibleName("搜索设置")
        self.search_edit.textChanged.connect(self._on_search_changed)
        self.search_edit.installEventFilter(self)
        layout.addWidget(self.search_edit)

        body = QHBoxLayout()
        body.setSpacing(10)
        nav = QFrame()
        nav.setObjectName("settingsNav")
        nav.setFixedWidth(180)
        self.nav = nav
        nav_box = QVBoxLayout(nav)
        nav_box.setContentsMargins(0, 0, 0, 0)
        nav_box.setSpacing(spacing("sm"))
        self.category_buttons = {}
        for key, label, summary, _attributes in CATEGORIES:
            item = SettingsNavItem(key, label, summary)
            item.clicked.connect(self.select_category)
            nav_box.addWidget(item)
            self.category_buttons[key] = item
        nav_box.addStretch(1)
        body.addWidget(nav)

        right = QVBoxLayout()
        right.setSpacing(spacing("md"))
        self.summary = SettingsSummary()
        right.addWidget(self.summary)
        self.stack = QStackedWidget()
        self.stack.setObjectName("settingsStack")
        self.pages = {}
        for key, _label, _summary, attributes in CATEGORIES:
            page = self._build_category_page(key, attributes)
            self.pages[key] = page
            self.stack.addWidget(page)
        self.search_page = self._build_search_page()
        self.stack.addWidget(self.search_page)
        right.addWidget(self.stack, 1)
        body.addLayout(right, 1)
        layout.addLayout(body, 1)

        footer = QHBoxLayout()
        footer.setSpacing(6)
        footer_hint = QLabel("对话与记忆只保存在本机")
        footer_hint.setObjectName("faint")
        self.theme_button = QPushButton()
        self.theme_button.setObjectName("settingsButton")
        self.theme_button.setCursor(Qt.PointingHandCursor)
        self.theme_button.setAccessibleName("切换主题")
        self.theme_button.clicked.connect(self._toggle_theme)
        self.overlay_button = QPushButton("桌面浮窗")
        self.overlay_button.setObjectName("settingsButton")
        self.overlay_button.setCheckable(True)
        self.overlay_button.setCursor(Qt.PointingHandCursor)
        self.overlay_button.setAccessibleName("显示或隐藏桌面浮窗")
        self.overlay_button.clicked.connect(self._toggle_overlay)
        footer.addWidget(footer_hint, 1)
        footer.addWidget(self.theme_button)
        footer.addWidget(self.overlay_button)
        layout.addLayout(footer)

        self.search_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self.search_shortcut.activated.connect(self.focus_search)

        self.select_category("companion")
        self.refresh()

    # ---- construction ---------------------------------------------------

    def _build_category_page(self, key, attributes):
        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("settingsPageBody")
        box = QVBoxLayout(body)
        box.setContentsMargins(0, 0, 4, 0)
        box.setSpacing(spacing("sm"))
        for attribute in attributes:
            name, detail, method = ENTRY_MAP[attribute]
            row = SettingsRow(name, detail)
            row.clicked.connect(
                lambda checked=False, target=method: self._open_panel(target)
            )
            box.addWidget(row)
            self.entry_rows[attribute] = row
            setattr(self, attribute, row)
        if key == "data":
            hint = QLabel("主题与桌面浮窗可在下方快速切换。")
            hint.setObjectName("faint")
            hint.setWordWrap(True)
            box.addWidget(hint)
            box.addSpacing(6)
            accessible = QLabel("无障碍")
            accessible.setObjectName("sectionTitle")
            box.addWidget(accessible)
            font_row = QHBoxLayout()
            font_row.setSpacing(8)
            font_label = QLabel("字体大小")
            font_label.setObjectName("settingsTitle")
            self.font_combo = QComboBox()
            self.font_combo.setObjectName("settingsField")
            self.font_combo.setAccessibleName("字体缩放")
            self.font_combo.setToolTip("按比例放大正文、界面文字和元信息")
            for percent in FONT_SCALES:
                self.font_combo.addItem("字体 {}%".format(percent), percent)
            self.font_combo.currentIndexChanged.connect(self._change_font_scale)
            font_row.addWidget(font_label)
            font_row.addWidget(self.font_combo, 1)
            box.addLayout(font_row)
            self.motion_check = QCheckBox("减少动态效果")
            self.motion_check.setObjectName("settingsCheck")
            self.motion_check.setToolTip(
                "停止流式回复时的自动跟随滚动，改为手动回到最新消息"
            )
            self.motion_check.setAccessibleName("减少动态效果")
            self.motion_check.toggled.connect(self._change_reduce_motion)
            box.addWidget(self.motion_check)
            motion_hint = QLabel(
                "开启动效减少后，流式回复不会自动滚动，"
                "“回到最新消息”按钮仍然可用。"
            )
            motion_hint.setObjectName("faint")
            motion_hint.setWordWrap(True)
            box.addWidget(motion_hint)
        box.addStretch(1)
        scroll.setWidget(body)
        return scroll

    def _build_search_page(self):
        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("settingsPageBody")
        self.search_layout = QVBoxLayout(body)
        self.search_layout.setContentsMargins(0, 0, 4, 0)
        self.search_layout.setSpacing(spacing("sm"))
        self.search_empty = QLabel("没有匹配的设置。")
        self.search_empty.setObjectName("faint")
        self.search_empty.setWordWrap(True)
        self.search_layout.addWidget(self.search_empty)
        self.search_layout.addStretch(1)
        scroll.setWidget(body)
        return scroll

    # ---- interaction ----------------------------------------------------

    def focus_search(self):
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def select_category(self, key):
        if key not in self.pages:
            return
        self.active_category = key
        if self.search_edit.text():
            self.search_edit.blockSignals(True)
            self.search_edit.clear()
            self.search_edit.blockSignals(False)
        for name, item in self.category_buttons.items():
            item.set_active(name == key)
        self.stack.setCurrentWidget(self.pages[key])
        self._refresh_summary()

    def _on_search_changed(self, text):
        query = str(text or "").strip()
        if not query:
            self.stack.setCurrentWidget(self.pages[self.active_category])
            self._refresh_summary()
            return
        count = self._rebuild_search(query)
        self.stack.setCurrentWidget(self.search_page)
        self.summary.apply(
            "找到 {} 项设置".format(count),
            "按 Esc 或清空搜索返回分类。",
        )

    def _rebuild_search(self, query):
        needle = query.lower()
        for row in self.search_rows:
            self.search_layout.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self.search_rows = []
        matches = []
        for attribute, name, detail, method in PANEL_ENTRIES:
            category = next(
                (
                    label
                    for key, label, summary, attributes in CATEGORIES
                    if attribute in attributes
                ),
                "",
            )
            haystack = " ".join((name, detail, method, category)).lower()
            if needle in haystack:
                matches.append((name, detail, method, category))
        for name, detail, action in APPEARANCE_ENTRIES:
            haystack = " ".join((name, detail, "外观", action)).lower()
            if needle in haystack:
                matches.append((name, detail, action, "数据与外观"))
        insert_at = self.search_layout.count() - 2
        for name, detail, action, category in matches:
            row = SettingsRow(name, "{} · {}".format(category, detail))
            row.clicked.connect(
                lambda checked=False, target=action: self._run_action(target)
            )
            self.search_layout.insertWidget(insert_at, row)
            insert_at += 1
            self.search_rows.append(row)
        self.search_empty.setVisible(not matches)
        return len(matches)

    def _run_action(self, action):
        if action == "theme":
            self._toggle_theme()
        elif action == "overlay":
            self._toggle_overlay()
        else:
            self._open_panel(action)

    def _open_panel(self, method_name):
        handler = getattr(self.parent_window, method_name, None)
        self.close()
        if handler is not None:
            QTimer.singleShot(0, handler)

    def _toggle_theme(self):
        self.parent_window.toggle_theme()
        self.refresh()

    def _toggle_overlay(self):
        self.parent_window.toggle_overlay()
        self.refresh()

    def _change_font_scale(self, _index):
        percent = self.font_combo.currentData()
        handler = getattr(self.parent_window, "set_font_scale", None)
        if handler is not None:
            handler(percent)
        self.refresh()

    def _change_reduce_motion(self, checked):
        handler = getattr(self.parent_window, "set_reduce_motion", None)
        if handler is not None:
            handler(bool(checked))
        self.refresh()

    def eventFilter(self, obj, event):
        if obj is self.search_edit and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key_Escape:
                if self.search_edit.text():
                    self.search_edit.clear()
                else:
                    self.search_edit.clearFocus()
                return True
        return super().eventFilter(obj, event)

    # ---- status ---------------------------------------------------------

    def refresh(self):
        if self.parent_window is None:
            return
        label = getattr(self.parent_window, "tokens", {}).get("label", "")
        self.theme_button.setText("主题：{}".format(label))
        self.theme_button.setToolTip("切换主题（当前：{}）".format(label))
        self.theme_button.setAccessibleName("切换主题，当前{}".format(label))
        visible = bool(self.parent_window.overlay_visible())
        self.overlay_button.setChecked(visible)
        self.overlay_button.setText(
            "桌面浮窗：{}".format("开" if visible else "关")
        )
        self.overlay_button.setToolTip(
            "桌面浮窗当前{}".format("显示" if visible else "隐藏")
        )
        self.overlay_button.setAccessibleName(
            "桌面浮窗，当前{}".format("显示" if visible else "隐藏")
        )
        scale = getattr(self.parent_window, "font_scale", 100)
        self.nav.setFixedWidth(int(round(180 * scale / 100.0)))
        if getattr(self, "font_combo", None) is not None:
            index = self.font_combo.findData(scale)
            if index >= 0 and index != self.font_combo.currentIndex():
                self.font_combo.blockSignals(True)
                self.font_combo.setCurrentIndex(index)
                self.font_combo.blockSignals(False)
            self.font_combo.setToolTip(
                "当前字体 {}%，放大后所有面板同步生效".format(scale)
            )
        if getattr(self, "motion_check", None) is not None:
            enabled = bool(
                getattr(self.parent_window, "reduce_motion", False)
            )
            if enabled != self.motion_check.isChecked():
                self.motion_check.blockSignals(True)
                self.motion_check.setChecked(enabled)
                self.motion_check.blockSignals(False)
        self._refresh_summary()

    def _refresh_summary(self):
        if self.stack.currentWidget() is self.search_page:
            return
        self.summary.apply(*self._summary_for(self.active_category))

    def _summary_for(self, key):
        if key == "companion":
            return self._companion_summary()
        if key == "work":
            return self._work_summary()
        return self._data_summary()

    def _companion_summary(self):
        name = "数字分身"
        confirmed = 0
        candidates = 0
        protected = 0
        user_mood = "未知"
        agent_mood = ""
        try:
            name = self.runtime.persona_info().get("name") or name
        except Exception:
            pass
        try:
            records = self.runtime.memories()
            confirmed = sum(
                1
                for record in records
                if getattr(record, "status", "confirmed") == "confirmed"
            )
            candidates = sum(
                1
                for record in records
                if getattr(record, "status", "") == "candidate"
            )
            protected = sum(
                1 for record in records if getattr(record, "protected", False)
            )
        except Exception:
            pass
        try:
            snapshot = self.runtime.affect_snapshot()
            user_mood = (snapshot.get("user") or {}).get("mood_label") or "未知"
            agent_mood = (snapshot.get("agent") or {}).get("mood_label") or ""
        except Exception:
            pass
        main = "{} · {} 条记忆 · 用户情绪 {}".format(name, confirmed, user_mood)
        detail = "待确认 {} 条 · 受保护 {} 条".format(candidates, protected)
        if agent_mood:
            detail += " · 角色状态 {}".format(agent_mood)
        return main, detail

    def _work_summary(self):
        state_label = "空闲"
        title = ""
        try:
            status = self.runtime.work_status()
            state_label = status.get("state_label") or state_label
            task = status.get("task")
            if task is not None:
                title = _clip(getattr(task.card, "title", ""), 24)
        except Exception:
            pass
        enabled = False
        daily = 0
        limit = 0
        unread = 0
        next_due = ""
        try:
            proactive = self.runtime.proactive_status()
            enabled = bool(proactive.get("enabled"))
            daily = int(proactive.get("daily_count") or 0)
            limit = int(proactive.get("daily_limit") or 0)
            unread = int(proactive.get("unread") or 0)
            due = proactive.get("next_due") or {}
            next_due = str(due.get("display") or "")
        except Exception:
            pass
        main = "任务：{}".format(state_label)
        if title:
            main += " · {}".format(title)
        main += " · 主动关心{}".format("开启" if enabled else "关闭")
        detail = "今日主动 {} / {} 次 · 未读 {} 条".format(daily, limit, unread)
        if next_due:
            detail += " · 下次提醒 {}".format(next_due)
        return main, detail

    def _data_summary(self):
        label = getattr(self.parent_window, "tokens", {}).get("label", "")
        visible = bool(self.parent_window.overlay_visible())
        main = "主题：{} · 浮窗：{}".format(
            label, "显示中" if visible else "已隐藏"
        )
        size = ""
        try:
            path = Path(self.runtime.store.database_path)
            if path.exists():
                size = " · 数据库 {:.1f} MB".format(
                    path.stat().st_size / (1024 * 1024)
                )
        except Exception:
            pass
        return main, "对话与记忆只保存在本机{}".format(size)
