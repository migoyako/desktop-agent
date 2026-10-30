"""Unified run strip above the composer, driven by application events."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
)

from ..app.contracts import RunStage
from .timefmt import clock as format_clock


STAGE_LABELS = {
    RunStage.PENDING.value: "准备中",
    RunStage.THINKING.value: "思考中",
    RunStage.TOOL.value: "调用工具",
    RunStage.ANSWERING.value: "生成回答",
    RunStage.WAITING_APPROVAL.value: "等待审批",
    RunStage.COMPLETED.value: "已完成",
    RunStage.CANCELLED.value: "已取消",
    RunStage.FAILED.value: "失败",
}
ACTIVE_STAGES = {
    RunStage.PENDING.value,
    RunStage.THINKING.value,
    RunStage.TOOL.value,
    RunStage.ANSWERING.value,
}
TERMINAL_STAGES = {
    RunStage.COMPLETED.value,
    RunStage.CANCELLED.value,
    RunStage.FAILED.value,
}


class RunBar(QFrame):
    stop_requested = Signal()

    VISIBLE_STEPS = 3

    def __init__(self, parent=None, show_stop=True):
        super().__init__(parent)
        self.setObjectName("runBar")
        self.setVisible(False)
        self.show_stop = bool(show_stop)
        self.run_id = ""
        self.stage = ""
        self.last_sequence = -1
        self._started = 0.0
        self._steps = []
        self._steps_open = False

        stack = QVBoxLayout(self)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(0)
        row = QHBoxLayout()
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(8)
        self.stage_label = QLabel()
        self.stage_label.setObjectName("runStage")
        self.detail_label = QLabel()
        self.detail_label.setObjectName("runDetail")
        self.detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.elapsed_label = QLabel()
        self.elapsed_label.setObjectName("runElapsed")
        self.steps_toggle = QPushButton("步骤")
        self.steps_toggle.setObjectName("runStepsToggle")
        self.steps_toggle.setCursor(Qt.PointingHandCursor)
        self.steps_toggle.setAccessibleName("本轮执行步骤")
        self.steps_toggle.clicked.connect(self._toggle_steps)
        self.steps_toggle.hide()
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("runStop")
        self.stop_button.setCursor(Qt.PointingHandCursor)
        self.stop_button.clicked.connect(self.stop_requested.emit)
        if not self.show_stop:
            self.stop_button.hide()
        row.addWidget(self.stage_label)
        row.addWidget(self.detail_label, 1)
        row.addWidget(self.elapsed_label)
        row.addWidget(self.steps_toggle)
        row.addWidget(self.stop_button)
        stack.addLayout(row)

        self.steps_label = QLabel()
        self.steps_label.setObjectName("runSteps")
        self.steps_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.steps_label.setAccessibleName("本轮执行步骤")
        self.steps_scroll = QScrollArea()
        self.steps_scroll.setObjectName("runStepsArea")
        self.steps_scroll.setFrameShape(QFrame.NoFrame)
        self.steps_scroll.setWidgetResizable(True)
        self.steps_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.steps_scroll.setWidget(self.steps_label)
        self.steps_scroll.hide()
        stack.addWidget(self.steps_scroll)

        self.clock = QTimer(self)
        self.clock.setInterval(1000)
        self.clock.timeout.connect(self._tick)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self._hide_if_done)

    def begin(self, run_id):
        self.run_id = str(run_id or "")
        self.stage = RunStage.THINKING.value
        self.last_sequence = -1
        self._started = time.monotonic()
        self._steps = []
        self._steps_open = False
        self._render_steps()
        self.hide_timer.stop()
        self.stop_button.setEnabled(True)
        self.stop_button.setVisible(self.show_stop)
        self._set_stage(self.stage, "正在准备…")
        self.setVisible(True)
        self.clock.start()

    def apply_event(self, event):
        if not isinstance(event, dict):
            return False
        if str(event.get("run_id") or "") != self.run_id:
            return False
        try:
            sequence = int(event.get("sequence", self.last_sequence + 1))
        except (TypeError, ValueError):
            sequence = self.last_sequence + 1
        if sequence <= self.last_sequence:
            return False
        self.last_sequence = sequence
        stage = str(event.get("stage") or "")
        if not stage:
            return False
        detail = self._detail_for(event)
        self._set_stage(stage, detail)
        if stage in TERMINAL_STAGES:
            self._settle(stage)
        return True

    def finish(self, stage, detail=""):
        self._set_stage(stage, detail)
        self._settle(stage)

    def set_steps(self, steps):
        """Take the collapsed timeline from the projection; the run bar never derives stages."""
        rows = []
        for step in steps or ():
            stage = str(getattr(step, "stage", "") or "")
            if not stage:
                continue
            rows.append(
                (
                    stage,
                    str(getattr(step, "detail", "") or "")
                    or str(getattr(step, "tool", "") or ""),
                    format_clock(getattr(step, "at", "")),
                )
            )
        if rows == self._steps:
            return
        self._steps = rows
        self._render_steps()

    def show_waiting(self, detail="等待你确认后才会执行"):
        self._set_stage(RunStage.WAITING_APPROVAL.value, detail)
        self.stop_button.hide()
        self.clock.stop()
        self._tick()
        self.hide_timer.stop()
        self.setVisible(True)

    def show_stopping(self):
        self.stage = RunStage.CANCELLED.value
        self.stage_label.setText("正在停止")
        self.detail_label.setText("等待当前分片收尾…")
        self.stop_button.setEnabled(False)
        self.clock.stop()
        self._tick()

    def reset(self):
        self.clock.stop()
        self.hide_timer.stop()
        self.run_id = ""
        self.stage = ""
        self.last_sequence = -1
        self._steps = []
        self._steps_open = False
        self._render_steps()
        self.setVisible(False)

    def elapsed_text(self):
        if not self._started:
            return ""
        seconds = max(0, int(time.monotonic() - self._started))
        if seconds < 60:
            return "{} 秒".format(seconds)
        return "{} 分 {:02d} 秒".format(seconds // 60, seconds % 60)

    def _tick(self):
        self.elapsed_label.setText(self.elapsed_text())

    def _toggle_steps(self):
        self._steps_open = not self._steps_open
        self._render_steps()

    def _render_steps(self):
        self.steps_toggle.setVisible(len(self._steps) >= 2)
        self.steps_toggle.setText(
            "收起步骤" if self._steps_open else "步骤 {}".format(len(self._steps))
        )
        visible = self._steps_open and bool(self._steps)
        self.steps_scroll.setVisible(visible)
        if not visible:
            self.steps_label.setText("")
            return
        self.steps_label.setText(
            "\n".join(
                "  ".join(
                    part for part in (at, STAGE_LABELS.get(stage, stage), what) if part
                )
                for stage, what, at in self._steps
            )
        )
        row_height = max(14, self.steps_label.fontMetrics().height())
        self.steps_scroll.setFixedHeight(row_height * self.VISIBLE_STEPS + 4)

    def _set_stage(self, stage, detail):
        self.stage = str(stage or "")
        label = STAGE_LABELS.get(self.stage, self.stage)
        self.stage_label.setText(label)
        self.stage_label.setProperty("stage", self.stage)
        self.stage_label.style().unpolish(self.stage_label)
        self.stage_label.style().polish(self.stage_label)
        if detail:
            self.detail_label.setText(str(detail))
        elif self.stage in (
            RunStage.THINKING.value,
            RunStage.ANSWERING.value,
        ):
            self.detail_label.setText("")
        self._tick()

    def _settle(self, stage):
        self.clock.stop()
        self.stop_button.hide()
        self._tick()
        if stage == RunStage.COMPLETED.value:
            self.hide_timer.start(4000)
        elif stage == RunStage.CANCELLED.value:
            self.hide_timer.start(3000)

    def _hide_if_done(self):
        if self.stage in (
            RunStage.COMPLETED.value,
            RunStage.CANCELLED.value,
        ):
            self.setVisible(False)

    def _detail_for(self, event):
        kind = str(event.get("kind") or "")
        stage = str(event.get("stage") or "")
        if stage == RunStage.FAILED.value:
            return str(event.get("message") or event.get("detail") or "生成失败")
        if kind == "started":
            return "正在准备…"
        if kind == "delta":
            return "正在整理回答…"
        if kind == "reset":
            return "根据工具结果重新整理…"
        if kind == "tool":
            name = str(event.get("tool_name") or "工具")
            if event.get("state") == "running":
                return "{}…".format(name)
            summary = " ".join(str(event.get("summary") or "").split())[:120]
            return "{}：{}".format(name, summary) if summary else name
        if kind == "error":
            return str(event.get("message") or "生成失败")
        if kind == "done":
            result = event.get("result") or {}
            if result.get("cancelled"):
                return "已停止生成"
            return "回复完成"
        return ""
