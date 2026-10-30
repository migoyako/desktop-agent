"""Scrollable chat transcript: bubbles, tool cards, dates and streaming tail."""

from __future__ import annotations

import math
from datetime import datetime

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import markdown
from .theme import FONT_SCALES, TYPE_TOKENS, spacing


STREAM_CURSOR = "▍"
EMPTY_COPY = {
    "chat": {
        "title": "今天想聊点什么？",
        "body": "{name} 在听。说说今天的计划、心情，或者只是打个招呼。",
        "prompts": ("整理今天的想法", "陪我复盘一下", "随便聊聊"),
    },
    "work": {
        "title": "把一件事交给我",
        "body": "描述目标和资料位置，我会先给出计划；写入前一定等你确认。",
        "prompts": ("写一份周报", "搜索资料并保存笔记", "梳理项目待办"),
    },
}
TOOL_STATE_LABELS = {
    "running": "执行中",
    "done": "已完成",
    "error": "失败",
}


class WrapLabel(QLabel):
    """Word-wrapped label that keeps its own height in sync with its width."""

    def __init__(self, text="", parent=None, object_name=None, muted=False):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setTextFormat(Qt.PlainText)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        if object_name:
            self.setObjectName(object_name)
        if muted:
            self.setObjectName("bubbleMeta")

    def setText(self, text):
        super().setText(text)
        self.sync_height()

    def sync_height(self):
        width = self.width()
        if width <= 0:
            return
        # heightForWidth() keeps reporting the previous fixed height, so the
        # wrapped height is measured from the font metrics instead.
        metrics = self.fontMetrics()
        total = 0
        for line in str(self.text() or "").split("\n"):
            rect = metrics.boundingRect(0, 0, width, 100000, Qt.TextWordWrap, line)
            total += max(rect.height(), metrics.lineSpacing())
        needed = max(total, metrics.lineSpacing())
        if self.minimumHeight() != needed or self.maximumHeight() != needed:
            self.setFixedHeight(needed)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.sync_height()

    def natural_width(self):
        metrics = self.fontMetrics()
        lines = str(self.text() or "").split("\n")
        return max((metrics.horizontalAdvance(line) for line in lines), default=0)


