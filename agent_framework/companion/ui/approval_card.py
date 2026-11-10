"""Inline transaction card for one pending file write."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..policy import mode_label
from .chat_view import WrapLabel

RISK_LABELS = {
    "safe": "只读",
    "fs-read": "读取文件",
    "fs-write": "写入文件",
    "fs-delete": "移入回收站",
    "shell": "命令执行",
    "network": "联网",
    "input-control": "控制输入",
}
CHANGE_LABELS = {
    "create": "新建文件",
    "update": "更新文件",
    "unavailable": "无法预览",
}


def _stamp(value):
    text = str(value or "").strip().replace("T", " ")
    return text[:16]


class ApprovalCard(QFrame):
    confirm_requested = Signal()
    cancel_requested = Signal()
    undo_requested = Signal()
    close_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("approvalCard")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.view = None
        self._receipt = None
        self._terminal = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 10)
        layout.setSpacing(5)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.heading = QLabel("待确认写入")
        self.heading.setObjectName("sectionTitle")
        self.status_pill = QLabel("等待确认")
        self.status_pill.setObjectName("pill")
        head.addWidget(self.heading)
        head.addWidget(self.status_pill)
        head.addStretch(1)
        layout.addLayout(head)

        meta = QGridLayout()
        meta.setHorizontalSpacing(8)
        meta.setVerticalSpacing(2)
        self.target_label = self._meta(meta, 0, "目标")
        self.workspace_label = self._meta(meta, 1, "工作区")
        self.mode_label = self._meta(meta, 2, "模式")
        self.risk_label = self._meta(meta, 2, "风险", column=2)
        self.expiry_label = self._meta(meta, 3, "有效期")
        self.undoable_label = self._meta(meta, 3, "可撤销", column=2)
        meta.setColumnStretch(1, 1)
        meta.setColumnStretch(3, 1)
        layout.addLayout(meta)

        self.plan_label = WrapLabel()
        self.plan_label.setObjectName("approvalPlan")
        layout.addWidget(self.plan_label)

        self.summary_label = QLabel()
        self.summary_label.setObjectName("diffSummary")
        layout.addWidget(self.summary_label)

        self.diff_box = QWidget()
        self.diff_layout = QVBoxLayout(self.diff_box)
        self.diff_layout.setContentsMargins(0, 2, 0, 2)
        self.diff_layout.setSpacing(0)
        layout.addWidget(self.diff_box)
        self._diff_widgets = []

        self.result_label = WrapLabel()
        self.result_label.setObjectName("approvalResult")
        self.result_label.hide()
        layout.addWidget(self.result_label)
        self.error_label = WrapLabel()
        self.error_label.setObjectName("toolError")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.confirm_button = QPushButton("允许这次写入")
        self.confirm_button.setObjectName("primary")
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("ghost")
        self.undo_button = QPushButton("撤销这次写入")
        self.undo_button.setObjectName("ghost")
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("ghost")
        self.confirm_button.clicked.connect(self.confirm_requested.emit)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        self.undo_button.clicked.connect(self.undo_requested.emit)
        self.close_button.clicked.connect(self._close)
        buttons.addWidget(self.confirm_button)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.undo_button)
        buttons.addWidget(self.close_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.undo_button.hide()
        self.close_button.hide()

    @staticmethod
    def _meta(grid, row, title, column=0):
        label = QLabel(title)
        label.setObjectName("faint")
        value = WrapLabel()
        value.setObjectName("approvalMeta")
        grid.addWidget(label, row, column)
        grid.addWidget(value, row, column + 1)
        return value

    def show_view(self, view):
        self.view = view
        self._receipt = None
        self._terminal = False
        preview = getattr(view, "preview", None)
        change = getattr(preview, "change", "unavailable")
        expired = bool(getattr(view, "expired", False))
        self.status_pill.setText("已过期" if expired else "等待确认")
        self.confirm_button.setText("允许这次写入")
        self.confirm_button.show()
        self.cancel_button.show()
        self.undo_button.hide()
        self.close_button.hide()
        self.result_label.hide()
        self.error_label.hide()
        self.target_label.setText(getattr(view, "target_summary", ""))
        self.workspace_label.setText(getattr(view, "workspace", "") or "未授权")
        self.mode_label.setText(mode_label(getattr(view, "mode", "chat")))
        risk = getattr(view, "risk", "")
        self.risk_label.setText(RISK_LABELS.get(risk, risk or "未知"))
        expiry = _stamp(getattr(view, "expires_at", "")) or "未设置"
        self.expiry_label.setText(
            "{}（已过期）".format(expiry) if expired else expiry
        )
        self.undoable_label.setText(
            "有写入前备份" if getattr(view, "reversible", False) else "不可撤销"
        )
        self.plan_label.setText(str(getattr(view, "plan", "") or ""))
        self._render_preview(preview)
        self.confirm_button.setEnabled(
            not expired and getattr(view, "status", "") == "awaiting_confirmation"
        )
        self.cancel_button.setEnabled(
            getattr(view, "status", "") == "awaiting_confirmation"
        )
        if expired:
            self.error_label.setText("提案已过期，不能确认。请重新生成提案。")
            self.error_label.show()

    def _render_preview(self, preview):
        self._clear_diff()
        if preview is None:
            self.summary_label.hide()
            self.diff_box.hide()
            return
        change = getattr(preview, "change", "unavailable")
        error = getattr(preview, "error", "")
        if change == "unavailable":
            self.summary_label.setText(CHANGE_LABELS["unavailable"])
            self.summary_label.show()
            self.diff_box.hide()
            if error:
                self.error_label.setText(error)
                self.error_label.show()
            return
        self.summary_label.setText(
            "{} · +{} / -{} 行".format(
                CHANGE_LABELS.get(change, change),
                getattr(preview, "added", 0),
                getattr(preview, "removed", 0),
            )
        )
        self.summary_label.show()
        rows = getattr(preview, "diff", ()) or ()
        for tag, text in rows:
            label = WrapLabel(("+ " if tag == "add" else "- " if tag == "del" else "  ") + text)
            label.setObjectName(
                "diffAdd"
                if tag == "add"
                else "diffDel"
                if tag == "del"
                else "diffContext"
            )
            self.diff_layout.addWidget(label)
            self._diff_widgets.append(label)
        if getattr(preview, "truncated", False):
            more = WrapLabel(
                "预览已截断，另有 {} 行未显示；确认后才会写入完整内容。".format(
                    getattr(preview, "omitted_lines", 0)
                )
            )
            more.setObjectName("muted")
            self.diff_layout.addWidget(more)
            self._diff_widgets.append(more)
        self.diff_box.setVisible(bool(self._diff_widgets))

    def _clear_diff(self):
        for widget in self._diff_widgets:
            self.diff_layout.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._diff_widgets = []

    def set_busy(self):
        self.status_pill.setText("正在执行")
        self.confirm_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.error_label.hide()

    def show_error(self, message):
        self.status_pill.setText("未执行")
        self.error_label.setText(str(message or "操作没有完成。"))
        self.error_label.show()
        self.confirm_button.setEnabled(
            not getattr(self.view, "expired", False)
            and getattr(self.view, "status", "") == "awaiting_confirmation"
        )
        self.cancel_button.setEnabled(
            getattr(self.view, "status", "") == "awaiting_confirmation"
        )

    def show_result(self, receipt, answer=""):
        self._receipt = receipt
        self._terminal = True
        self.status_pill.setText(
            "已完成" if getattr(receipt, "status", "") == "completed" else "未完成"
        )
        self.result_label.setText(
            str(answer or getattr(receipt, "summary", "") or "执行结束。")
        )
        self.result_label.show()
        self.confirm_button.hide()
        self.cancel_button.hide()
        undoable = (
            getattr(receipt, "undoable", False)
            and getattr(receipt, "undo_status", "") == "available"
        )
        self.undo_button.setVisible(bool(undoable))
        self.close_button.show()

    def show_cancelled(self, message="已取消，没有写入文件。"):
        self._terminal = True
        self.status_pill.setText("已取消")
        self.result_label.setText(str(message))
        self.result_label.show()
        self.confirm_button.hide()
        self.cancel_button.hide()
        self.undo_button.hide()
        self.close_button.show()

    def show_undone(self, message):
        self._terminal = True
        self.status_pill.setText("已撤销")
        self.result_label.setText(str(message))
        self.result_label.show()
        self.undo_button.hide()
        self.close_button.show()

    def _close(self):
        self.hide()
        self.close_requested.emit()

    @property
    def terminal(self):
        return self._terminal

    @property
    def receipt(self):
        return self._receipt

    def natural_width(self):
        widest = 0
        for label in self.findChildren(WrapLabel):
            if not label.isVisible():
                continue
            widest = max(widest, label.natural_width())
        return widest + 90
