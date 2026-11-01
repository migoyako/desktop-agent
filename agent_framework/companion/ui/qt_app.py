"""PySide6 desktop companion window."""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QPoint,
    QSize,
    QThread,
    QTimer,
    QUrl,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QDesktopServices,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..app import (
    CompanionApplicationService,
    SessionBusyError,
    create_application_service,
)
from ..models import (
    AgentMode,
    ProposalStatus,
    WORK_PLAN_DISCUSSING,
    WORK_PLAN_REVIEW,
)
from ..policy import mode_label
from ..proactive import status_label
from ..work import FORWARD_LABELS, STATE_LABELS
from .approval_card import ApprovalCard
from .chat_view import ChatView
from .inspector import InspectorPanel
from .markdown import safe_url
from .run_bar import RunBar
from .settings import SettingsDialog
from .state import UiStateStore, clamp_position
from .stream import StreamWorker
from .timefmt import clock as _clock
from .theme import (
    font_px,
    normalize_font_scale,
    normalize_theme,
    stylesheet,
    theme,
)
from .tray import TrayController, app_icon
from .console import ManagementConsoleDialog


WINDOW_WIDTH = 1000
WINDOW_HEIGHT = 700
MIN_WIDTH = 780
MIN_HEIGHT = 560
RAIL_WIDTH = 208
INSPECTOR_COLLAPSE_WIDTH = 940
RAIL_COLLAPSE_WIDTH = 860
INPUT_MIN_LINES = 2
INPUT_MAX_LINES = 5
INPUT_WARN_CHARS = 4000
INPUT_MAX_CHARS = 8000
DRAFT_SAVE_DELAY_MS = 400


def _day_stamp(value):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return text[:10]
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    day = moment.strftime("%Y-%m-%d")
    return "今天" if day == datetime.now().strftime("%Y-%m-%d") else day


