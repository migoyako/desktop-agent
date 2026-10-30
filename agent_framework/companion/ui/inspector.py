"""Tabbed, read-only context drawer rendered from the application projection."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .chat_view import WrapLabel
from ..app.projection import build_inspector_sections


TAB_LAYOUT = (
    ("execute", "执行", ("当前状态", "待确认提案", "最近回执", "详情")),
    ("files", "文件", ("文件",)),
    ("sources", "来源", ("来源",)),
    ("context", "上下文", ("上下文", "技能", "主动陪伴")),
    ("tasks", "任务", ("工作任务",)),
)
TAB_EMPTY = {
    "execute": "暂无执行状态",
    "files": "还没有文件变更",
    "sources": "还没有检索来源",
    "context": "还没有上下文信息",
    "tasks": "还没有工作任务",
}
_TAB_BY_TITLE = {
    title: key for key, _label, titles in TAB_LAYOUT for title in titles
}


class InspectorPanel(QFrame):
    close_requested = Signal()

    def __init__(self, provider, parent=None):
        super().__init__(parent)
        self.provider = provider
        self._signature = None
        self._sections = []
        self._widgets = []
        self._columns = {}
        self._tabs = {}
        self.setObjectName("inspector")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.setMinimumWidth(220)
        self.setMaximumWidth(420)
        self.resize(268, self.height())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 8, 10)
        layout.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(4)
        title = QLabel("上下文")
        title.setObjectName("heading")
        head.addWidget(title, 1)
        self.close_button = QPushButton("×")
        self.close_button.setObjectName("inspectorClose")
        self.close_button.setToolTip("收起上下文抽屉")
        self.close_button.setAccessibleName("收起上下文抽屉")
        self.close_button.setMinimumSize(22, 22)
        self.close_button.clicked.connect(self.close_requested.emit)
        head.addWidget(self.close_button)
        layout.addLayout(head)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("inspectorTabs")
        self.tabs.setDocumentMode(True)
        layout.addWidget(self.tabs, 1)
        for key, label, _titles in TAB_LAYOUT:
            area = QScrollArea(self)
            area.setObjectName("inspectorArea")
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.NoFrame)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            container = QWidget()
            container.setObjectName("inspectorList")
            column = QVBoxLayout(container)
            column.setContentsMargins(0, 4, 4, 0)
            column.setSpacing(6)
            column.addStretch(1)
            area.setWidget(container)
            self.tabs.addTab(area, label)
            self._columns[key] = column
            self._tabs[key] = area
        self.tabs.currentChanged.connect(lambda _index: self._apply_tab_widths())

    def build_sections(self):
        projection = getattr(self.provider, "ui_projection", None)
        try:
            if callable(projection):
                data = projection()
                return [
                    (section.title, list(section.lines))
                    for section in data.inspector
                ]
            return [
                (section.title, list(section.lines))
                for section in build_inspector_sections(self.provider)
            ]
        except Exception:
            return [("详情", ["暂时读不到"])]

    def _clear(self):
        for widget in self._widgets:
            widget.setParent(None)
            widget.deleteLater()
        self._widgets = []

    def refresh(self):
        sections = self.build_sections()
        signature = tuple((title, tuple(lines)) for title, lines in sections)
        if signature != self._signature:
            self._signature = signature
            self._sections = sections
            self._rebuild(sections)
        return sections

    def _rebuild(self, sections):
        self._clear()
        grouped = {key: [] for key, _label, _titles in TAB_LAYOUT}
        for title, lines in sections:
            key = _TAB_BY_TITLE.get(title, "execute")
            grouped.setdefault(key, []).append((title, lines))
        for key, _label, _titles in TAB_LAYOUT:
            column = self._columns[key]
            entries = grouped.get(key) or []
            if not entries:
                empty = WrapLabel(TAB_EMPTY.get(key, "暂无内容"))
                empty.setObjectName("muted")
                column.insertWidget(column.count() - 1, empty)
                self._widgets.append(empty)
                continue
            for title, lines in entries:
                card = self._build_card(title, lines)
                column.insertWidget(column.count() - 1, card)
                self._widgets.append(card)
        self._apply_tab_widths()

    @staticmethod
    def _build_card(title, lines):
        card = QFrame()
        card.setObjectName("section")
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        box = QVBoxLayout(card)
        box.setContentsMargins(9, 7, 9, 8)
        box.setSpacing(2)
        head = QLabel(title)
        head.setObjectName("sectionTitle")
        box.addWidget(head)
        for index, line in enumerate(lines):
            label = WrapLabel(line)
            if index == 0:
                label.setObjectName("accentText")
            box.addWidget(label)
        return card

    def _apply_tab_widths(self):
        for label in self.findChildren(WrapLabel):
            label.sync_height()

    def focus_tab(self, key):
        key = str(key or "")
        if key not in self._tabs:
            return False
        self.tabs.setCurrentIndex(self.tabs.indexOf(self._tabs[key]))
        return True

    def active_tab(self):
        index = self.tabs.currentIndex()
        for key, _label, _titles in TAB_LAYOUT:
            if self.tabs.indexOf(self._tabs[key]) == index:
                return key
        return ""

    def snapshot(self):
        return [
            (title, list(lines))
            for title, lines in (self._sections or self.build_sections())
        ]