class MarkdownLabel(QLabel):
    """Rich-text label for user/model Markdown that never executes raw HTML."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._source = ""
        self._applying = False
        self.setWordWrap(True)
        self.setTextFormat(Qt.RichText)
        self.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse
        )
        self.setOpenExternalLinks(False)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.set_source(text)

    def source_text(self):
        return self._source

    def _font_scale(self):
        """Snap the QSS body size back to the nearest preset font scale."""
        pixel = self.font().pixelSize()
        if pixel <= 0:
            return 100
        ratio = pixel * 100.0 / TYPE_TOKENS["body"]
        return min(FONT_SCALES, key=lambda value: abs(value - ratio))

    def _apply(self):
        self._applying = True
        try:
            super().setText(markdown.document(self._source, self._font_scale()))
        finally:
            self._applying = False
        self.sync_height()

    def set_source(self, text):
        self._source = str(text or "")
        self._apply()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.FontChange and not self._applying and self._source:
            self._apply()

    def sync_height(self):
        width = self.width()
        if width <= 0:
            return
        document = QTextDocument()
        document.setDefaultFont(self.font())
        document.setDocumentMargin(0)
        document.setTextWidth(max(24, width - 2))
        document.setHtml(self.text())
        needed = max(
            int(math.ceil(document.size().height())),
            self.fontMetrics().lineSpacing(),
        )
        if self.minimumHeight() != needed or self.maximumHeight() != needed:
            self.setFixedHeight(needed)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.sync_height()

    def natural_width(self):
        metrics = self.fontMetrics()
        lines = str(self._source or "").split("\n")
        return max((metrics.horizontalAdvance(line) for line in lines), default=0)


class DateDivider(QLabel):
    def __init__(self, label, parent=None):
        super().__init__(label, parent)
        self.setObjectName("dateDivider")
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)


def _block_signature(block):
    arguments = block.get("arguments") or {}
    if isinstance(arguments, dict):
        arguments = tuple(
            sorted((str(key), str(value)) for key, value in arguments.items())
        )
    sources = tuple(
        (str(item.get("title") or ""), str(item.get("url") or ""))
        for item in (block.get("sources") or ())
        if isinstance(item, dict)
    )
    return (
        block.get("kind"),
        block.get("id"),
        block.get("state"),
        block.get("date"),
        block.get("text"),
        block.get("summary"),
        block.get("name"),
        arguments,
        sources,
        block.get("error"),
        block.get("preview"),
        block.get("result_id"),
        block.get("result_chars"),
    )


def _frame_natural_width(frame):
    """Width the frame would need to show every line without wrapping."""
    widest = 0
    for label in frame.findChildren(QLabel):
        if isinstance(label, MarkdownLabel):
            source = label.source_text()
        elif isinstance(label, WrapLabel):
            source = label.text()
        else:
            continue
        if not source:
            continue
        widest = max(widest, label.natural_width())
    margins = frame.layout().contentsMargins()
    return widest + margins.left() + margins.right() + 62


class MessageBubble(QFrame):
    def __init__(self, block, parent=None):
        super().__init__(parent)
        self.role = "user" if block.get("role") == "user" else "assistant"
        self.raw_text = str(block.get("text") or "")
        self.setObjectName("bubbleUser" if self.role == "user" else "bubbleAgent")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 8)
        layout.setSpacing(3)

        header = QHBoxLayout()
        header.setSpacing(6)
        self.role_label = QLabel(
            str(block.get("name") or ("你" if self.role == "user" else "助手"))
        )
        self.role_label.setObjectName("bubbleRole")
        header.addWidget(self.role_label)
        meta = " · ".join(
            str(item)
            for item in (block.get("time"),)
            if item
        )
        self.meta_label = QLabel(meta)
        self.meta_label.setObjectName("bubbleMeta")
        self.meta_label.setVisible(bool(meta))
        header.addWidget(self.meta_label)
        header.addStretch(1)
        self.copy_button = QPushButton("复制")
        self.copy_button.setObjectName("bubbleAction")
        self.copy_button.setMinimumHeight(20)
        self.copy_button.setCursor(Qt.PointingHandCursor)
        self.copy_button.setToolTip("复制这条消息的原文")
        self.copy_button.setAccessibleName("复制这条消息的原文")
        self.copy_button.clicked.connect(self.copy)
        header.addWidget(self.copy_button)
        layout.addLayout(header)

        self.body = MarkdownLabel(self.raw_text)
        self.body.setObjectName("bubbleBody")
        layout.addWidget(self.body)

    def set_text(self, text):
        self.raw_text = str(text or "")
        self.body.set_source(self.raw_text)

    def copy(self):
        QApplication.clipboard().setText(self.raw_text)

    def natural_width(self):
        return _frame_natural_width(self)


class ToolCard(QFrame):
    """Receipt card: collapsed shows outcome; expanded shows arguments/results."""

    toggled = Signal(bool)

    def __init__(self, block, parent=None, expanded=False):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self._block = {}
        self._expanded = bool(expanded)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 5, 9, 6)
        layout.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(4)
        self.title = WrapLabel()
        self.detail = WrapLabel()
        head.addWidget(self.title, 1)
        self.toggle_button = QPushButton("详情")
        self.toggle_button.setObjectName("toolToggle")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setCursor(Qt.PointingHandCursor)
        self.toggle_button.setMinimumHeight(20)
        self.toggle_button.setToolTip("展开或收起工具回执详情")
        self.toggle_button.setAccessibleName("展开或收起工具回执详情")
        self.toggle_button.clicked.connect(self._toggle)
        head.addWidget(self.toggle_button, 0, Qt.AlignTop)
        layout.addLayout(head)
        layout.addWidget(self.detail)

        self.extra = QWidget()
        extra_layout = QVBoxLayout(self.extra)
        extra_layout.setContentsMargins(0, 3, 0, 0)
        extra_layout.setSpacing(3)
        self.args_label = WrapLabel()
        self.args_label.setObjectName("toolDetail")
        self.error_label = WrapLabel()
        self.error_label.setObjectName("toolError")
        self.preview_label = WrapLabel()
        self.preview_label.setObjectName("toolDetail")
        self.sources_label = WrapLabel()
        self.sources_label.setObjectName("toolSource")
        self.handle_label = WrapLabel()
        self.handle_label.setObjectName("toolHandle")
        for label in (
            self.args_label,
            self.error_label,
            self.preview_label,
            self.sources_label,
            self.handle_label,
        ):
            extra_layout.addWidget(label)
        layout.addWidget(self.extra)
        self.apply(block)

    def _toggle(self):
        self.set_expanded(not self._expanded)
        self.toggled.emit(self._expanded)

    def set_expanded(self, value):
        self._expanded = bool(value)
        self.toggle_button.setChecked(self._expanded)
        self.toggle_button.setText("收起" if self._expanded else "详情")
        self.extra.setVisible(self._expanded and self._has_detail())
        self._sync_height()

    def _has_detail(self):
        block = self._block
        return bool(
            block.get("arguments")
            or block.get("error")
            or block.get("preview")
            or block.get("sources")
            or block.get("result_id")
        )

    def apply(self, block):
        self._block = dict(block or {})
        state = str(block.get("state") or "running")
        suffix = TOOL_STATE_LABELS.get(state, state)
        name = str(block.get("name") or "工具")
        self.setObjectName(
            "toolCardFailed"
            if state == "error"
            else "toolCardDone"
            if state == "done"
            else "toolCard"
        )
        self.title.setText("{} · {}".format(name, suffix))
        summary = " ".join(str(block.get("summary") or "").split())[:160]
        self.detail.setText(summary)
        self.detail.setVisible(bool(summary))

        arguments = block.get("arguments") or {}
        if isinstance(arguments, dict):
            args_text = "\n".join(
                "{}：{}".format(key, value)
                for key, value in arguments.items()
                if str(value).strip()
            )
        else:
            args_text = str(arguments)
        self.args_label.setText(
            "参数\n{}".format(args_text) if args_text else ""
        )
        self.args_label.setVisible(bool(args_text))

        error = str(block.get("error") or "").strip()
        self.error_label.setText(
            "错误{}：{}".format(
                "（{}）".format(block.get("code"))
                if block.get("code")
                else "",
                error,
            )
            if error
            else ""
        )
        self.error_label.setVisible(bool(error))

        preview = str(block.get("preview") or "").strip()
        self.preview_label.setText(
            "结果\n{}".format(preview) if preview else ""
        )
        self.preview_label.setVisible(bool(preview))

        sources = block.get("sources") or []
        source_lines = []
        for index, item in enumerate(sources[:5], start=1):
            if not isinstance(item, dict):
                continue
            source_lines.append(
                "{}. {}".format(index, item.get("title") or item.get("url") or "")
            )
            if item.get("url"):
                source_lines.append("   {}".format(item["url"]))
        self.sources_label.setText(
            "来源\n{}".format("\n".join(source_lines)) if source_lines else ""
        )
        self.sources_label.setVisible(bool(source_lines))

        handle = str(block.get("result_id") or "")
        chars = int(block.get("result_chars") or 0)
        self.handle_label.setText(
            "完整结果：{}（{} 字符）".format(handle, chars)
            if handle
            else ""
        )
        self.handle_label.setVisible(bool(handle))

        self.toggle_button.setVisible(self._has_detail() or self._expanded)
        self.set_expanded(self._expanded)
        self.style().unpolish(self)
        self.style().polish(self)

    def _sync_height(self):
        for label in (
            self.title,
            self.detail,
            self.args_label,
            self.error_label,
            self.preview_label,
            self.sources_label,
            self.handle_label,
        ):
            label.sync_height()
        self.updateGeometry()

    @property
    def expanded(self):
        return self._expanded

    def natural_width(self):
        widgets = [self.title, self.detail]
        if self._expanded:
            widgets.extend(
                [
                    self.args_label,
                    self.error_label,
                    self.preview_label,
                    self.sources_label,
                    self.handle_label,
                ]
            )
        metrics = self.fontMetrics()
        widest = 0
        for label in widgets:
            if not label.isVisible() and label is not self.title:
                continue
            widest = max(widest, label.natural_width())
        margins = self.layout().contentsMargins()
        return widest + margins.left() + margins.right() + 62


class EmptyState(QFrame):
    """Mode-aware welcome card with one-click opening prompts."""

    quick_start = Signal(str)

    def __init__(self, mode, assistant_name, recent_task="", parent=None):
        super().__init__(parent)
        mode = mode if mode in EMPTY_COPY else "chat"
        copy = EMPTY_COPY[mode]
        self.setObjectName("emptyState")
        self.setProperty("mode", mode)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 16, 18, 16)
        box.setSpacing(spacing("md"))

        self.title_label = WrapLabel(copy["title"])
        self.title_label.setObjectName("emptyTitle")
        box.addWidget(self.title_label)
        self.body_label = WrapLabel(
            copy["body"].format(name=assistant_name or "助手")
        )
        self.body_label.setObjectName("emptyBody")
        box.addWidget(self.body_label)

        prompts = QHBoxLayout()
        prompts.setSpacing(6)
        self.action_buttons = []
        for prompt in copy["prompts"]:
            button = QPushButton(prompt)
            button.setObjectName("emptyAction")
            button.setCursor(Qt.PointingHandCursor)
            button.setAccessibleName("快捷开场：{}".format(prompt))
            button.clicked.connect(
                lambda checked=False, text=prompt: self.quick_start.emit(text)
            )
            prompts.addWidget(button)
            self.action_buttons.append(button)
        prompts.addStretch(1)
        box.addLayout(prompts)

        self.recent_label = WrapLabel()
        self.recent_label.setObjectName("emptyRecent")
        if recent_task:
            self.recent_label.setText(recent_task)
        self.recent_label.setVisible(bool(recent_task))
        box.addWidget(self.recent_label)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

    def natural_width(self):
        widest = max(
            self.title_label.natural_width(),
            self.body_label.natural_width(),
            self.recent_label.natural_width() if self.recent_label.isVisible() else 0,
        )
        buttons = sum(button.sizeHint().width() for button in self.action_buttons)
        buttons += 6 * max(0, len(self.action_buttons) - 1)
        return max(widest + 36, buttons + 36, 320)


class ChatView(QScrollArea):
    """Renders transcript blocks; only the streaming bubble changes per delta."""

    link_activated = Signal(str)
    quick_start = Signal(str)

    def __init__(self, parent=None, assistant_name="助手"):
        super().__init__(parent)
        self.setObjectName("chat")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)
        self._signature = None
        self._rows = []
        self._stick = True
        self._assistant_name = assistant_name
        self._tool_expanded = set()
        self._approval_card = None
        self._empty_mode = "chat"
        self._empty_recent = ""
        self._reduce_motion = False

        container = QWidget()
        container.setObjectName("chatList")
        self.column = QVBoxLayout(container)
        self.column.setContentsMargins(14, 12, 14, 12)
        self.column.setSpacing(8)
        self.setWidget(container)
        self.column.addStretch(1)

        self.stream_bubble = MessageBubble(
            {
                "kind": "message",
                "role": "assistant",
                "name": assistant_name,
            }
        )
        self.stream_bubble.body.linkActivated.connect(self.link_activated.emit)
        self.stream_bubble.hide()
        self.verticalScrollBar().valueChanged.connect(self._track_scroll)
        # Wrapped heights are known only after the next layout pass, so the
        # scrollbar range grows after the delta that caused it.
        self.verticalScrollBar().rangeChanged.connect(self._on_range_changed)

        self.jump_button = QPushButton("回到最新消息", self.viewport())
        self.jump_button.setObjectName("jumpLatest")
        self.jump_button.setCursor(Qt.PointingHandCursor)
        self.jump_button.setToolTip("滚动到最新消息")
        self.jump_button.setAccessibleName("滚动到最新消息")
        self.jump_button.clicked.connect(self.scroll_to_latest)
        self.jump_button.hide()

    def set_assistant_name(self, name):
        self._assistant_name = str(name or "助手")
        self.stream_bubble.role_label.setText(self._assistant_name)

    def set_empty_context(self, mode, recent_task=""):
        mode = str(getattr(mode, "value", mode) or "chat")
        recent_task = str(recent_task or "")
        if (mode, recent_task) == (self._empty_mode, self._empty_recent):
            return
        self._empty_mode = mode if mode in EMPTY_COPY else "chat"
        self._empty_recent = recent_task
        self._signature = None

    def set_reduce_motion(self, enabled):
        self._reduce_motion = bool(enabled)
        if self._reduce_motion and self.stream_bubble.isVisible():
            self._stick = False

    def _track_scroll(self, value):
        bar = self.verticalScrollBar()
        self._stick = value >= bar.maximum() - 4
        if self._stick:
            self.jump_button.hide()
        elif not self.jump_button.isVisible():
            self.jump_button.setText("回到最新消息")
            self.jump_button.show()
        self._place_jump_button()

    def _scroll_bottom(self):
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_range_changed(self, _minimum, maximum):
        if self._stick:
            self.verticalScrollBar().setValue(maximum)

    def _scroll_bottom_after_layout(self):
        # Bubble heights are recomputed during the next layout pass, so the
        # scrollbar maximum is still stale right now.
        QTimer.singleShot(0, self._scroll_if_sticky)

    def _scroll_if_sticky(self):
        if self._stick:
            self._scroll_bottom()

    def scroll_to_latest(self):
        self._stick = True
        self.jump_button.hide()
        self._scroll_bottom()
        self._scroll_bottom_after_layout()

    def _place_jump_button(self):
        if not self.jump_button.isVisible():
            return
        margin = 12
        self.jump_button.adjustSize()
        x = max(
            margin,
            self.viewport().width() - self.jump_button.width() - margin,
        )
        y = max(
            margin,
            self.viewport().height() - self.jump_button.height() - margin,
        )
        self.jump_button.move(x, y)

    def _clear_rows(self):
        for widget in self._rows:
            self.column.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._rows = []

    def _append_row(self, widget, align):
        self.column.insertWidget(self.column.count() - 1, widget, 0, align)
        self._rows.append(widget)

    def _remember_tool(self, key, expanded):
        if expanded:
            self._tool_expanded.add(key)
        else:
            self._tool_expanded.discard(key)
        self._apply_widths()
        QTimer.singleShot(0, self._scroll_if_sticky)

    def _build_bubble(self, block):
        role = block.get("role")
        fallback = "你" if role == "user" else self._assistant_name
        bubble = MessageBubble(dict(block, name=block.get("name") or fallback))
        bubble.body.linkActivated.connect(self.link_activated.emit)
        return bubble

    def render(self, blocks, stream_text="", follow=False):
        blocks = list(blocks or [])
        signature = tuple(_block_signature(block) for block in blocks)
        if not blocks:
            signature = ("empty", self._empty_mode, self._empty_recent)
        rebuilt = signature != self._signature
        if rebuilt:
            self._signature = signature
            self._clear_rows()
            if not blocks:
                empty = EmptyState(
                    self._empty_mode,
                    self._assistant_name,
                    self._empty_recent,
                )
                empty.quick_start.connect(self.quick_start.emit)
                self._append_row(empty, Qt.AlignLeft)
            last_day = ""
            for block in blocks:
                if block.get("kind") == "tool":
                    key = str(block.get("id") or "")
                    card = ToolCard(
                        block,
                        expanded=key in self._tool_expanded,
                    )
                    card.toggled.connect(
                        lambda checked, value=key: self._remember_tool(
                            value, checked
                        )
                    )
                    self._append_row(card, Qt.AlignLeft)
                    continue
                day = str(block.get("date") or "")
                if day and day != last_day:
                    last_day = day
                    self._append_row(DateDivider(day), Qt.AlignHCenter)
                bubble = self._build_bubble(block)
                align = (
                    Qt.AlignRight
                    if bubble.role == "user"
                    else Qt.AlignLeft
                )
                self._append_row(bubble, align)

        self.column.removeWidget(self.stream_bubble)
        if self._approval_card is not None:
            self.column.removeWidget(self._approval_card)
        self.column.insertWidget(
            self.column.count() - 1,
            self.stream_bubble,
            0,
            Qt.AlignLeft,
        )
        if self._approval_card is not None:
            self.column.insertWidget(
                self.column.count() - 1,
                self._approval_card,
                0,
                Qt.AlignLeft,
            )
        self.stream_bubble.setVisible(bool(stream_text))
        if stream_text:
            self.stream_bubble.set_text(stream_text + STREAM_CURSOR)
        self._apply_widths()
        if follow and not (self._reduce_motion and stream_text):
            self._stick = True
        if self._reduce_motion and stream_text:
            self._stick = False
        if self._stick:
            self.jump_button.hide()
            self._scroll_bottom()
            self._scroll_bottom_after_layout()
        elif rebuilt or stream_text:
            self.jump_button.setText(
                "有新内容 · 回到最新" if stream_text else "回到最新消息"
            )
            self.jump_button.show()
            self._place_jump_button()

    def attach_approval_card(self, card):
        self._approval_card = card
        self.column.insertWidget(self.column.count() - 1, card, 0, Qt.AlignLeft)
        card.hide()

    def _apply_widths(self):
        limit = max(240, min(760, int(self.viewport().width() * 0.82)))
        if self._approval_card is not None:
            self._approval_card.setFixedWidth(limit)
        for widget in self._rows + [self.stream_bubble]:
            if isinstance(widget, DateDivider):
                continue
            natural = widget.natural_width()
            if natural <= 0:
                widget.setMaximumWidth(limit)
                continue
            widget.setFixedWidth(min(limit, max(180, natural)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_widths()
        self._place_jump_button()

    def plain_text(self):
        pieces = []
        for widget in self._rows:
            if isinstance(widget, MessageBubble):
                pieces.append(widget.body.source_text())
            elif isinstance(widget, ToolCard):
                pieces.append(widget.title.text())
            elif isinstance(widget, WrapLabel):
                pieces.append(widget.text())
            elif isinstance(widget, EmptyState):
                pieces.append(widget.title_label.text())
        if self.stream_bubble.isVisible():
            pieces.append(self.stream_bubble.body.source_text())
        return "\n".join(pieces)

    def bubble_count(self):
        return sum(1 for widget in self._rows if isinstance(widget, MessageBubble))

    def tool_card_count(self):
        return sum(1 for widget in self._rows if isinstance(widget, ToolCard))

    def date_divider_count(self):
        return sum(1 for widget in self._rows if isinstance(widget, DateDivider))