def _relative_time(value):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return text[:16].replace("T", " ")
    now = datetime.now(moment.tzinfo) if moment.tzinfo else datetime.now()
    seconds = max(0.0, (now - moment).total_seconds())
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return "{} 分钟前".format(int(seconds // 60))
    if seconds < 86400:
        return "{} 小时前".format(int(seconds // 3600))
    days = int(seconds // 86400)
    if days == 1:
        return "昨天"
    if days < 7:
        return "{} 天前".format(days)
    return moment.strftime("%Y-%m-%d")


def _session_group(value):
    text = str(value or "").strip()
    if not text:
        return "更早"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return "更早"
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    days = (datetime.now(moment.tzinfo) - moment).days
    if days <= 0:
        return "今天"
    if days == 1:
        return "昨天"
    if days < 7:
        return "最近 7 天"
    return "更早"


def _clip_text(value, limit=60):
    flat = " ".join(str(value or "").split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


def rail_icon(kind, tokens, size=16):
    """Paint the small monochrome glyphs used by the navigation rail."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    color = QColor(tokens.get("muted") or "#8e8e93")
    pen = QPen(color, 1.4)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    center = size / 2.0
    if kind == "new":
        painter.drawEllipse(1.75, 1.75, size - 3.5, size - 3.5)
        painter.drawLine(center, 4.5, center, size - 4.5)
        painter.drawLine(4.5, center, size - 4.5, center)
    elif kind == "search":
        painter.drawEllipse(2.0, 2.0, size - 7.0, size - 7.0)
        painter.drawLine(size - 5.4, size - 5.4, size - 2.0, size - 2.0)
    elif kind == "folder":
        painter.drawRoundedRect(1.5, 4.0, size - 3.0, size - 6.0, 2.0, 2.0)
        painter.drawLine(2.5, 4.0, 6.0, 4.0)
    elif kind == "more":
        painter.setPen(QPen(color, 2.0, Qt.SolidLine, Qt.RoundCap))
        for step in (4, int(center), size - 4):
            painter.drawPoint(int(step), int(center))
    elif kind == "plus":
        painter.drawLine(center, 3.5, center, size - 3.5)
        painter.drawLine(3.5, center, size - 3.5, center)
    elif kind == "settings":
        for offset, knob in ((4.5, 10.5), (8.0, 6.0), (11.5, 9.0)):
            painter.drawLine(2.0, offset, size - 2.0, offset)
            painter.setBrush(color)
            painter.drawEllipse(knob, offset - 1.2, 2.4, 2.4)
            painter.setBrush(Qt.NoBrush)
    painter.end()
    return pixmap


class ElidedLabel(QLabel):
    """Single-line label that keeps the full text and elides it on resize."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full_text = ""
        self.set_full_text(text)

    def full_text(self):
        return self._full_text

    def set_full_text(self, text):
        self._full_text = str(text or "")
        self.setToolTip(self._full_text)
        self._apply_elision()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_elision()

    def _apply_elision(self):
        width = self.width() - 2
        if width <= 0:
            super().setText(self._full_text)
            return
        super().setText(
            self.fontMetrics().elidedText(
                self._full_text,
                Qt.ElideRight,
                width,
            )
        )


class SessionRow(QFrame):
    clicked = Signal(str)
    pin_toggled = Signal(str, bool)
    unread_toggled = Signal(str, bool)
    rename_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, summary, active=False, parent=None):
        super().__init__(parent)
        self.summary = dict(summary or {})
        self.session_id = self.summary.get("id") or ""
        self.pinned = bool(self.summary.get("pinned_at"))
        self.unread = bool(int(self.summary.get("unread") or 0))
        self.active = bool(active)
        self.setObjectName("sessionActive" if active else "sessionRow")
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setContextMenuPolicy(Qt.DefaultContextMenu)
        self.title = ElidedLabel(self._title())
        self.title.setObjectName("sessionLabel")
        self.title.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.search_blob = " ".join(
            str(self.summary.get(key) or "")
            for key in ("id", "title", "topic", "last_content")
        ).lower()
        self.setToolTip(self._tooltip())
        self.setAccessibleName(self._accessible_name())

        box = QVBoxLayout(self)
        box.setContentsMargins(10, 5, 4, 6)
        box.setSpacing(1)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(2)
        self.more_button = QPushButton("⋯")
        self.more_button.setObjectName("sessionMore")
        self.more_button.setMinimumSize(20, 18)
        self.more_button.setCursor(Qt.PointingHandCursor)
        self.more_button.setFocusPolicy(Qt.TabFocus)
        self.more_button.setToolTip("管理会话")
        self.more_button.setAccessibleName("管理会话 {}".format(self._title()))
        self.more_button.clicked.connect(self._show_menu)
        top.addWidget(self.title, 1)
        top.addWidget(self.more_button, 0)
        box.addLayout(top)
        self.meta = QLabel(self._meta())
        self.meta.setObjectName("sessionMeta")
        self.meta.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        box.addWidget(self.meta)

    def _title(self):
        custom = " ".join(str(self.summary.get("title") or "").split())
        if custom:
            return custom
        topic = " ".join(str(self.summary.get("topic") or "").split())
        return topic or "新的对话"

    def _meta(self):
        stamp = self.summary.get("last_at") or self.summary.get("updated_at")
        parts = []
        if self.pinned:
            parts.append("置顶")
        relative = _relative_time(stamp)
        if relative:
            parts.append(relative)
        if self.unread:
            parts.append("未读")
        if self.active:
            parts.append("当前")
        return " · ".join(parts) or "还没有消息"

    def _accessible_name(self):
        return "{}，{}".format(self._title(), self._meta())

    def _tooltip(self):
        count = int(self.summary.get("message_count") or 0)
        stamp = _clock(self.summary.get("last_at"))
        meta = "{} 条消息".format(count) if count else "还没有消息"
        if stamp:
            meta += " · 最后 {}".format(stamp)
        state = []
        if self.pinned:
            state.append("已置顶")
        if self.unread:
            state.append("未读")
        if self.active:
            state.append("当前会话")
        if state:
            meta += " · " + " · ".join(state)
        preview = _clip_text(self.summary.get("last_content"), 80)
        lines = [self._title(), meta]
        if preview:
            lines.append(preview)
        lines.append("右键或点 ⋯ 管理会话")
        return "\n".join(lines)

    def _menu(self):
        menu = QMenu(self)
        menu.addAction(
            "取消置顶" if self.pinned else "置顶会话"
        ).triggered.connect(
            lambda: self.pin_toggled.emit(self.session_id, not self.pinned)
        )
        menu.addAction("重命名…").triggered.connect(
            lambda: self.rename_requested.emit(self.session_id)
        )
        menu.addAction(
            "标记已读" if self.unread else "标记未读"
        ).triggered.connect(
            lambda: self.unread_toggled.emit(self.session_id, not self.unread)
        )
        menu.addSeparator()
        menu.addAction("删除会话…").triggered.connect(
            lambda: self.delete_requested.emit(self.session_id)
        )
        return menu

    def _show_menu(self):
        self._menu().exec(QCursor.pos())

    def contextMenuEvent(self, event):
        self._menu().exec(event.globalPos())
        event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit(self.session_id)
            event.accept()
            return
        if event.key() == Qt.Key_Menu:
            self._show_menu()
            event.accept()
            return
        super().keyPressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.session_id)
        super().mouseReleaseEvent(event)


def side_dialog_geometry(preferred, parent, available, margin=16):
    """Place a dialog beside the main window, preferring the right edge."""
    screen_left = available.x()
    screen_right = available.x() + available.width()
    screen_top = available.y()
    screen_bottom = available.y() + available.height()
    right_x = parent.x() + parent.width() + margin
    right_room = max(0, screen_right - right_x)
    left_room = max(0, parent.x() - margin - screen_left)
    min_width = min(320, preferred.width())

    if preferred.width() <= right_room:
        width = preferred.width()
        x = right_x
    elif preferred.width() <= left_room:
        width = preferred.width()
        x = parent.x() - margin - width
    elif max(right_room, left_room) >= min_width:
        if right_room >= left_room:
            width = right_room
            x = right_x
        else:
            width = left_room
            x = parent.x() - margin - width
    else:
        width = min(preferred.width(), available.width())
        x = screen_left + (available.width() - width) // 2

    height = min(preferred.height(), available.height())
    y = parent.y()
    top_room = max(0, parent.y() - margin - screen_top)
    bottom_room = max(0, screen_bottom - parent.y() - parent.height() - margin)
    if x + width <= parent.x() or x >= parent.x() + parent.width():
        if y + height > screen_bottom:
            y = parent.y() + parent.height() - height
    elif max(top_room, bottom_room) >= height:
        x = parent.x() + (parent.width() - width) // 2
        if top_room >= bottom_room:
            y = parent.y() - margin - height
        else:
            y = parent.y() + parent.height() + margin

    x = max(screen_left, min(x, screen_right - width))
    y = max(screen_top, min(y, screen_bottom - height))
    return x, y, width, height


class QtWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, function):
        super().__init__()
        self.function = function

    def run(self):
        try:
            self.completed.emit(self.function())
        except Exception as error:
            self.failed.emit(str(error))


class MemoryDialog(QDialog):
    def __init__(self, runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.setWindowTitle("记忆与共同经历")
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        title = QLabel("记忆账本")
        title.setObjectName("heading")
        layout.addWidget(title)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["ID", "类型", "内容", "状态", "保护"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 2)
        self.detail = QTextBrowser()
        self.detail.setMinimumHeight(150)
        layout.addWidget(self.detail, 2)
        actions = QHBoxLayout()
        correct = QPushButton("纠正选中记忆")
        forget = QPushButton("忘记选中记忆")
        protect = QPushButton("切换受保护")
        persona = QPushButton("新增人设")
        import_button = QPushButton("导入旧聊天")
        self.close_button = QPushButton("关闭")
        actions.addWidget(correct)
        actions.addWidget(forget)
        actions.addWidget(protect)
        actions.addWidget(persona)
        actions.addWidget(import_button)
        actions.addStretch()
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        experience_label = QLabel("共同经历")
        experience_label.setObjectName("heading")
        layout.addWidget(experience_label)
        self.experiences = QTextBrowser()
        layout.addWidget(self.experiences, 1)
        correct.clicked.connect(self.correct)
        forget.clicked.connect(self.forget)
        protect.clicked.connect(self.toggle_protected)
        persona.clicked.connect(self.add_persona)
        import_button.clicked.connect(self.import_chat)
        self.table.itemSelectionChanged.connect(self.show_details)
        self.close_button.clicked.connect(self.close)
        self.refresh()

    def refresh(self):
        memories = self.runtime.memories()
        self.table.setRowCount(len(memories))
        for row, memory in enumerate(memories):
            values = [
                memory.memory_id,
                memory.kind,
                memory.content,
                memory.status,
                "是" if memory.protected else "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        lines = []
        for experience in self.runtime.experiences():
            lines.append(
                "<p><b>{}</b><br>{}</p>".format(
                    html.escape(experience.title),
                    html.escape(experience.summary),
                )
            )
        self.experiences.setHtml(
            "".join(lines) or "<p>还没有共同经历记录。</p>"
        )
        self.show_details()

    def show_details(self):
        memory_id, _ = self.selected_memory()
        if not memory_id:
            self.detail.setHtml("<p>选择一条记忆查看来源、候选与召回记录。</p>")
            return
        try:
            details = self.runtime.memory_details(memory_id)
        except ValueError as error:
            self.detail.setHtml(
                "<p>{}</p>".format(html.escape(str(error)))
            )
            return
        memory = details["memory"]
        lines = [
            "<h3>{}</h3>".format(html.escape(memory.content)),
            "<p>ID：{}<br>类型：{} / 状态：{} / 保护：{}"
            "<br>规范键：{} / 证据：{} 条 / 重要度：{:.2f}</p>".format(
                html.escape(memory.memory_id),
                html.escape(memory.kind),
                html.escape(memory.status),
                "是" if memory.protected else "否",
                html.escape(memory.canonical_key or "-"),
                memory.evidence_count,
                memory.importance,
            ),
        ]
        if details["evidence"]:
            lines.append("<h4>来源证据</h4><ul>")
            for item in details["evidence"]:
                lines.append(
                    "<li>消息 #{}（{}）<br>原文：{}</li>".format(
                        item["message_id"],
                        html.escape(item["source_role"]),
                        html.escape(item.get("message_content", "")),
                    )
                )
            lines.append("</ul>")
        if details["candidates"]:
            lines.append("<h4>候选记录</h4><ul>")
            for item in details["candidates"]:
                lines.append(
                    "<li>{} · 评分 {:.2f} · {}</li>".format(
                        html.escape(item["status"]),
                        item["score"],
                        html.escape(item["reason"]),
                    )
                )
            lines.append("</ul>")
        if details["recalls"]:
            lines.append("<h4>最近召回</h4><ul>")
            for item in details["recalls"]:
                score = item.get("score")
                lines.append(
                    "<li>{} · {} · 评分 {} · {}</li>".format(
                        html.escape(item["created_at"]),
                        html.escape(item.get("source") or "-"),
                        "{:.3f}".format(score)
                        if score is not None
                        else "-",
                        "已采用" if item.get("used_in_reply") else "未采用",
                    )
                )
            lines.append("</ul>")
        if details["events"]:
            lines.append("<h4>生命周期事件</h4><ul>")
            for item in details["events"]:
                lines.append(
                    "<li>{} · {}</li>".format(
                        html.escape(item["event_type"]),
                        html.escape(item["created_at"]),
                    )
                )
            lines.append("</ul>")
        if details["derived"]:
            lines.append(
                "<p>派生记忆：{}</p>".format(
                    html.escape("、".join(details["derived"]))
                )
            )
        self.detail.setHtml("".join(lines))

    def selected_memory(self):
        row = self.table.currentRow()
        if row < 0:
            return None, ""
        return (
            self.table.item(row, 0).text(),
            self.table.item(row, 2).text(),
        )

    def correct(self):
        memory_id, content = self.selected_memory()
        if not memory_id:
            QMessageBox.information(self, "记忆", "请先选择一条记忆。")
            return
        replacement, ok = QInputDialog.getMultiLineText(
            self,
            "纠正记忆",
            "新的内容：",
            content,
        )
        if ok and replacement.strip():
            self.runtime.correct_memory(memory_id, replacement.strip())
            self.refresh()

    def forget(self):
        memory_id, _ = self.selected_memory()
        if not memory_id:
            QMessageBox.information(self, "记忆", "请先选择一条记忆。")
            return
        if QMessageBox.question(
            self,
            "忘记记忆",
            "确认忘记选中的记忆？之后不会再参与召回。",
        ) == QMessageBox.Yes:
            self.runtime.forget_memory(memory_id)
            self.refresh()

    def toggle_protected(self):
        memory_id, _ = self.selected_memory()
        if not memory_id:
            QMessageBox.information(self, "记忆", "请先选择一条记忆。")
            return
        details = self.runtime.memory_details(memory_id)
        self.runtime.set_memory_protected(
            memory_id,
            not details["memory"].protected,
        )
        self.refresh()

    def add_persona(self):
        content, ok = QInputDialog.getMultiLineText(
            self,
            "新增人设",
            "希望长期保留、不会被自动淘汰的人设：",
            "",
        )
        if not ok or not content.strip():
            return
        self.runtime.remember_persona(content.strip())
        self.refresh()

    def import_chat(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入旧聊天",
            "",
            "聊天记录 (*.json *.jsonl *.ndjson);;所有文件 (*)",
        )
        if not path:
            return
        try:
            estimate = self.runtime.estimate_chat_import(path)
        except Exception as error:
            QMessageBox.warning(self, "导入旧聊天", str(error))
            return
        detail = (
            "文件：{}\n消息：{} 条（其中用户 {} 条）\n"
            "会话：{} 个\n预计生成记忆：{} 条\n"
            "预计耗时：约 {:.1f} 秒\n已导入进度：{}/{}"
        ).format(
            Path(path).name,
            estimate["messages"],
            estimate["user_messages"],
            estimate["conversations"],
            estimate["estimated_memories"],
            estimate["estimated_seconds"],
            estimate["resume_from"],
            estimate["messages"],
        )
        if QMessageBox.question(
            self,
            "导入成本预估",
            detail + "\n\n导入会写入本地数据库，确定继续吗？",
        ) != QMessageBox.Yes:
            return
        try:
            result = self.runtime.import_chat(path)
        except Exception as error:
            QMessageBox.warning(self, "导入旧聊天", str(error))
            return
        QMessageBox.information(
            self,
            "导入旧聊天",
            "状态：{}\n导入消息：{} 条\n新记忆：{} 条\n"
            "跳过重复：{} 条".format(
                result["status"],
                result["imported_messages"],
                result["imported_memories"],
                result["skipped_duplicates"],
            ),
        )
        self.refresh()


class AffectDialog(QDialog):
    def __init__(self, runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.setWindowTitle("情绪状态")
        self.resize(480, 300)
        layout = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        info = QLabel(
            "情绪只是根据对话做出的估计。你拥有最终解释权，可以随时纠正。"
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        row = QHBoxLayout()
        for label, value in [
            ("我现在没事", "neutral"),
            ("我有点低落", "low"),
            ("我只是累了", "tired"),
            ("我心情不错", "positive"),
        ]:
            button = QPushButton(label)
            button.clicked.connect(
                lambda checked=False, mood=value: self.correct(mood)
            )
            row.addWidget(button)
        layout.addLayout(row)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.close)
        layout.addWidget(self.close_button)
        self.refresh()

    def refresh(self):
        state = self.runtime.affect_snapshot()
        user = state["user"]
        agent = state["agent"]
        self.summary.setText(
            "你的状态估计：{}，置信度 {:.2f}\n"
            "角色状态：{}，精力 {}".format(
                user["mood_label"],
                user["confidence"],
                agent["mood_label"],
                "未知"
                if agent["energy"] is None
                else "{:.2f}".format(agent["energy"]),
            )
        )

    def correct(self, mood):
        self.runtime.correct_affect(mood)
        self.refresh()


class ProactiveDialog(QDialog):
    spoke = Signal()

    def __init__(self, runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.setWindowTitle("主动陪伴")
        self.resize(700, 600)
        layout = QVBoxLayout(self)
        title = QLabel("主动陪伴")
        title.setObjectName("heading")
        layout.addWidget(title)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.summary)
        info = QLabel(
            "主动消息只来自到点提醒、未完成话题或你近期用过的记忆；"
            "没有可追溯来源时不会发言。错过的提醒不会补发，只标记为未读。"
        )
        info.setObjectName("muted")
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        self.enabled_box = QCheckBox("开启主动发言")
        form.addRow("状态", self.enabled_box)
        quiet_row = QHBoxLayout()
        self.quiet_start = QSpinBox()
        self.quiet_start.setRange(0, 23)
        self.quiet_start.setSuffix(" 点")
        self.quiet_end = QSpinBox()
        self.quiet_end.setRange(0, 23)
        self.quiet_end.setSuffix(" 点")
        quiet_row.addWidget(self.quiet_start)
        quiet_row.addWidget(QLabel("至"))
        quiet_row.addWidget(self.quiet_end)
        quiet_row.addWidget(QLabel("（相同则关闭免打扰）"))
        quiet_row.addStretch()
        form.addRow("免打扰", quiet_row)
        self.daily_limit = QSpinBox()
        self.daily_limit.setRange(0, 20)
        self.daily_limit.setSuffix(" 次/天")
        form.addRow("每日上限", self.daily_limit)
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0, 168)
        self.interval.setDecimals(1)
        self.interval.setSuffix(" 小时")
        form.addRow("最小间隔", self.interval)
        self.max_unanswered = QSpinBox()
        self.max_unanswered.setRange(0, 20)
        self.max_unanswered.setSuffix(" 次")
        form.addRow("连续未回复", self.max_unanswered)
        self.include_memory = QCheckBox("允许近期用过的记忆作为来源")
        form.addRow("记忆来源", self.include_memory)
        layout.addLayout(form)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["时间", "来源", "状态", "触发原因"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        self.note = QLabel()
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        actions = QHBoxLayout()
        save = QPushButton("保存设置")
        save.setObjectName("primary")
        refresh = QPushButton("刷新")
        read_all = QPushButton("全部已读")
        check_now = QPushButton("现在检查")
        close = QPushButton("关闭")
        actions.addWidget(save)
        actions.addWidget(check_now)
        actions.addWidget(read_all)
        actions.addWidget(refresh)
        actions.addStretch()
        actions.addWidget(close)
        layout.addLayout(actions)
        save.clicked.connect(self.save)
        check_now.clicked.connect(self.check_now)
        read_all.clicked.connect(self.mark_all_read)
        refresh.clicked.connect(self.refresh)
        close.clicked.connect(self.close)
        self.refresh()

    def refresh(self):
        status = self.runtime.proactive_status()
        settings = status["settings"]
        next_due = status["next_due"]
        lines = [
            "状态：{}｜当前活动：{}（空闲 {:.0f} 秒）".format(
                "开启" if status["enabled"] else "已暂停",
                status["activity_label"],
                status["idle_seconds"],
            ),
            "今日主动：{}/{} 次｜连续未回复：{}/{}｜未读事件：{} 条".format(
                status["daily_count"],
                status["daily_limit"],
                status["unanswered"],
                status["max_unanswered"],
                status["unread"],
            ),
        ]
        if next_due:
            lines.append(
                "下一条提醒：{}（{}）".format(
                    next_due["content"],
                    next_due["display"],
                )
            )
        else:
            lines.append("下一条提醒：无")
        if status["last_event"]:
            last = status["last_event"]
            lines.append(
                "最近一次：{}｜{}｜{}".format(
                    last["status_label"],
                    last["trigger_reason"],
                    last["created_at"],
                )
            )
        self.summary.setText("\n".join(lines))
        self.enabled_box.setChecked(bool(status["enabled"]))
        self.quiet_start.setValue(int(settings["quiet_start"]))
        self.quiet_end.setValue(int(settings["quiet_end"]))
        self.daily_limit.setValue(int(settings["daily_limit"]))
        self.interval.setValue(float(settings["min_interval_hours"]))
        self.max_unanswered.setValue(int(settings["max_unanswered"]))
        self.include_memory.setChecked(bool(settings["include_memory"]))

        events = self.runtime.proactive_events(20)
        self.table.setRowCount(len(events))
        for row, event in enumerate(events):
            try:
                moment = datetime.fromisoformat(event.created_at)
                when = moment.astimezone().strftime("%m-%d %H:%M")
            except ValueError:
                when = event.created_at
            state = status_label(event.status)
            if event.read_at is None:
                state += " / 未读"
            values = [
                when,
                event.source_type,
                state,
                event.trigger_reason,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, column, item)
            self.table.item(row, 3).setToolTip(event.content)
        self.table.resizeColumnsToContents()

    def save(self):
        try:
            self.runtime.update_proactive_settings(
                enabled=self.enabled_box.isChecked(),
                quiet_start=self.quiet_start.value(),
                quiet_end=self.quiet_end.value(),
                daily_limit=self.daily_limit.value(),
                min_interval_hours=self.interval.value(),
                max_unanswered=self.max_unanswered.value(),
                include_memory=self.include_memory.isChecked(),
            )
        except ValueError as error:
            QMessageBox.warning(self, "设置不可用", str(error))
            return
        self.note.setText("设置已保存。")
        self.refresh()

    def check_now(self):
        try:
            result = self.runtime.proactive_tick()
        except Exception as error:
            QMessageBox.warning(self, "主动检查失败", str(error))
            return
        if result.get("spoke"):
            self.note.setText(
                "刚刚主动发言：{}".format(
                    result.get("trigger_reason") or "已送达"
                )
            )
            self.spoke.emit()
        elif result.get("missed"):
            self.note.setText(
                "有 {} 条提醒错过送达窗口，已标记未读。".format(
                    len(result["missed"])
                )
            )
        else:
            self.note.setText(
                "现在不发言：{}".format(
                    result.get("reason_label") or result.get("reason")
                )
            )
        self.refresh()

    def mark_all_read(self):
        count = self.runtime.mark_proactive_read()
        self.note.setText("已标记 {} 条事件为已读。".format(count))
        self.refresh()


class PersonaDialog(QDialog):
    persona_changed = Signal()

    def __init__(self, runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.setWindowTitle("人设与设定")
        self.resize(580, 680)
        layout = QVBoxLayout(self)
        heading = QLabel("人设包")
        heading.setObjectName("heading")
        layout.addWidget(heading)
        self.info = QTextBrowser()
        self.info.setMinimumHeight(200)
        layout.addWidget(self.info, 2)

        form = QFormLayout()
        self.address = QLineEdit()
        self.address.setPlaceholderText("例如：搭档、共犯、名字")
        self.relationship = QTextEdit()
        self.relationship.setFixedHeight(70)
        self.relationship.setPlaceholderText("你希望她如何理解你们的关系")
        self.boundaries = QTextEdit()
        self.boundaries.setFixedHeight(70)
        self.boundaries.setPlaceholderText("不希望被提起的话题、称呼或行为")
        form.addRow("称呼", self.address)
        form.addRow("关系", self.relationship)
        form.addRow("边界与雷点", self.boundaries)
        layout.addLayout(form)

        note = QLabel(
            "这些设定以受保护人设记忆保存，优先于角色默认；留空表示清除。"
            "它们不能覆盖安全规则或工具确认。"
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        actions = QHBoxLayout()
        save = QPushButton("保存设定")
        import_button = QPushButton("导入人设包…")
        self.close_button = QPushButton("关闭")
        actions.addWidget(save)
        actions.addWidget(import_button)
        actions.addStretch()
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        save.clicked.connect(self.save)
        import_button.clicked.connect(self.import_pack)
        self.close_button.clicked.connect(self.close)
        self.refresh()

    def refresh(self):
        info = self.runtime.persona_info()
        source = info.get("source")
        if not isinstance(source, dict):
            source = {}
        license_info = info.get("license")
        if not isinstance(license_info, dict):
            license_info = {}
        lines = [
            "<h3>{} <span style='color:#8da7ad'>v{}</span></h3>".format(
                html.escape(str(info.get("name") or "数字分身")),
                html.escape(str(info.get("version") or "-")),
            ),
            "<p>ID：{}<br>稳定前缀哈希：{}<br>世界书：{} 条 / {} 字符</p>".format(
                html.escape(str(info.get("id") or "-")),
                html.escape(str(info.get("stable_hash") or "-")),
                info.get("worldbook_entries", 0),
                info.get("worldbook_chars", 0),
            ),
        ]
        if not info.get("loaded"):
            lines.append(
                "<p style='color:#f2c28f'>人设包未加载：{}</p>".format(
                    html.escape(str(info.get("error") or "目录不存在"))
                )
            )
        source_bits = []
        for label, key in (
            ("作品", "work"),
            ("权利方", "owner"),
            ("版本", "release"),
            ("设定截止", "canon_cutoff"),
        ):
            value = source.get(key)
            if value:
                source_bits.append(
                    "{}：{}".format(label, html.escape(str(value)))
                )
        if source_bits:
            lines.append("<p>来源<br>{}</p>".format("<br>".join(source_bits)))
        license_bits = []
        for label, key in (
            ("角色 IP", "character_ip"),
            ("使用边界", "derivative_policy"),
        ):
            value = license_info.get(key)
            if value:
                license_bits.append(
                    "{}：{}".format(label, html.escape(str(value)))
                )
        if license_bits:
            lines.append(
                "<p>许可<br>{}</p>".format("<br>".join(license_bits))
            )
        files = info.get("files")
        if isinstance(files, dict) and files:
            lines.append(
                "<p>文件<br>{}</p>".format(
                    "<br>".join(
                        "{}：{:.1f} KB".format(
                            html.escape(str(name)),
                            size / 1024.0,
                        )
                        for name, size in files.items()
                    )
                )
            )
        self.info.setHtml("".join(lines))

        preferences = info.get("preferences")
        if not isinstance(preferences, dict):
            preferences = {}
        self.address.setText(str(preferences.get("address") or ""))
        self.relationship.setPlainText(
            str(preferences.get("relationship") or "")
        )
        self.boundaries.setPlainText(
            str(preferences.get("boundaries") or "")
        )

    def save(self):
        try:
            self.runtime.set_persona_preference(
                "address",
                self.address.text(),
            )
            self.runtime.set_persona_preference(
                "relationship",
                self.relationship.toPlainText(),
            )
            self.runtime.set_persona_preference(
                "boundaries",
                self.boundaries.toPlainText(),
            )
        except ValueError as error:
            QMessageBox.warning(self, "人设设定", str(error))
            return
        self.refresh()
        self.status.setText("已保存。空白的设定项已清除。")
        self.persona_changed.emit()

    def import_pack(self):
        path = QFileDialog.getExistingDirectory(
            self,
            "选择人设包目录",
            "",
        )
        if not path:
            return
        try:
            preview = self.runtime.preview_persona_import(path)
        except Exception as error:
            QMessageBox.warning(self, "导入人设包", str(error))
            return
        source = preview.get("source")
        if not isinstance(source, dict):
            source = {}
        license_info = preview.get("license")
        if not isinstance(license_info, dict):
            license_info = {}
        warnings = preview.get("warnings") or []
        detail = (
            "名称：{}\nID：{}\n版本：{}\n作品：{}\n权利方：{}\n"
            "角色 IP：{}\n总大小：{:.1f} KB\n世界书：{} 条"
        ).format(
            preview.get("name"),
            preview.get("id"),
            preview.get("version"),
            source.get("work") or "-",
            source.get("owner") or "-",
            license_info.get("character_ip") or "-",
            preview.get("total_bytes", 0) / 1024.0,
            preview.get("worldbook_entries", 0),
        )
        if warnings:
            detail += "\n\n" + "\n".join(str(item) for item in warnings)
        overwrite = bool(preview.get("target_exists"))
        if overwrite:
            question = (
                detail
                + "\n\n同 ID 人设包已存在。覆盖后会用新文件替换当前版本，"
                "已有用户记忆不受影响。继续吗？"
            )
        else:
            question = detail + "\n\n只复制人设文本文件，确定导入吗？"
        if QMessageBox.question(
            self,
            "导入人设包",
            question,
        ) != QMessageBox.Yes:
            return
        try:
            info = self.runtime.import_persona(
                path,
                overwrite=overwrite,
            )
        except Exception as error:
            QMessageBox.warning(self, "导入人设包", str(error))
            return
        self.refresh()
        self.status.setText(
            "已导入并切换到“{}”。当前哈希：{}".format(
                info.get("name"),
                str(info.get("stable_hash") or "")[:12],
            )
        )
        self.persona_changed.emit()


class WorkDialog(QDialog):
    work_changed = Signal()

    def __init__(self, runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.tasks = []
        self.worker = None
        self.busy = False
        self.setWindowTitle("工作任务卡")
        self.resize(760, 620)
        layout = QVBoxLayout(self)
        title = QLabel("工作任务卡与计划模式")
        title.setObjectName("heading")
        layout.addWidget(title)
        note = QLabel(
            "任务先落库为计划，批准前不会执行或转发。确认超时只会退回讨论，"
            "不会自动通过；转发结果不确定时不会自动重发。"
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["更新时间", "任务", "计划状态", "转发回执"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        layout.addWidget(self.table, 1)
        self.detail = QTextBrowser()
        self.detail.setMinimumHeight(180)
        layout.addWidget(self.detail, 1)
        self.note = QLabel()
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        actions = QHBoxLayout()
        new_button = QPushButton("新建任务卡")
        new_button.setObjectName("primary")
        self.approve_button = QPushButton("批准计划")
        self.revise_button = QPushButton("修改计划")
        self.reject_button = QPushButton("拒绝")
        refresh = QPushButton("刷新")
        close = QPushButton("关闭")
        self.close_button = close
        actions.addWidget(new_button)
        actions.addWidget(self.approve_button)
        actions.addWidget(self.revise_button)
        actions.addWidget(self.reject_button)
        actions.addStretch()
        actions.addWidget(refresh)
        actions.addWidget(close)
        layout.addLayout(actions)
        new_button.clicked.connect(self.new_task)
        self.approve_button.clicked.connect(self.approve)
        self.revise_button.clicked.connect(self.revise)
        self.reject_button.clicked.connect(self.reject_task)
        refresh.clicked.connect(self.refresh)
        close.clicked.connect(self.close)
        self.table.itemSelectionChanged.connect(self.show_selected)
        self.refresh()

    def selected_task(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        row = rows[0].row()
        if 0 <= row < len(self.tasks):
            return self.tasks[row]
        return None

    def refresh(self):
        selected = self.selected_task()
        selected_id = selected.task_id if selected else ""
        self.tasks = self.runtime.work_tasks(20)
        self.table.setRowCount(len(self.tasks))
        for row, task in enumerate(self.tasks):
            try:
                moment = datetime.fromisoformat(task.updated_at)
                when = moment.astimezone().strftime("%m-%d %H:%M")
            except ValueError:
                when = task.updated_at
            receipt = self.runtime.work_receipt(task.task_id)
            values = [
                when,
                task.card.title,
                STATE_LABELS.get(task.state, task.state),
                FORWARD_LABELS.get(receipt.status, receipt.status) if receipt else "-",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        if self.tasks:
            target = 0
            for index, task in enumerate(self.tasks):
                if task.task_id == selected_id:
                    target = index
                    break
            self.table.selectRow(target)
        else:
            self.detail.setHtml("<p>还没有任务卡。</p>")
        self.update_buttons()

    def show_selected(self):
        task = self.selected_task()
        self.update_buttons()
        if task is None:
            self.detail.setHtml("<p>选择一条任务查看计划。</p>")
            return
        receipt = self.runtime.work_receipt(task.task_id)
        lines = [
            "<p><b>标题</b>：{}</p>".format(html.escape(task.card.title)),
            "<p><b>目标</b>：{}</p>".format(
                html.escape(task.card.goal).replace("\n", "<br>")
            ),
            "<p><b>理由</b>：{}</p>".format(html.escape(task.card.reason)),
            "<p><b>执行器</b>：{}　<b>项目</b>：{}</p>".format(
                html.escape(task.card.executor),
                html.escape(task.card.project),
            ),
            "<p><b>口语播报</b>：{}</p>".format(
                html.escape(task.card.spoken_summary)
            ),
            "<p><b>计划状态</b>：{}　<b>修订</b>：{}　"
            "<b>已追问</b>：{} 次</p>".format(
                STATE_LABELS.get(task.state, task.state),
                task.revision,
                task.info_asked,
            ),
        ]
        if task.approval_reason:
            lines.append(
                "<p><b>审批说明</b>：{}</p>".format(
                    html.escape(task.approval_reason)
                )
            )
        if receipt:
            lines.append(
                "<p><b>转发回执</b>：{}（尝试 {} 次）<br>{}</p>".format(
                    FORWARD_LABELS.get(receipt.status, receipt.status),
                    receipt.attempts,
                    html.escape(receipt.summary),
                )
            )
            if receipt.detail:
                lines.append(
                    "<p><b>回执详情</b>：{}</p>".format(
                        html.escape(receipt.detail)
                    )
                )
            if receipt.status == "unknown":
                lines.append(
                    "<p><b>注意</b>：结果不确定，系统不会自动重发。</p>"
                )
        self.detail.setHtml("".join(lines))

    def update_buttons(self):
        task = self.selected_task()
        actionable = (
            task is not None
            and not self.busy
            and task.state in (WORK_PLAN_REVIEW, WORK_PLAN_DISCUSSING)
        )
        self.approve_button.setEnabled(
            actionable and task.state == WORK_PLAN_REVIEW
        )
        self.revise_button.setEnabled(actionable)
        self.reject_button.setEnabled(actionable)

    def set_busy(self, busy):
        self.busy = busy
        self.update_buttons()

    def new_task(self):
        text, accepted = QInputDialog.getMultiLineText(
            self,
            "新建任务卡",
            "描述你想完成的工作：",
        )
        if not accepted or not text.strip():
            return
        self._run(lambda: self.runtime.create_work_task(text, force=True))

    def approve(self):
        task = self.selected_task()
        if task is None:
            return
        if QMessageBox.question(
            self,
            "批准计划",
            "批准后才会调用执行器。若结果不确定，系统不会自动重发。继续吗？",
        ) != QMessageBox.Yes:
            return
        self._run(lambda: self.runtime.approve_work_task(task.task_id))

    def revise(self):
        task = self.selected_task()
        if task is None:
            return
        text, accepted = QInputDialog.getMultiLineText(
            self,
            "修改计划",
            "说明要修改的目标、对象或步骤：",
        )
        if not accepted or not text.strip():
            return
        self._run(
            lambda: self.runtime.revise_work_task(task.task_id, text)
        )

    def reject_task(self):
        task = self.selected_task()
        if task is None:
            return
        if QMessageBox.question(
            self,
            "拒绝计划",
            "拒绝后不会执行或转发这个任务。继续吗？",
        ) != QMessageBox.Yes:
            return
        self._run(
            lambda: self.runtime.reject_work_task(
                task.task_id,
                "用户在任务面板拒绝",
            )
        )

    def _run(self, function, on_success=None):
        if self.worker is not None:
            return
        self.set_busy(True)
        self.note.setText("处理中…")
        worker = QtWorker(function)
        self.worker = worker

        def done(result):
            self.worker = None
            self.set_busy(False)
            self.note.setText(
                (result or {}).get("answer", "已完成。").splitlines()[0]
            )
            self.refresh()
            self.work_changed.emit()
            if on_success is not None:
                on_success(result)

        def failed(message):
            self.worker = None
            self.set_busy(False)
            self.note.setText("操作失败：{}".format(message))
            QMessageBox.warning(self, "任务操作失败", message)

        worker.completed.connect(done)
        worker.failed.connect(failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()


class CompanionWindow(QMainWindow):
    def __init__(self, runtime):
        super().__init__()
        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(False)
        if isinstance(runtime, CompanionApplicationService):
            self.application = runtime
        else:
            self.application = create_application_service(runtime)
        self.runtime = self.application.runtime
        self.mode = AgentMode.CHAT
        self.avatar_status = "待机"
        self.current_proposal = None
        self.memory_dialog = None
        self.affect_dialog = None
        self.persona_dialog = None
        self.proactive_dialog = None
        self.work_dialog = None
        self.console_dialog = None
        self.settings_dialog = None
        self.proactive_inflight = False
        self.workers = []
        self.drag_position = QPoint()
        self._restore_geometry = None
        self.ui_state = UiStateStore(self.runtime.data_dir)
        self._restore_active_session()
        self.tray = None
        self.active_generation = None
        self.stopped_generation = None
        self.stream_worker = None
        self.stream_text = ""
        self.persona_name = (
            self.runtime.persona.name
            if getattr(self.runtime, "persona", None)
            else "数字分身"
        )
        self.theme_name = normalize_theme(self.ui_state.theme())
        self.font_scale = self.ui_state.font_scale()
        self.reduce_motion = self.ui_state.reduce_motion()
        self.tokens = theme(self.theme_name)
        self.turn_tools = []
        self.inspector_shown = self.ui_state.inspector_visible()
        self.setWindowTitle(self.persona_name)
        # A Qt.Tool window has no taskbar entry, so hiding it leaves the tray as the
        # only way back - unusable where the tray is missing or collapsed. The panel
        # behaves like a normal window; only the Web overlay floats above others.
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowMinimizeButtonHint
        )
        self.setWindowIcon(app_icon(self.runtime))
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
        shell = QWidget()
        shell.setObjectName("shell")
        self.setCentralWidget(shell)
        shell_row = QHBoxLayout(shell)
        shell_row.setContentsMargins(0, 0, 0, 0)
        shell_row.setSpacing(0)

        self.rail_host = QWidget()
        self.rail_host.setObjectName("railHost")
        rail_box = QHBoxLayout(self.rail_host)
        rail_box.setContentsMargins(0, 0, 0, 0)
        rail_box.setSpacing(0)
        self.rail = self._build_rail()
        rail_box.addWidget(self.rail)
        self.rail_visible = bool(self.ui_state.load().get("rail_visible", True))
        self.rail_host.setFixedWidth(RAIL_WIDTH if self.rail_visible else 0)
        self._rail_auto_hidden = False
        self._rail_drawer_open = False
        shell_row.addWidget(self.rail_host)

        main = QWidget()
        main.setObjectName("mainColumn")
        main.setMinimumWidth(360)
        outer = QVBoxLayout(main)
        outer.setContentsMargins(16, 12, 16, 14)
        outer.setSpacing(10)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setObjectName("mainSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(4)
        self.splitter.addWidget(main)
        self.inspector = InspectorPanel(self.application)
        self.inspector.close_requested.connect(
            lambda: self.toggle_inspector(False)
        )
        self.splitter.addWidget(self.inspector)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        shell_row.addWidget(self.splitter, 1)
        self._inspector_auto_hidden = False
        self._inspector_compact_override = False
        self._inspector_user_width = self.ui_state.inspector_width()
        self.inspector.setVisible(self.inspector_shown)
        self.splitter.setSizes(
            [
                max(420, WINDOW_WIDTH - RAIL_WIDTH - self._inspector_user_width),
                self._inspector_user_width,
            ]
        )
        self.splitter.splitterMoved.connect(self._on_splitter_moved)

        header = QHBoxLayout()
        header.setSpacing(6)
        self.sidebar_button = QPushButton("≡")
        self.sidebar_button.setObjectName("ghost")
        self.sidebar_button.setFixedWidth(34)
        self.sidebar_button.setToolTip("显示或隐藏左侧栏")
        self.sidebar_button.setAccessibleName("显示或隐藏左侧栏")
        self.sidebar_button.clicked.connect(self.toggle_sidebar)
        header.addWidget(self.sidebar_button)

        identity = QHBoxLayout()
        identity.setSpacing(6)
        who = QVBoxLayout()
        who.setSpacing(0)
        self.name_label = QLabel(self.persona_name)
        self.name_label.setObjectName("brandSmall")
        self.status_pill = QLabel("待机")
        self.status_pill.setObjectName("pill")
        who.addWidget(self.name_label)
        who.addWidget(self.status_pill, 0, Qt.AlignLeft)
        identity.addLayout(who)
        header.addSpacing(4)
        header.addLayout(identity)
        header.addStretch(2)

        self.mode_switch = QFrame()
        self.mode_switch.setObjectName("modeSwitch")
        switch_row = QHBoxLayout(self.mode_switch)
        switch_row.setContentsMargins(3, 3, 3, 3)
        switch_row.setSpacing(2)
        self.chat_button = QPushButton("陪伴")
        self.work_button = QPushButton("工作")
        self.chat_button.clicked.connect(lambda: self.set_mode(AgentMode.CHAT))
        self.work_button.clicked.connect(lambda: self.set_mode(AgentMode.WORK))
        switch_row.addWidget(self.chat_button)
        switch_row.addWidget(self.work_button)
        header.addWidget(self.mode_switch)
        header.addStretch(2)

        self.inspector_button = QPushButton("面板")
        self.inspector_button.setObjectName("ghost")
        self.inspector_button.setCheckable(True)
        self.inspector_button.setChecked(self.inspector_shown)
        self.inspector_button.setToolTip("显示或隐藏上下文抽屉")
        self.inspector_button.setAccessibleName("显示或隐藏上下文抽屉")
        self.inspector_button.clicked.connect(self.toggle_inspector)
        self.minimize_button = QPushButton("最小化")
        self.minimize_button.setObjectName("ghost")
        self.minimize_button.setAccessibleName("最小化窗口")
        self.minimize_button.clicked.connect(self.minimize_to_taskbar)
        close = QPushButton("关闭")
        close.setObjectName("ghost")
        close.setAccessibleName("关闭窗口")
        close.clicked.connect(self.quit_application)
        header.addWidget(self.inspector_button)
        header.addWidget(self.minimize_button)
        header.addWidget(close)
        outer.addLayout(header)

        self.chat = ChatView(assistant_name=self.persona_name)
        self.chat.link_activated.connect(self.open_external_link)
        self.chat.quick_start.connect(self.use_quick_start)
        self.chat.set_reduce_motion(self.reduce_motion)
        outer.addWidget(self.chat, 1)

        composer = QFrame()
        composer.setObjectName("composer")
        composer_box = QVBoxLayout(composer)
        composer_box.setContentsMargins(12, 10, 12, 8)
        composer_box.setSpacing(6)
        self.run_bar = RunBar(show_stop=False)
        self.run_bar.stop_requested.connect(self.stop_stream)
        composer_box.addWidget(self.run_bar)

        pill_row = QHBoxLayout()
        pill_row.setSpacing(6)
        self.mode_pill = self._composer_pill("mode")
        self.workspace_pill = self._composer_pill("workspace")
        self.context_pill = self._composer_pill("context")
        pill_row.addWidget(self.mode_pill)
        pill_row.addWidget(self.workspace_pill)
        pill_row.addWidget(self.context_pill)
        pill_row.addStretch(1)
        composer_box.addLayout(pill_row)

        self.input = QTextEdit()
        self.input.setPlaceholderText(
            "和{}说点什么，或在工作模式搜索并保存笔记".format(self.persona_name)
        )
        self.input.setAccessibleName("输入消息")
        self.input.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.input.installEventFilter(self)
        self._composing = False
        composer_box.addWidget(self.input)
        self._restoring_draft = False
        self._draft_session = self.runtime.session_id
        self._draft_timer = QTimer(self)
        self._draft_timer.setSingleShot(True)
        self._draft_timer.setInterval(DRAFT_SAVE_DELAY_MS)
        self._draft_timer.timeout.connect(self._save_draft_now)
        self.input.textChanged.connect(self._on_input_changed)
        self._resize_input()

        composer_row = QHBoxLayout()
        composer_row.setSpacing(6)
        self.composer_hint = QLabel("0 字")
        self.composer_hint.setObjectName("composerCount")
        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("primary")
        self.send_button.setFixedWidth(78)
        self.send_button.setAccessibleName("发送消息")
        self.send_button.setToolTip("发送消息（Enter）")
        self.send_button.clicked.connect(self.primary_action)
        composer_row.addWidget(self.composer_hint, 1)
        composer_row.addWidget(self.send_button)
        composer_box.addLayout(composer_row)
        outer.addWidget(composer)

        self.stop_button = self.send_button
        self._refresh_char_hint()

        self.confirm_frame = ApprovalCard()
        self.confirm_plan = self.confirm_frame.plan_label
        self.confirm_button = self.confirm_frame.confirm_button
        self.cancel_button = self.confirm_frame.cancel_button
        self.confirm_frame.confirm_requested.connect(self.confirm_action)
        self.confirm_frame.cancel_requested.connect(self.cancel_action)
        self.confirm_frame.undo_requested.connect(self.undo_last)
        self.confirm_frame.close_requested.connect(self._refresh_inspector)
        self.chat.attach_approval_card(self.confirm_frame)

        status_row = QHBoxLayout()
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.undo_button = QPushButton("撤销最近写入")
        self.undo_button.setObjectName("ghost")
        self.undo_button.clicked.connect(self.undo_last)
        self.undo_button.hide()
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.undo_button)
        outer.addLayout(status_row)

        self.set_mode(self.runtime.current_mode())
        self.refresh_workspace()
        self.refresh_history()
        self.refresh_session_list()
        self.refresh_pending()
        self._restore_draft()
        self.proactive_timer = QTimer(self)
        self.proactive_timer.setInterval(60000)
        self.proactive_timer.timeout.connect(self.poll_proactive)
        self.proactive_timer.start()
        self.chrome_timer = QTimer(self)
        self.chrome_timer.setInterval(30000)
        self.chrome_timer.timeout.connect(self._refresh_inspector)
        self.chrome_timer.start()

        self.new_shortcut = QShortcut(QKeySequence("Ctrl+N"), self)
        self.new_shortcut.activated.connect(self.new_session)
        self.search_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.search_shortcut.activated.connect(self.focus_search)
        self.settings_shortcut = QShortcut(QKeySequence("Ctrl+,"), self)
        self.settings_shortcut.activated.connect(self.open_settings)

        self.tray = TrayController(self, self.runtime)
        self._apply_stylesheet()
        self.restore_window_geometry()

    def _section_label(self, text):
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def _build_rail(self):
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(RAIL_WIDTH)
        rail.setVisible(bool(self.ui_state.load().get("rail_visible", True)))
        self.rail = rail
        box = QVBoxLayout(rail)
        box.setContentsMargins(8, 10, 6, 10)
        box.setSpacing(2)

        self.new_task_button = self._rail_action("new", "新的任务")
        self.new_task_button.setToolTip("开始一个新的会话（Ctrl+N）")
        self.new_task_button.setAccessibleName("新建会话，快捷键 Ctrl+N")
        self.new_task_button.clicked.connect(self.new_session)
        box.addWidget(self.new_task_button)

        self.search_button = self._rail_action("search", "搜索")
        self.search_button.setToolTip("按标题或内容筛选会话（Ctrl+K）")
        self.search_button.setAccessibleName("搜索会话，快捷键 Ctrl+K")
        self.search_button.clicked.connect(self.toggle_search)
        box.addWidget(self.search_button)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("railSearch")
        self.search_edit.setPlaceholderText("搜索会话（Esc 退出）")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setAccessibleName("搜索会话")
        self.search_edit.hide()
        self.search_edit.textChanged.connect(self._filter_sessions)
        self.search_edit.installEventFilter(self)
        box.addWidget(self.search_edit)

        box.addSpacing(10)
        box.addWidget(self._section_label("工作区"))
        box.addSpacing(2)
        box.addWidget(self._build_workspace_row())
        box.addSpacing(6)
        box.addWidget(self._section_label("会话"))
        box.addSpacing(2)

        self.session_empty = QLabel("还没有会话")
        self.session_empty.setObjectName("sessionEmpty")
        self.session_empty.setWordWrap(True)
        self.session_empty.hide()

        self.session_scroll = QScrollArea()
        self.session_scroll.setObjectName("sessionList")
        self.session_scroll.setWidgetResizable(True)
        self.session_scroll.setFrameShape(QFrame.NoFrame)
        self.session_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        list_body = QWidget()
        list_body.setObjectName("sessionListBody")
        self.session_box = QVBoxLayout(list_body)
        self.session_box.setContentsMargins(0, 0, 0, 0)
        self.session_box.setSpacing(1)
        self.session_box.addWidget(self.session_empty)
        self.session_box.addStretch(1)
        self.session_scroll.setWidget(list_body)
        self.session_rows = []
        self.session_group_rows = []
        self._session_summaries = []
        box.addWidget(self.session_scroll, 1)

        self.settings_button = self._rail_action("settings", "设置")
        self.settings_button.setToolTip(
            "记忆、情绪、人设、主动、任务、管理与外观（Ctrl+,）"
        )
        self.settings_button.setAccessibleName("打开设置，快捷键 Ctrl+逗号")
        self.settings_button.clicked.connect(self.open_settings)
        box.addWidget(self.settings_button)
        return rail

    def _rail_action(self, kind, text):
        button = QPushButton(text)
        button.setObjectName("railAction")
        button.setCursor(Qt.PointingHandCursor)
        button._rail_kind = kind
        button.setIcon(QIcon(rail_icon(kind, self.tokens)))
        button.setIconSize(QSize(16, 16))
        return button

    def _composer_pill(self, variant):
        label = QLabel()
        label.setObjectName("composerPill")
        label.setProperty("variant", variant)
        return label

    def _build_workspace_row(self):
        row = QFrame()
        row.setObjectName("workspaceRow")
        row.setAttribute(Qt.WA_Hover, True)
        line = QHBoxLayout(row)
        line.setContentsMargins(6, 4, 2, 4)
        line.setSpacing(4)

        self.workspace_icon = QLabel()
        self.workspace_icon.setObjectName("railIcon")
        self.workspace_icon.setPixmap(rail_icon("folder", self.tokens))
        line.addWidget(self.workspace_icon)

        self.workspace_name = ElidedLabel("未授权")
        self.workspace_name.setObjectName("workspaceName")
        line.addWidget(self.workspace_name, 1)

        self.workspace_more = QPushButton()
        self.workspace_more.setObjectName("railMini")
        self.workspace_more.setIcon(QIcon(rail_icon("more", self.tokens, 14)))
        self.workspace_more.setIconSize(QSize(14, 14))
        self.workspace_more.setFixedSize(20, 20)
        self.workspace_more.setCursor(Qt.PointingHandCursor)
        self.workspace_more.setToolTip("工作目录操作")
        self.workspace_more.setAccessibleName("工作目录操作")
        self.workspace_more.clicked.connect(self.open_workspace_menu)
        line.addWidget(self.workspace_more)

        self.workspace_add = QPushButton()
        self.workspace_add.setObjectName("railMini")
        self.workspace_add.setIcon(QIcon(rail_icon("plus", self.tokens, 14)))
        self.workspace_add.setIconSize(QSize(14, 14))
        self.workspace_add.setFixedSize(20, 20)
        self.workspace_add.setCursor(Qt.PointingHandCursor)
        self.workspace_add.setToolTip("在当前工作区新建会话")
        self.workspace_add.setAccessibleName("在当前工作区新建会话")
        self.workspace_add.clicked.connect(self.new_session)
        line.addWidget(self.workspace_add)
        return row

    def _apply_rail_icons(self):
        for button in (
            getattr(self, "new_task_button", None),
            getattr(self, "search_button", None),
            getattr(self, "settings_button", None),
        ):
            if button is None:
                continue
            button.setIcon(QIcon(rail_icon(button._rail_kind, self.tokens)))
            button.setIconSize(QSize(16, 16))
        for button, kind in (
            (getattr(self, "workspace_more", None), "more"),
            (getattr(self, "workspace_add", None), "plus"),
        ):
            if button is not None:
                button.setIcon(QIcon(rail_icon(kind, self.tokens, 14)))
        icon = getattr(self, "workspace_icon", None)
        if icon is not None:
            icon.setPixmap(rail_icon("folder", self.tokens))

    def _apply_stylesheet(self):
        self.tokens = theme(self.theme_name)
        background = None
        if self.ui_state.background_enabled():
            asset = self.runtime.background_asset()
            if asset is not None:
                background = asset.path
        css = stylesheet(self.theme_name, self.font_scale, background=background)
        self.setStyleSheet(css)
        self._apply_rail_icons()
        self._update_send_button_width()
        if getattr(self, "input", None) is not None:
            self._resize_input()
        for attribute in (
            "memory_dialog",
            "affect_dialog",
            "persona_dialog",
            "proactive_dialog",
            "work_dialog",
            "console_dialog",
            "settings_dialog",
        ):
            dialog = getattr(self, attribute, None)
            if dialog is not None:
                dialog.setStyleSheet(css)
        if getattr(self, "settings_dialog", None) is not None:
            self.settings_dialog.refresh()
        if getattr(self, "tray", None) is not None:
            self.tray.refresh()

    def toggle_theme(self):
        other = (
            "pearl-white"
            if self.theme_name == "charcoal-pink"
            else "charcoal-pink"
        )
        self.theme_name = normalize_theme(other)
        self.ui_state.set_theme(self.theme_name)
        self._apply_stylesheet()

    def set_font_scale(self, percent):
        self.font_scale = normalize_font_scale(percent)
        self.ui_state.set_font_scale(self.font_scale)
        self._apply_stylesheet()

    def set_reduce_motion(self, enabled):
        self.reduce_motion = bool(enabled)
        self.ui_state.set_reduce_motion(self.reduce_motion)
        if getattr(self, "chat", None) is not None:
            self.chat.set_reduce_motion(self.reduce_motion)

    def _update_send_button_width(self):
        button = getattr(self, "send_button", None)
        if button is None:
            return
        button.setFixedWidth(max(78, int(round(78 * self.font_scale / 100.0))))

    def _refresh_inspector(self, focus=None):
        inspector = getattr(self, "inspector", None)
        if inspector is None:
            return
        try:
            inspector.refresh()
        except Exception:
            pass
        if focus and self.inspector_shown and inspector.isVisible():
            try:
                inspector.focus_tab(focus)
            except Exception:
                pass

    def toggle_inspector(self, checked=None):
        visible = (
            self.inspector.isVisible()
            if checked is None
            else bool(checked)
        )
        self.inspector_shown = visible
        self._inspector_auto_hidden = False
        self._inspector_compact_override = bool(
            visible and self.width() < INSPECTOR_COLLAPSE_WIDTH
        )
        self.inspector.setVisible(visible)
        self.inspector_button.setChecked(visible)
        self.ui_state.set_inspector_visible(visible)
        if visible:
            self._restore_inspector_width()
        else:
            self.splitter.setSizes([self.splitter.width(), 0])

    def toggle_sidebar(self):
        if self.width() < RAIL_COLLAPSE_WIDTH and self._rail_auto_hidden:
            if self._rail_drawer_open:
                self._close_rail_drawer()
            else:
                self._open_rail_drawer()
            return
        visible = not self.rail.isVisible()
        self.rail.setVisible(visible)
        self.rail_visible = visible
        self.rail_host.setFixedWidth(RAIL_WIDTH if visible else 0)
        self.ui_state.set(rail_visible=visible)

    def minimize_to_taskbar(self):
        if not self.isVisible():
            return
        self.save_window_geometry()
        self._restore_geometry = self.geometry()
        self.showMinimized()
        if self.tray is not None:
            self.tray.refresh()

    def changeEvent(self, event):
        # Windows can hand a frameless window back at the wrong size after it is
        # restored from the taskbar, so the pre-minimize geometry is reapplied.
        if event.type() == QEvent.WindowStateChange and not self.isMinimized():
            saved = self._restore_geometry
            if saved is not None:
                self._restore_geometry = None
                if saved.width() >= MIN_WIDTH and saved.height() >= MIN_HEIGHT:
                    self.setGeometry(saved)
        super().changeEvent(event)

    def restore_window_geometry(self):
        stored = self.ui_state.window_geometry()
        if stored is None:
            return
        x, y, width, height = stored
        screens = [
            tuple(screen.availableGeometry().getRect())
            for screen in QApplication.screens()
        ]
        self.resize(width, height)
        x, y = clamp_position(x, y, width, height, screens)
        self.move(x, y)

    def save_window_geometry(self):
        if self.isMaximized() or self.isMinimized() or self.isHidden():
            return
        self.ui_state.set_window_geometry(
            self.x(), self.y(), self.width(), self.height()
        )

    def _on_splitter_moved(self, _position, _index):
        if not self.inspector.isVisible():
            return
        width = int(self.inspector.width())
        if width < 120:
            return
        self._inspector_user_width = max(220, min(width, 420))
        self.ui_state.set_inspector_width(self._inspector_user_width)

    def _restore_inspector_width(self):
        width = int(self._inspector_user_width)
        total = max(600, int(self.splitter.width()))
        self.splitter.setSizes([max(360, total - width), width])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "inspector"):
            return
        if self._rail_drawer_open:
            self._position_rail_drawer()
        self._apply_responsive()

    def _apply_responsive(self):
        width = self.width()
        compact = width < INSPECTOR_COLLAPSE_WIDTH
        if not compact:
            self._inspector_compact_override = False
        if (
            compact
            and self.inspector.isVisible()
            and self.inspector_shown
            and not self._inspector_compact_override
        ):
            self.inspector.hide()
            self._inspector_auto_hidden = True
        elif (
            not compact
            and self._inspector_auto_hidden
            and self.inspector_shown
        ):
            self.inspector.show()
            self._inspector_auto_hidden = False
            self._restore_inspector_width()

        narrow = width < RAIL_COLLAPSE_WIDTH
        if narrow and not self._rail_auto_hidden:
            self._hide_rail_for_narrow()
        elif not narrow and self._rail_auto_hidden:
            self._restore_rail()

    def _hide_rail_for_narrow(self):
        self._close_rail_drawer()
        self._rail_auto_hidden = True
        self.rail.hide()
        self.rail_host.setFixedWidth(0)

    def _restore_rail(self):
        self._close_rail_drawer()
        self._rail_auto_hidden = False
        self.rail_host.setFixedWidth(RAIL_WIDTH if self.rail_visible else 0)
        self.rail.setVisible(self.rail_visible)

    def _open_rail_drawer(self):
        if self._rail_drawer_open:
            return
        self.rail.setParent(self.centralWidget())
        self.rail.setFixedWidth(RAIL_WIDTH)
        self.rail.show()
        self.rail.raise_()
        self._rail_drawer_open = True
        self._position_rail_drawer()

    def _close_rail_drawer(self):
        if not self._rail_drawer_open:
            return
        self.rail.hide()
        self.rail_host.layout().addWidget(self.rail)
        self.rail_host.setFixedWidth(0)
        self._rail_drawer_open = False

    def _position_rail_drawer(self):
        if not self._rail_drawer_open:
            return
        shell = self.centralWidget()
        if shell is None:
            return
        self.rail.setGeometry(0, 0, RAIL_WIDTH, shell.height())
        self.rail.raise_()

    def set_mode(self, mode):
        mode = AgentMode(mode)
        try:
            self.application.set_mode(mode)
        except SessionBusyError as error:
            self.status.setText(str(error))
            return
        self.mode = mode
        if mode == AgentMode.CHAT:
            self.chat_button.setObjectName("active")
            self.work_button.setObjectName("")
            self.set_avatar_state(status="陪伴中")
        else:
            self.work_button.setObjectName("active")
            self.chat_button.setObjectName("")
            self.set_avatar_state(status="工作中")
        for button in (self.chat_button, self.work_button):
            button.style().unpolish(button)
            button.style().polish(button)
        self.refresh_composer_hint()
        self.refresh_history()
        if getattr(self, "tray", None) is not None:
            self.tray.refresh()
        self._refresh_inspector()

    def set_avatar_state(self, status=None, mood=None):
        if status:
            self.avatar_status = status
        label = self.avatar_status
        self.status_pill.setText(label)
        idle = label in ("待机", "陪伴中", "工作中")
        self.status_pill.setObjectName("pill" if idle else "pillActive")
        self.status_pill.style().unpolish(self.status_pill)
        self.status_pill.style().polish(self.status_pill)

    def refresh_composer_hint(self):
        if getattr(self, "mode_pill", None) is None:
            return
        mode_text = "{}模式".format(mode_label(self.mode))
        self.mode_pill.setText(mode_text)
        self.mode_pill.setToolTip(
            "当前为{}；工作模式可调用需要确认的工具。".format(mode_text)
        )
        self.mode_pill.setAccessibleName("当前模式：" + mode_text)
        workspace = self.runtime.workspace()
        if workspace:
            workspace_text = "工作区 {}".format(
                _clip_text(Path(workspace).name or workspace, 14)
            )
            workspace_tip = str(workspace)
        else:
            workspace_text = "未选择工作区"
            workspace_tip = "尚未授权工作目录，点击左栏工作区的 ⋯ 选择"
        self.workspace_pill.setText(workspace_text)
        self.workspace_pill.setToolTip(workspace_tip)
        self.workspace_pill.setAccessibleName("工作区：" + workspace_tip)
        context = None
        try:
            context = self.application.ui_projection().context
        except Exception:
            context = None
        if context is not None:
            context_text = "上下文 {}%".format(context.percent)
            context_tip = "上下文 {} / {} 字符".format(
                context.context_used,
                context.context_limit,
            )
            if context.compressed:
                context_tip += "；历史已压缩"
        else:
            context_text = "上下文待命"
            context_tip = "下一轮回复后显示上下文占用"
        self.context_pill.setText(context_text)
        self.context_pill.setToolTip(context_tip)
        self.context_pill.setAccessibleName("上下文：" + context_tip)

    def _resize_input(self):
        if getattr(self, "input", None) is None:
            return
        line = max(14, self.input.fontMetrics().lineSpacing())
        frame = self.input.frameWidth() * 2 + 8
        # A hidden QTextEdit never lays its document out on its own, so the
        # wrapped height must be measured against an explicit text width.
        width = self.input.viewport().width()
        if width > 0:
            self.input.document().setTextWidth(width)
        document = int(
            self.input.document().documentLayout().documentSize().height()
        )
        minimum = line * INPUT_MIN_LINES + frame
        maximum = line * INPUT_MAX_LINES + frame
        target = max(minimum, min(maximum, document + frame))
        if self.input.height() != target:
            self.input.setFixedHeight(target)

    def _on_input_changed(self):
        self._resize_input()
        self._refresh_char_hint()
        if self._restoring_draft or not self._draft_session:
            return
        self._draft_timer.start()

    def _refresh_char_hint(self):
        if getattr(self, "composer_hint", None) is None:
            return
        count = len(self.input.toPlainText())
        warning = count > INPUT_WARN_CHARS
        error = count > INPUT_MAX_CHARS
        if not count:
            hint = "0 字 · Shift+Enter 换行"
        elif error:
            hint = "{} 字 · 超过 {} 字上限，请拆分发送".format(
                count,
                INPUT_MAX_CHARS,
            )
        elif warning:
            hint = "{} 字 · 内容较长，建议拆分".format(count)
        else:
            hint = "{} 字".format(count)
        self.composer_hint.setText(hint)
        changed = (
            self.composer_hint.property("warning") != warning
            or self.composer_hint.property("error") != error
        )
        self.composer_hint.setProperty("warning", warning)
        self.composer_hint.setProperty("error", error)
        if changed:
            self.composer_hint.style().unpolish(self.composer_hint)
            self.composer_hint.style().polish(self.composer_hint)

    def _save_draft_now(self):
        if getattr(self, "_draft_timer", None) is not None:
            self._draft_timer.stop()
        session_id = getattr(self, "_draft_session", "")
        if not session_id:
            return
        try:
            self.application.save_draft(
                session_id,
                self.input.toPlainText(),
            )
        except Exception:
            pass

    def _restore_draft(self):
        self._draft_timer.stop()
        self._draft_session = self.runtime.session_id
        try:
            content = self.application.draft(self._draft_session)
        except Exception:
            content = ""
        self._restoring_draft = True
        try:
            self.input.setPlainText(content or "")
        finally:
            self._restoring_draft = False
        self._resize_input()
        self._refresh_char_hint()

    def _set_primary_mode(self, stopping):
        self.send_button.setText("停止" if stopping else "发送")
        self.send_button.setProperty("mode", "stop" if stopping else "send")
        self.send_button.setAccessibleName(
            "停止生成" if stopping else "发送消息"
        )
        self.send_button.setToolTip(
            "停止生成（Esc）" if stopping else "发送消息（Enter）"
        )
        self.send_button.style().unpolish(self.send_button)
        self.send_button.style().polish(self.send_button)

    def primary_action(self):
        if self.active_generation is not None or self.stream_worker is not None:
            self.stop_stream()
        else:
            self.send()

    def refresh_workspace(self):
        workspace = self.runtime.workspace()
        label = (
            (Path(workspace).name or str(workspace))
            if workspace
            else "未授权"
        )
        self.workspace_name.set_full_text(label)
        tooltip = str(workspace or "尚未授权工作目录，点击右侧 … 选择")
        self.workspace_name.setToolTip(tooltip)
        self.workspace_icon.setToolTip(tooltip)
        self.refresh_composer_hint()
        self._refresh_inspector()

    def choose_workspace(self):
        selected = QFileDialog.getExistingDirectory(self, "选择允许分身读写的目录")
        if not selected:
            return
        try:
            self.application.select_workspace(selected)
            self.refresh_workspace()
            self.status.setText("工作目录已授权：{}".format(selected))
        except Exception as error:
            QMessageBox.warning(self, "目录不可用", str(error))

    def clear_workspace(self):
        try:
            self.application.clear_workspace()
        except SessionBusyError as error:
            self.status.setText(str(error))
            return
        self.refresh_workspace()
        self.status.setText("已取消工作目录授权。")

    def open_workspace_folder(self):
        workspace = self.runtime.workspace()
        if workspace:
            QDesktopServices.openUrl(QUrl.fromLocalFile(workspace))

    def open_workspace_menu(self):
        workspace = self.runtime.workspace()
        menu = QMenu(self)
        open_action = menu.addAction("打开工作目录")
        open_action.setEnabled(bool(workspace))
        open_action.triggered.connect(self.open_workspace_folder)
        menu.addAction("更换工作目录…", self.choose_workspace)
        clear_action = menu.addAction("取消授权")
        clear_action.setEnabled(bool(workspace))
        clear_action.triggered.connect(self.clear_workspace)
        menu.exec(
            self.workspace_more.mapToGlobal(
                self.workspace_more.rect().bottomLeft()
            )
        )

    def refresh_history(self, follow=False):
        self.render_history(self.stream_text, follow=follow)

    def refresh_session_list(self):
        try:
            summaries = self.runtime.session_summaries()
        except Exception as error:
            self.status.setText("会话列表读取失败：{}".format(error))
            return
        self._session_summaries = summaries
        for row in self.session_rows:
            row.setParent(None)
            row.deleteLater()
        self.session_rows = []
        for label, _rows in self.session_group_rows:
            label.setParent(None)
            label.deleteLater()
        self.session_group_rows = []
        current_group = None
        for summary in summaries:
            empty = not int(summary.get("message_count") or 0)
            if empty and summary.get("id") != self.runtime.session_id:
                continue
            group = _session_group(
                summary.get("last_at") or summary.get("updated_at")
            )
            if group != current_group:
                current_group = group
                label = QLabel(group)
                label.setObjectName("sessionGroup")
                self.session_box.insertWidget(
                    self.session_box.count() - 1, label
                )
                self.session_group_rows.append((label, []))
            row = SessionRow(
                summary,
                active=summary.get("id") == self.runtime.session_id,
            )
            row.clicked.connect(self.activate_session)
            row.pin_toggled.connect(self.set_session_pinned)
            row.unread_toggled.connect(self.set_session_unread)
            row.rename_requested.connect(self.rename_session)
            row.delete_requested.connect(self.delete_session)
            self.session_box.insertWidget(self.session_box.count() - 1, row)
            self.session_rows.append(row)
            self.session_group_rows[-1][1].append(row)
        self._filter_sessions(self.search_edit.text())

    def _filter_sessions(self, text=None):
        needle = (self.search_edit.text() if text is None else text)
        needle = needle.strip().lower()
        matched = {}
        shown = 0
        for row in self.session_rows:
            match = not needle or needle in row.search_blob
            row.setVisible(match)
            matched[row] = match
            shown += 1 if match else 0
        for label, rows in self.session_group_rows:
            label.setVisible(any(matched.get(row) for row in rows))
        self.session_empty.setText(
            "没有匹配的会话" if needle else "还没有会话"
        )
        self.session_empty.setVisible(shown == 0)

    def toggle_search(self):
        visible = not self.search_edit.isVisible()
        self.search_edit.setVisible(visible)
        if visible:
            self.search_edit.setFocus()
        else:
            self.search_edit.clear()
            self._filter_sessions("")

    def focus_search(self):
        if not self.search_edit.isVisible():
            self.search_edit.setVisible(True)
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def _session_busy(self):
        return (
            self.active_generation is not None
            or self.stream_worker is not None
            or self.application.has_active_run()
        )

    def new_session(self):
        if self._session_busy():
            self.status.setText("正在生成回复，稍后再新建会话。")
            return
        if not self.runtime.history(limit=1):
            self.input.setFocus()
            return
        try:
            session_id = self.application.create_session(AgentMode.CHAT)
        except SessionBusyError as error:
            self.status.setText(str(error))
            return
        self.activate_session(session_id)
        self.input.setFocus()

    def activate_session(self, session_id):
        session_id = str(session_id or "")
        if not session_id or session_id == self.runtime.session_id:
            return
        if self._session_busy():
            self.status.setText("正在生成回复，稍后再切换会话。")
            return
        self._save_draft_now()
        if not self.application.switch_session(session_id):
            return
        self._load_active_session()

    def _load_active_session(self):
        self.ui_state.set_active_session(self.runtime.session_id)
        self.current_proposal = None
        self.confirm_frame.hide()
        self.undo_button.hide()
        self.status.setText("")
        self.stream_text = ""
        self.turn_tools = []
        self.run_bar.reset()
        if self.search_edit.text():
            self.search_edit.clear()
        self._restore_draft()
        self.set_mode(self.runtime.current_mode())
        self.refresh_history()
        self.refresh_pending()
        self.refresh_workspace()
        self.refresh_session_list()
        self._refresh_inspector()

    def _summary_for(self, session_id):
        for summary in self._session_summaries:
            if summary.get("id") == session_id:
                return summary
        return {}

    def _summary_title(self, summary):
        title = " ".join(str(summary.get("title") or "").split())
        if title:
            return title
        topic = " ".join(str(summary.get("topic") or "").split())
        return topic or "新的对话"

    def _session_failed(self, title, message):
        self.status.setText(message)
        QMessageBox.warning(self, title, message)

    def set_session_pinned(self, session_id, pinned):
        if self._session_busy():
            self.status.setText("正在生成回复，稍后再调整会话。")
            return
        try:
            changed = self.application.set_session_pinned(session_id, pinned)
        except SessionBusyError as error:
            self.status.setText(str(error))
            return
        if not changed:
            self._session_failed("置顶失败", "找不到这条会话，可能已被删除。")
            self.refresh_session_list()
            return
        self.status.setText("已置顶会话。" if pinned else "已取消置顶。")
        self.refresh_session_list()

    def set_session_unread(self, session_id, unread):
        if self._session_busy():
            self.status.setText("正在生成回复，稍后再调整会话。")
            return
        try:
            changed = self.application.set_session_unread(session_id, unread)
        except SessionBusyError as error:
            self.status.setText(str(error))
            return
        if not changed:
            self._session_failed("标记失败", "找不到这条会话，可能已被删除。")
            self.refresh_session_list()
            return
        self.status.setText(
            "已标记为未读。" if unread else "已标记为已读。"
        )
        self.refresh_session_list()

    def rename_session(self, session_id):
        if self._session_busy():
            self.status.setText("正在生成回复，稍后再重命名会话。")
            return
        summary = self._summary_for(session_id)
        current = self._summary_title(summary)
        text, accepted = QInputDialog.getText(
            self,
            "重命名会话",
            "会话名称",
            text="" if current == "新的对话" else current,
        )
        if not accepted:
            return
        text = " ".join(str(text or "").split())
        if not text:
            self._session_failed("重命名失败", "会话名称不能为空。")
            return
        try:
            changed = self.application.rename_session(session_id, text)
        except SessionBusyError as error:
            self.status.setText(str(error))
            return
        if not changed:
            self._session_failed("重命名失败", "找不到这条会话，可能已被删除。")
            self.refresh_session_list()
            return
        self.status.setText("已重命名为「{}」。".format(text))
        self.refresh_session_list()

    def delete_session(self, session_id):
        if self._session_busy():
            self.status.setText("正在生成回复，稍后再删除会话。")
            return
        summary = self._summary_for(session_id)
        title = self._summary_title(summary)
        answer = QMessageBox.question(
            self,
            "删除会话",
            "确定删除「{}」？\n消息、记忆和草稿会一并删除，且无法撤销。".format(
                title
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        was_active = session_id == self.runtime.session_id
        try:
            deleted = self.application.delete_session(session_id)
        except SessionBusyError as error:
            self.status.setText(str(error))
            return
        if not deleted:
            self._session_failed("删除失败", "找不到这条会话，可能已被删除。")
            self.refresh_session_list()
            return
        if was_active:
            self._load_active_session()
        else:
            self.refresh_session_list()
        self.status.setText("会话「{}」已删除。".format(title))

    def _restore_active_session(self):
        stored = self.ui_state.active_session()
        if stored and stored != self.runtime.session_id:
            self.application.switch_session(stored)

    def _transcript_blocks(self):
        projection = self.application.ui_projection()
        blocks = []
        for message in projection.messages:
            mine = message.role == "user"
            blocks.append(
                {
                    "kind": "message",
                    "id": "m{}".format(message.message_id),
                    "role": "user" if mine else "assistant",
                    "name": "你" if mine else self.persona_name,
                    "text": message.text,
                    "time": _clock(message.created_at),
                    "date": _day_stamp(message.created_at),
                }
            )
        for index, tool in enumerate(self.turn_tools):
            block = {"kind": "tool", "id": "t{}".format(index)}
            block.update(tool)
            blocks.append(block)
        return blocks

    def render_history(self, preview="", follow=False):
        self.chat.set_empty_context(self.mode, self._empty_recent_task())
        self.chat.render(self._transcript_blocks(), preview, follow=follow)

    def _empty_recent_task(self):
        if self.mode != AgentMode.WORK:
            return ""
        try:
            tasks = self.runtime.work_tasks(1)
        except Exception:
            return ""
        if not tasks:
            return ""
        task = tasks[0]
        title = " ".join(str(getattr(task.card, "title", "") or "").split())
        if not title:
            return ""
        state = STATE_LABELS.get(task.state, task.state)
        return "最近任务：{}（{}）".format(_clip_text(title, 40), state)

    def empty_state_actions_keyboard_reachable(self):
        card = self.chat.findChild(QFrame, "emptyState")
        if card is None:
            return False
        buttons = card.findChildren(QPushButton)
        return bool(buttons) and all(
            button.focusPolicy() != Qt.NoFocus for button in buttons
        )

    def use_quick_start(self, prompt):
        prompt = " ".join(str(prompt or "").split())
        if not prompt:
            return
        self.input.setPlainText(prompt)
        cursor = self.input.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.input.setTextCursor(cursor)
        self.input.setFocus()

    def open_external_link(self, url):
        target = safe_url(url)
        if not target:
            self.status.setText("已忽略不安全的链接。")
            return
        if QMessageBox.question(
            self,
            "打开链接",
            "链接来自模型回复，可能不可信。\n{}\n\n要在浏览器中打开吗？".format(
                target
            ),
        ) == QMessageBox.Yes:
            QDesktopServices.openUrl(QUrl(target))

    def send(self):
        text = self.input.toPlainText().strip()
        if not text:
            return
        if len(text) > INPUT_MAX_CHARS:
            self.status.setText(
                "消息超过 {} 字上限，请拆分后再发送。".format(INPUT_MAX_CHARS)
            )
            return
        self._draft_timer.stop()
        try:
            self.application.save_draft(self.runtime.session_id, "")
        except Exception:
            pass
        self._restoring_draft = True
        try:
            self.input.clear()
        finally:
            self._restoring_draft = False
        self.start_stream(text)

    def start_stream(self, text):
        try:
            run = self.application.begin_turn(text, self.mode)
        except Exception as error:
            self.status.setText(str(error))
            return
        generation = run.run_id
        self.active_generation = generation
        self.stream_text = ""
        self.turn_tools = []
        self._set_primary_mode(True)
        self.send_button.setEnabled(True)
        self.run_bar.begin(generation)
        self.set_avatar_state(status="思考中")
        self.status.setText("正在处理...")
        self.refresh_history(follow=True)

        worker = StreamWorker(self.application, run)
        self.stream_worker = worker
        self.workers.append(worker)
        worker.event.connect(self.on_stream_event)
        worker.completed.connect(
            lambda result, value=generation: self.on_stream_done(result, value)
        )
        worker.failed.connect(
            lambda message, value=generation: self.on_stream_failed(message, value)
        )
        worker.finished.connect(lambda: self.cleanup_stream_worker(worker))
        worker.start()

    def cleanup_stream_worker(self, worker):
        if worker in self.workers:
            self.workers.remove(worker)
        if self.stream_worker is worker:
            self.stream_worker = None

    def _sync_run_steps(self):
        try:
            view = self.application.run_view()
        except Exception:
            return
        if view is not None:
            self.run_bar.set_steps(view.steps)

    def on_stream_event(self, event):
        if event.get("generation_id") != self.active_generation:
            return
        before = (self.run_bar.stage, self.run_bar.detail_label.text())
        accepted = self.run_bar.apply_event(event)
        if accepted and (self.run_bar.stage, self.run_bar.detail_label.text()) != before:
            self._sync_run_steps()
        kind = event.get("kind")
        if kind == "delta":
            self.stream_text += event.get("text") or ""
            self.render_history(self.stream_text)
        elif kind == "reset":
            self.stream_text = ""
            self.turn_tools = []
            self.render_history()
        elif kind == "tool":
            name = event.get("tool_name") or "工具"
            state = event.get("state") or "running"
            detail = {
                "name": name,
                "arguments": event.get("arguments") or {},
                "sources": event.get("sources") or [],
                "error": event.get("error") or "",
                "code": event.get("code") or "",
                "preview": event.get("preview") or "",
                "result_id": event.get("result_id") or "",
                "result_chars": event.get("result_chars") or 0,
                "result_truncated": bool(event.get("result_truncated")),
            }
            if state == "running":
                self.turn_tools.append(
                    dict(detail, state="running", summary="")
                )
                self.set_avatar_state(status="执行中")
                self.status.setText("正在调用：{}".format(name))
            else:
                summary = event.get("summary") or ""
                outcome = state if state in ("done", "error") else "done"
                for entry in reversed(self.turn_tools):
                    if entry["name"] == name and entry["state"] == "running":
                        entry.update(detail)
                        entry["state"] = outcome
                        entry["summary"] = summary
                        break
                else:
                    self.turn_tools.append(
                        dict(detail, state=outcome, summary=summary)
                    )
                self.status.setText(
                    "{} 完成{}".format(
                        name,
                        "：{}".format(summary) if summary else "",
                    )
                )
                if detail["sources"] and self.inspector_shown:
                    self._refresh_inspector(focus="sources")
            self.render_history(self.stream_text)
        elif kind == "error":
            self.status.setText(event.get("message") or "生成失败")

    def finish_stream_ui(self):
        self.active_generation = None
        self.stream_text = ""
        self.send_button.setEnabled(True)
        self._set_primary_mode(False)
        self.set_avatar_state(
            status="陪伴中" if self.mode == AgentMode.CHAT else "工作中"
        )
        self.refresh_composer_hint()
        self.refresh_session_list()
        focus = (
            "sources"
            if getattr(self.runtime, "last_tool_sources", None)
            else None
        )
        self._refresh_inspector(focus=focus)

    def on_stream_done(self, result, generation):
        if generation != self.active_generation:
            stopped = generation == self.stopped_generation
            if not stopped:
                return
            self.stopped_generation = None
            self.finish_stream_ui()
            self.run_bar.finish("cancelled", "已停止生成")
            self.status.setText("已停止生成。")
            self.refresh_history()
            if self.tray is not None:
                self.tray.refresh()
            return

        self.stream_text = ""
        self.finish_stream_ui()
        self.run_bar.finish("completed", "回复完成")
        self.status.setText("")
        self.refresh_history()
        self.refresh_workspace()
        if result.get("proposal"):
            self.show_proposal(result["proposal"])
        task = result.get("task")
        if task is not None:
            self.open_work()
            if task.state == WORK_PLAN_REVIEW:
                self.set_avatar_state(status="等待审批")
                self.run_bar.show_waiting("任务卡等待批准")
                self.status.setText("任务卡已生成，请批准、修改或拒绝。")
            elif task.state == WORK_PLAN_DISCUSSING:
                self.set_avatar_state(status="等待补充")
                self.run_bar.show_waiting("等待补充信息")
        self.refresh_affect_avatar()
        if self.tray is not None:
            self.tray.refresh()

    def on_stream_failed(self, message, generation):
        if generation != self.active_generation:
            if generation != self.stopped_generation:
                return
            self.stopped_generation = None
            self.finish_stream_ui()
            self.run_bar.finish("failed", message)
            self.status.setText("停止后未能确认保存：{}".format(message))
            self.refresh_history()
            return
        self.finish_stream_ui()
        self.run_bar.finish("failed", message)
        self.worker_failed(message)

    def stop_stream(self):
        worker = self.stream_worker
        if worker is None:
            return
        self.status.setText("正在停止...")
        self.run_bar.show_stopping()
        self.send_button.setEnabled(False)
        self.send_button.setText("停止中")
        self.stopped_generation = self.active_generation
        self.active_generation = None
        worker.cancel()

    def show_proposal(self, proposal):
        action_id = getattr(proposal, "action_id", "") or str(proposal or "")
        try:
            view = self.application.approval_view(action_id)
        except Exception as error:
            self.status.setText(str(error))
            return
        self.current_proposal = view
        self.confirm_frame.show_view(view)
        self.confirm_frame.show()
        self.undo_button.hide()
        self.run_bar.show_waiting("等待确认写入")
        self.set_avatar_state(status="等待确认")
        self.chat.scroll_to_latest()
        self._refresh_inspector(focus="execute")

    def refresh_pending(self):
        try:
            approvals = self.application.ui_projection().approvals
        except Exception:
            approvals = ()
        if approvals:
            self.show_proposal(approvals[0])
        elif (
            self.current_proposal is None
            and not self.confirm_frame.terminal
        ):
            self.confirm_frame.hide()

    def confirm_action(self):
        view = self.confirm_frame.view
        if view is None or getattr(view, "expired", False):
            return
        action_id = view.action_id
        mode = self.mode
        self.confirm_frame.set_busy()
        self.confirm_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.set_avatar_state(status="写入中")

        def done(result):
            receipt = result.get("receipt")
            self.current_proposal = None
            self.run_bar.reset()
            self.refresh_history()
            self.refresh_workspace()
            if receipt:
                text = (
                    receipt.summary
                    if receipt.status == "completed"
                    else result.get("answer", receipt.summary)
                )
                self.status.setText(text)
                self.confirm_frame.show_result(receipt, text)
                if receipt.undoable and receipt.undo_status == "available":
                    self.undo_button.show()
                else:
                    self.undo_button.hide()
            else:
                message = result.get("answer") or "执行没有返回回执。"
                self.status.setText(message)
                self.confirm_frame.show_error(message)
            self._refresh_inspector(focus="files")
            self.set_avatar_state(
                status="陪伴中" if self.mode == AgentMode.CHAT else "工作中"
            )
            if self.tray is not None:
                self.tray.refresh()

        self.run_worker(
            lambda: self.application.confirm_action(action_id, mode),
            done,
        )

    def cancel_action(self):
        view = self.confirm_frame.view
        if view is None:
            return
        try:
            self.application.cancel_action(view.action_id)
        except Exception as error:
            self.confirm_frame.show_error(error)
            return
        self.current_proposal = None
        self.run_bar.reset()
        self.refresh_history()
        self.refresh_workspace()
        self.confirm_frame.show_cancelled("已取消，没有写入文件。")
        self.status.setText("已取消，没有写入文件。")
        self._refresh_inspector(focus="execute")

    def undo_last(self):
        receipts = [
            receipt
            for receipt in self.runtime.latest_receipts(10)
            if receipt.undoable and receipt.undo_status == "available"
        ]
        if not receipts:
            self.status.setText("没有可撤销的写入。")
            return
        receipt = receipts[0]

        def done(message):
            self.status.setText(message)
            self.undo_button.hide()
            card_receipt = self.confirm_frame.receipt
            if card_receipt is not None and card_receipt.receipt_id == receipt.receipt_id:
                self.confirm_frame.show_undone(message)
            self.refresh_history()
            self._refresh_inspector(focus="files")

        self.run_worker(
            lambda: self.application.undo_receipt(receipt.receipt_id),
            done,
        )

    def refresh_affect_avatar(self):
        state = self.runtime.affect_snapshot()
        self.set_avatar_state(mood=state["agent"]["mood_label"])

    def open_memory(self):
        self.open_side_dialog("memory_dialog", MemoryDialog)

    def open_affect(self):
        self.open_side_dialog("affect_dialog", AffectDialog)

    def open_persona(self):
        self.open_side_dialog("persona_dialog", PersonaDialog)

    def open_proactive(self):
        self.open_side_dialog("proactive_dialog", ProactiveDialog)

    def open_work(self):
        dialog = self.open_side_dialog("work_dialog", WorkDialog)
        dialog.refresh()
        return dialog

    def open_console(self):
        return self.open_side_dialog(
            "console_dialog",
            ManagementConsoleDialog,
        )

    def open_settings(self):
        return self.open_side_dialog("settings_dialog", SettingsDialog)

    def poll_proactive(self):
        if self.proactive_inflight or self._session_busy():
            return
        try:
            status = self.runtime.proactive_status()
        except Exception as error:
            self.status.setText("主动检查失败：{}".format(error))
            return
        if not status["enabled"]:
            return
        self.proactive_inflight = True

        def done(result):
            self.proactive_inflight = False
            if result.get("spoke"):
                self.refresh_history()
                self.refresh_session_list()
                self.refresh_affect_avatar()
                self.set_avatar_state(status="有话说")
                self.status.setText(
                    "主动：{}".format(
                        result.get("trigger_reason") or "已送达"
                    )
                )
            elif result.get("missed"):
                self.status.setText(
                    "有 {} 条错过的提醒，已标记未读。".format(
                        len(result["missed"])
                    )
                )
            if self.proactive_dialog is not None:
                self.proactive_dialog.refresh()
            self._refresh_inspector()

        self.run_worker(self.runtime.proactive_tick, done)

    def refresh_persona(self):
        self.persona_name = (
            self.runtime.persona.name
            if getattr(self.runtime, "persona", None)
            else "数字分身"
        )
        self.setWindowTitle(self.persona_name)
        self.name_label.setText(self.persona_name)
        self.input.setPlaceholderText(
            "和{}说点什么，或在工作模式搜索并保存笔记".format(
                self.persona_name
            )
        )
        self.chat.set_assistant_name(self.persona_name)
        self.refresh_history()

    def open_side_dialog(self, attribute, dialog_type):
        dialog = getattr(self, attribute)
        if dialog is not None:
            if hasattr(dialog, "refresh_all"):
                dialog.refresh_all()
            elif attribute == "settings_dialog":
                dialog.refresh()
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            return dialog

        dialog = dialog_type(self.runtime, self)
        dialog.setWindowModality(Qt.NonModal)
        dialog.setAttribute(Qt.WA_DeleteOnClose, False)
        dialog.setStyleSheet(self.styleSheet())
        setattr(self, attribute, dialog)
        if hasattr(dialog, "persona_changed"):
            dialog.persona_changed.connect(self.refresh_persona)
        if hasattr(dialog, "spoke"):
            dialog.spoke.connect(self.refresh_history)
        if hasattr(dialog, "work_changed"):
            dialog.work_changed.connect(self.refresh_history)

        def closed(_result=0):
            if getattr(self, attribute) is dialog:
                setattr(self, attribute, None)

        dialog.finished.connect(closed)
        self.position_side_dialog(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    def position_side_dialog(self, dialog):
        screen = self.screen() or QApplication.primaryScreen()
        x, y, width, height = side_dialog_geometry(
            dialog.size(),
            self.frameGeometry(),
            screen.availableGeometry(),
        )
        dialog.resize(width, height)
        dialog.move(x, y)

    def toggle_window(self):
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()
        if self.tray is not None:
            self.tray.refresh()

    def overlay_visible(self):
        return False

    def set_overlay_visible(self, visible):
        self.status.setText("桌面浮窗需要 Web 界面（--ui web）")
        if getattr(self, "settings_dialog", None) is not None:
            self.settings_dialog.refresh()
        if self.tray is not None:
            self.tray.refresh()

    def toggle_overlay(self):
        self.set_overlay_visible(not self.overlay_visible())

    def quit_application(self):
        self.close()

    def closeEvent(self, event):
        self._save_draft_now()
        if self.proactive_timer.isActive():
            self.proactive_timer.stop()
        if self.chrome_timer.isActive():
            self.chrome_timer.stop()
        self.save_window_geometry()
        if self.stream_worker is not None:
            self.stream_worker.cancel()
            self.stream_worker.wait(1500)
        for attribute in (
            "memory_dialog",
            "affect_dialog",
            "persona_dialog",
            "proactive_dialog",
            "work_dialog",
            "console_dialog",
            "settings_dialog",
        ):
            dialog = getattr(self, attribute)
            if dialog is not None:
                dialog.close()
        if self.tray is not None:
            self.tray.close()
        event.accept()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def run_worker(self, function, on_success):
        worker = QtWorker(function)
        self.workers.append(worker)

        def cleanup():
            if worker in self.workers:
                self.workers.remove(worker)

        worker.completed.connect(on_success)
        worker.failed.connect(self.worker_failed)
        worker.finished.connect(cleanup)
        worker.start()

    def worker_failed(self, message):
        self.proactive_inflight = False
        self.send_button.setEnabled(True)
        self._set_primary_mode(False)
        self.confirm_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        if self.confirm_frame.view is not None and self.confirm_frame.isVisible():
            self.confirm_frame.show_error(message)
        self.set_avatar_state(status="遇到问题")
        self.status.setText(message)
        QMessageBox.warning(self, "操作失败", message)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.position().y() < 56:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and not self.drag_position.isNull():
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        if not self.drag_position.isNull():
            self.drag_position = QPoint()
            self.save_window_geometry()

    def eventFilter(self, watched, event):
        if (
            watched is getattr(self, "input", None)
            and event.type() == QEvent.InputMethod
        ):
            self._composing = bool(event.preeditString())
            return False
        if (
            watched is getattr(self, "search_edit", None)
            and event.type() == QEvent.KeyPress
            and event.key() == Qt.Key_Escape
        ):
            self.toggle_search()
            self.input.setFocus()
            return True
        if (
            watched is getattr(self, "input", None)
            and event.type() == QEvent.KeyPress
            and event.key() == Qt.Key_Escape
            and (
                self.active_generation is not None
                or self.stream_worker is not None
            )
        ):
            self.stop_stream()
            return True
        if (
            watched is getattr(self, "input", None)
            and event.type() == QEvent.KeyPress
            and event.key() in (Qt.Key_Return, Qt.Key_Enter)
            and not event.modifiers() & Qt.ShiftModifier
            and not self._composing
        ):
            if self.send_button.isEnabled():
                self.send()
            return True
        return super().eventFilter(watched, event)


def launch_qt(runtime):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = CompanionWindow(runtime)
    window.show()
    return app.exec()
