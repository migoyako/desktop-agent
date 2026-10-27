"""Unified management console for memory, receipts, failures and data."""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ...maintenance import (
    create_backup,
    export_data,
    integrity_report,
    list_backups,
    purge_all,
    purge_memory,
    purge_session,
    restore_backup,
    verify_backup,
)
from ...models import FORWARD_UNAVAILABLE, FORWARD_UNKNOWN


def _table(headers):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _fill(table, rows):
    table.setRowCount(len(rows))
    for row_index, values in enumerate(rows):
        for column, value in enumerate(values):
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            item = QTableWidgetItem("" if value is None else str(value))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row_index, column, item)
    table.resizeColumnsToContents()


def _selected_id(table, column=0):
    row = table.currentRow()
    if row < 0 or table.item(row, column) is None:
        return ""
    return table.item(row, column).text()


class ConsolePage(QWidget):
    title = ""

    def __init__(self, runtime, console):
        super().__init__()
        self.runtime = runtime
        self.console = console

    def refresh(self):
        return None


class MemoryPage(ConsolePage):
    title = "记忆"

    def __init__(self, runtime, console):
        super().__init__(runtime, console)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        top.addWidget(
            QLabel("来源证据、候选、召回与派生关系均可追溯；彻底删除不可撤销。")
        )
        top.addStretch()
        top.addWidget(refresh)
        layout.addLayout(top)
        self.table = _table(["ID", "类型", "内容", "状态", "保护"])
        layout.addWidget(self.table, 2)
        self.detail = QTextBrowser()
        self.detail.setMinimumHeight(170)
        layout.addWidget(self.detail, 2)
        actions = QHBoxLayout()
        correct = QPushButton("纠正")
        forget = QPushButton("忘记")
        protect = QPushButton("切换保护")
        hard_delete = QPushButton("彻底删除")
        correct.clicked.connect(self.correct)
        forget.clicked.connect(self.forget)
        protect.clicked.connect(self.toggle_protected)
        hard_delete.clicked.connect(self.hard_delete)
        actions.addWidget(correct)
        actions.addWidget(forget)
        actions.addWidget(protect)
        actions.addWidget(hard_delete)
        actions.addStretch()
        layout.addLayout(actions)
        self.table.itemSelectionChanged.connect(self.show_details)
        self._memories = []

    def refresh(self):
        self._memories = self.runtime.memories()
        _fill(
            self.table,
            [
                [
                    memory.memory_id,
                    memory.kind,
                    memory.content,
                    memory.status,
                    "是" if memory.protected else "",
                ]
                for memory in self._memories
            ],
        )
        self.show_details()

    def selected_memory(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._memories):
            return None
        return self._memories[row]

    def show_details(self):
        memory = self.selected_memory()
        if memory is None:
            self.detail.setHtml("<p>选择一条记忆查看来源与生命周期。</p>")
            return
        try:
            details = self.runtime.memory_details(memory.memory_id)
        except ValueError as error:
            self.detail.setHtml("<p>{}</p>".format(html.escape(str(error))))
            return
        record = details["memory"]
        lines = [
            "<h3>{}</h3>".format(html.escape(record.content)),
            "<p>ID：{}<br>类型：{} / 状态：{} / 保护：{}"
            "<br>规范键：{} / 证据：{} 条 / 重要度：{:.2f}</p>".format(
                html.escape(record.memory_id),
                html.escape(record.kind),
                html.escape(record.status),
                "是" if record.protected else "否",
                html.escape(record.canonical_key or "-"),
                record.evidence_count,
                record.importance,
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

    def correct(self):
        memory = self.selected_memory()
        if memory is None:
            QMessageBox.information(self, "记忆", "请先选择一条记忆。")
            return
        replacement, ok = QInputDialog.getMultiLineText(
            self,
            "纠正记忆",
            "新的内容：",
            memory.content,
        )
        if ok and replacement.strip():
            self.runtime.correct_memory(memory.memory_id, replacement.strip())
            self.refresh()
            self.console.refresh_all()

    def forget(self):
        memory = self.selected_memory()
        if memory is None:
            QMessageBox.information(self, "记忆", "请先选择一条记忆。")
            return
        if QMessageBox.question(
            self,
            "忘记记忆",
            "确认忘记选中的记忆？之后不会再参与召回。",
        ) == QMessageBox.Yes:
            self.runtime.forget_memory(memory.memory_id)
            self.refresh()
            self.console.refresh_all()

    def toggle_protected(self):
        memory = self.selected_memory()
        if memory is None:
            QMessageBox.information(self, "记忆", "请先选择一条记忆。")
            return
        self.runtime.set_memory_protected(
            memory.memory_id,
            not memory.protected,
        )
        self.refresh()
        self.console.refresh_all()

    def hard_delete(self):
        memory = self.selected_memory()
        if memory is None:
            QMessageBox.information(self, "记忆", "请先选择一条记忆。")
            return
        if QMessageBox.question(
            self,
            "彻底删除记忆",
            "彻底删除会同时移除来源证据、候选、召回引用与派生记忆，"
            "且不会留下遗忘墓碑，无法撤销。确定继续吗？",
        ) != QMessageBox.Yes:
            return
        result = purge_memory(self.runtime.store, memory.memory_id)
        self.refresh()
        self.console.refresh_all()
        QMessageBox.information(
            self,
            "彻底删除记忆",
            "已删除 {} 条记忆（含派生），更新召回 {} 条，删除召回 {} 条。".format(
                result["deleted"],
                result["recall_updated"],
                result["recall_deleted"],
            ),
        )


class RecallPage(ConsolePage):
    title = "召回"

    def __init__(self, runtime, console):
        super().__init__(runtime, console)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("最多"))
        self.limit = QComboBox()
        self.limit.addItems(["20", "50", "100"])
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        top.addWidget(self.limit)
        top.addStretch()
        top.addWidget(refresh)
        layout.addLayout(top)
        self.table = _table(
            ["时间", "来源", "查询哈希", "记忆数", "已采用", "延迟(ms)"]
        )
        layout.addWidget(self.table, 2)
        self.detail = QTextBrowser()
        layout.addWidget(self.detail, 2)
        self.table.itemSelectionChanged.connect(self.show_details)
        self._rows = []

    def refresh(self):
        limit = int(self.limit.currentText())
        self._rows = self.runtime.recall_logs(limit)
        rows = []
        for item in self._rows:
            try:
                facts = json.loads(item.get("fact_ids") or "[]")
            except ValueError:
                facts = []
            rows.append(
                [
                    item.get("created_at", ""),
                    item.get("source", ""),
                    (item.get("query_hash") or "")[:16],
                    len(facts),
                    "是" if item.get("used_in_reply") else "否",
                    "{:.1f}".format(float(item.get("latency_ms") or 0.0)),
                ]
            )
        _fill(self.table, rows)
        self.show_details()

    def show_details(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            self.detail.setHtml(
                "<p>选择一次召回查看命中的记忆与评分。</p>"
            )
            return
        item = self._rows[row]
        try:
            fact_ids = json.loads(item.get("fact_ids") or "[]")
            scores = json.loads(item.get("scores") or "[]")
        except ValueError:
            fact_ids, scores = [], []
        memories = self.runtime.store.get_memories_by_ids(fact_ids)
        lines = [
            "<p>召回 ID：{}<br>查询哈希：{}<br>来源：{} / 延迟 {:.1f}ms"
            " / {}</p>".format(
                html.escape(item.get("recall_id", "")),
                html.escape(item.get("query_hash", "")),
                html.escape(item.get("source") or "-"),
                float(item.get("latency_ms") or 0.0),
                "已采用" if item.get("used_in_reply") else "未采用",
            )
        ]
        if fact_ids:
            lines.append("<h4>命中记忆</h4><ul>")
            for index, memory_id in enumerate(fact_ids):
                score = scores[index] if index < len(scores) else None
                memory = memories.get(memory_id)
                lines.append(
                    "<li>{} · 评分 {} · {}</li>".format(
                        html.escape(memory_id),
                        "{:.3f}".format(score)
                        if score is not None
                        else "-",
                        html.escape(
                            memory.content
                            if memory is not None
                            else "（记忆已删除）"
                        ),
                    )
                )
            lines.append("</ul>")
        else:
            lines.append("<p>本次召回没有命中记忆。</p>")
        self.detail.setHtml("".join(lines))


class CandidatePage(ConsolePage):
    title = "候选"

    def __init__(self, runtime, console):
        super().__init__(runtime, console)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("状态"))
        self.status = QComboBox()
        self.status.addItems(["全部", "pending", "accepted", "superseded"])
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        top.addWidget(self.status)
        top.addStretch()
        top.addWidget(refresh)
        layout.addLayout(top)
        self.table = _table(
            ["更新时间", "状态", "规范键", "内容", "评分", "原因", "记忆 ID"]
        )
        layout.addWidget(self.table)
        self.summary = QLabel()
        self.summary.setObjectName("muted")
        layout.addWidget(self.summary)
        self._rows = []

    def refresh(self):
        selected = self.status.currentText()
        statuses = None if selected == "全部" else [selected]
        self._rows = self.runtime.store.list_memory_candidates(
            self.runtime.session_id,
            statuses=statuses,
            limit=200,
        )
        _fill(
            self.table,
            [
                [
                    item.get("updated_at", ""),
                    item.get("status", ""),
                    item.get("canonical_key", ""),
                    item.get("content", ""),
                    "{:.2f}".format(float(item.get("score") or 0.0)),
                    item.get("reason", ""),
                    item.get("memory_id", ""),
                ]
                for item in self._rows
            ],
        )
        self.summary.setText(
            "共 {} 条候选；accepted 表示已晋升，superseded 表示被新值取代。"
            .format(len(self._rows))
        )


class ExecutionPage(ConsolePage):
    title = "执行与审计"
    KINDS = ("写入提案", "写入回执", "工作任务", "技能审计")
    HEADERS = {
        "写入提案": ["时间", "状态", "风险", "工具", "目标", "动作 ID"],
        "写入回执": ["时间", "状态", "摘要", "可撤销", "撤销状态", "回执 ID"],
        "工作任务": ["更新时间", "状态", "审批", "标题", "执行器", "任务 ID"],
        "技能审计": ["时间", "技能", "动作", "结果", "模式", "详情"],
    }

    def __init__(self, runtime, console):
        super().__init__(runtime, console)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.kind = QComboBox()
        self.kind.addItems(list(self.KINDS))
        self.kind.currentTextChanged.connect(self.refresh)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        top.addWidget(self.kind)
        top.addStretch()
        top.addWidget(refresh)
        layout.addLayout(top)
        self.table = _table(self.HEADERS[self.KINDS[0]])
        layout.addWidget(self.table, 2)
        self.detail = QTextBrowser()
        layout.addWidget(self.detail, 1)
        self.table.itemSelectionChanged.connect(self.show_details)
        self._rows = []
        self._headers = list(self.HEADERS[self.KINDS[0]])

    def refresh(self):
        kind = self.kind.currentText() or self.KINDS[0]
        headers = list(self.HEADERS[kind])
        if headers != self._headers:
            self.table.setColumnCount(len(headers))
            self.table.setHorizontalHeaderLabels(headers)
            self.table.clearContents()
            self._headers = headers
        self._rows = []
        if kind == "写入提案":
            for proposal in self.runtime.store.list_proposals(
                self.runtime.session_id,
                limit=100,
            ):
                self._rows.append(
                    {
                        "显示": [
                            proposal.created_at,
                            proposal.status.value,
                            proposal.risk.value,
                            proposal.tool_name,
                            proposal.target_summary,
                            proposal.action_id,
                        ],
                        "详情": {
                            "user_visible_plan": proposal.user_visible_plan,
                            "idempotency_key": proposal.idempotency_key,
                            "confirmation_digest": proposal.confirmation_digest,
                        },
                    }
                )
        elif kind == "写入回执":
            for receipt in self.runtime.store.list_receipts(
                self.runtime.session_id,
                limit=100,
            ):
                self._rows.append(
                    {
                        "显示": [
                            receipt.created_at,
                            receipt.status,
                            receipt.summary,
                            "是" if receipt.undoable else "否",
                            receipt.undo_status,
                            receipt.receipt_id,
                        ],
                        "详情": {
                            "action_id": receipt.action_id,
                            "error": receipt.error,
                            "result": receipt.result,
                        },
                    }
                )
        elif kind == "工作任务":
            for task in self.runtime.work_tasks(limit=100):
                self._rows.append(
                    {
                        "显示": [
                            task.updated_at,
                            task.state,
                            task.approval_status,
                            task.card.title,
                            task.card.executor,
                            task.task_id,
                        ],
                        "详情": {
                            "goal": task.card.goal,
                            "spoken_summary": task.card.spoken_summary,
                            "metadata": task.metadata,
                        },
                    }
                )
        else:
            for item in self.runtime.store.list_skill_audits(
                self.runtime.session_id,
                limit=100,
            ):
                self._rows.append(
                    {
                        "显示": [
                            item.get("created_at", ""),
                            item.get("skill_name", ""),
                            item.get("action", ""),
                            item.get("outcome", ""),
                            item.get("mode", ""),
                            item.get("detail", ""),
                        ],
                        "详情": item,
                    }
                )
        _fill(self.table, [item["显示"] for item in self._rows])
        self.show_details()

    def show_details(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            self.detail.setHtml("<p>选择一行查看原始记录。</p>")
            return
        self.detail.setHtml(
            "<pre>{}</pre>".format(
                html.escape(
                    json.dumps(
                        self._rows[row]["详情"],
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    )
                )
            )
        )


class AffectPage(ConsolePage):
    title = "情绪"

    def __init__(self, runtime, console):
        super().__init__(runtime, console)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        top.addWidget(self.summary, 1)
        top.addWidget(refresh)
        layout.addLayout(top)
        self.table = _table(
            ["时间", "对象", "情绪", "效价", "唤醒", "能量", "置信度", "来源"]
        )
        layout.addWidget(self.table)

    def refresh(self):
        state = self.runtime.affect_snapshot()
        self.summary.setText(
            "用户：{}（{}）/ 角色：{}（{}）".format(
                state["user"]["mood_label"],
                state["user"]["source"],
                state["agent"]["mood_label"],
                state["agent"]["source"],
            )
        )
        records = self.runtime.store.list_affect_states(
            self.runtime.session_id,
            limit=100,
        )
        _fill(
            self.table,
            [
                [
                    record.created_at,
                    record.target,
                    record.mood_label,
                    "{:.2f}".format(record.valence),
                    "{:.2f}".format(record.arousal),
                    "-"
                    if record.energy is None
                    else "{:.2f}".format(record.energy),
                    "{:.2f}".format(record.confidence),
                    record.source,
                ]
                for record in records
            ],
        )


class FailurePage(ConsolePage):
    title = "失败记录"
    FAILED_PROPOSAL_STATUSES = {"failed", "not_executed", "unknown"}
    FAILED_RECEIPT_STATUSES = {"failed", "unknown"}
    FAILED_WORK_STATUSES = {FORWARD_UNKNOWN, FORWARD_UNAVAILABLE}

    def __init__(self, runtime, console):
        super().__init__(runtime, console)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        note = QLabel(
            "只读派生页：从提案、回执、任务、技能审计、提醒与主动事件生成，"
            "不额外记录失败日志。"
        )
        note.setWordWrap(True)
        note.setObjectName("muted")
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        top.addWidget(note, 1)
        top.addWidget(refresh)
        layout.addLayout(top)
        self.table = _table(["时间", "类型", "摘要", "追踪 ID", "详情"])
        layout.addWidget(self.table)
        self._rows = []

    def refresh(self):
        rows = []
        for proposal in self.runtime.store.list_proposals(
            self.runtime.session_id,
            limit=200,
        ):
            if proposal.status.value not in self.FAILED_PROPOSAL_STATUSES:
                continue
            rows.append(
                {
                    "time": proposal.updated_at,
                    "type": "写入提案",
                    "summary": "{} · {}".format(
                        proposal.status.value,
                        proposal.target_summary,
                    ),
                    "trace": proposal.action_id,
                    "detail": proposal.user_visible_plan,
                }
            )
        for receipt in self.runtime.store.list_receipts(
            self.runtime.session_id,
            limit=200,
        ):
            if receipt.status not in self.FAILED_RECEIPT_STATUSES:
                continue
            rows.append(
                {
                    "time": receipt.finished_at or receipt.created_at,
                    "type": "写入回执",
                    "summary": "{} · {}".format(
                        receipt.status,
                        receipt.summary,
                    ),
                    "trace": receipt.receipt_id,
                    "detail": receipt.error,
                }
            )
        for task in self.runtime.work_tasks(limit=200):
            receipt = self.runtime.store.get_work_receipt_for_task(
                task.task_id
            )
            if receipt is None or receipt.status not in self.FAILED_WORK_STATUSES:
                continue
            rows.append(
                {
                    "time": receipt.updated_at,
                    "type": "工作回执",
                    "summary": "{} · {}".format(
                        receipt.status,
                        receipt.summary,
                    ),
                    "trace": receipt.receipt_id,
                    "detail": receipt.detail,
                }
            )
        for item in self.runtime.store.list_skill_audits(
            self.runtime.session_id,
            limit=200,
        ):
            if item.get("outcome") == "ok":
                continue
            rows.append(
                {
                    "time": item.get("created_at", ""),
                    "type": "技能审计",
                    "summary": "{} · {}".format(
                        item.get("skill_name", ""),
                        item.get("outcome", ""),
                    ),
                    "trace": item.get("audit_id", ""),
                    "detail": item.get("detail", ""),
                }
            )
        for reminder in self.runtime.store.list_reminders(
            self.runtime.session_id,
            status="missed",
            limit=100,
        ):
            rows.append(
                {
                    "time": reminder.updated_at,
                    "type": "错过的提醒",
                    "summary": reminder.content,
                    "trace": reminder.reminder_id,
                    "detail": "到期时间 {}".format(reminder.due_at),
                }
            )
        for event in self.runtime.proactive_events(limit=100):
            if event.status != "missed":
                continue
            rows.append(
                {
                    "time": event.created_at,
                    "type": "主动事件",
                    "summary": event.trigger_reason,
                    "trace": event.event_id,
                    "detail": event.reason,
                }
            )
        rows.sort(key=lambda item: item["time"], reverse=True)
        self._rows = rows[:200]
        _fill(
            self.table,
            [
                [
                    item["time"],
                    item["type"],
                    item["summary"],
                    item["trace"],
                    item["detail"],
                ]
                for item in self._rows
            ],
        )


class SettingsPage(ConsolePage):
    title = "设置与数据"

    def __init__(self, runtime, console):
        super().__init__(runtime, console)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("本地数据、备份与导出；删除操作不可撤销。"))
        top.addStretch()
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        top.addWidget(refresh)
        layout.addLayout(top)
        self.info = QTextBrowser()
        self.info.setMinimumHeight(150)
        layout.addWidget(self.info, 1)
        self.backup_table = _table(
            ["文件", "创建时间", "schema", "消息数", "状态"]
        )
        layout.addWidget(self.backup_table, 1)
        actions = QHBoxLayout()
        integrity = QPushButton("完整性检查")
        backup = QPushButton("立即备份")
        verify = QPushButton("校验所选备份")
        restore = QPushButton("恢复所选备份")
        export = QPushButton("导出数据")
        rebuild = QPushButton("重建 FTS")
        delete_session = QPushButton("删除当前会话")
        delete_all = QPushButton("彻底删除全部数据")
        integrity.clicked.connect(self.check_integrity)
        backup.clicked.connect(self.on_backup)
        verify.clicked.connect(self.verify_selected)
        restore.clicked.connect(self.restore_selected)
        export.clicked.connect(self.on_export)
        rebuild.clicked.connect(self.rebuild_fts)
        delete_session.clicked.connect(self.delete_current_session)
        delete_all.clicked.connect(self.delete_everything)
        for button in (
            integrity,
            backup,
            verify,
            restore,
            export,
            rebuild,
            delete_session,
            delete_all,
        ):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.backup_entries = []

    def refresh(self):
        store = self.runtime.store
        report = integrity_report(store.database_path)
        try:
            proactive = self.runtime.proactive_status()
            proactive_text = "enabled={} daily_limit={}".format(
                "yes" if proactive.get("enabled") else "no",
                proactive.get("daily_limit", "-"),
            )
        except Exception as error:
            proactive_text = "unavailable: {}".format(error)
        try:
            skills = self.runtime.skills_status()
            if isinstance(skills, dict):
                skill_text = "available={} invalid={}".format(
                    len(skills.get("available") or []),
                    len(skills.get("issues") or []),
                )
            else:
                skill_text = "valid={}".format(len(skills))
        except Exception as error:
            skill_text = "unavailable: {}".format(error)
        workspace = self.runtime.workspace() or "未授权"
        lines = [
            "<p>数据目录：{}<br>数据库：{}<br>会话：{} / 模式：{} / 工作目录：{}"
            "</p>".format(
                html.escape(str(self.runtime.data_dir)),
                html.escape(str(store.database_path)),
                html.escape(self.runtime.session_id),
                html.escape(self.runtime.current_mode().value),
                html.escape(str(workspace)),
            ),
            "<p>schema：v{}（{}） / integrity={} / 外键错误={}"
            "<br>FTS：{}（{} 行 / 记忆 {} 行） / 缺失索引：{}"
            "<br>大小：{} 字节 / WAL：{} 字节<br>"
            "计数：消息 {} / 记忆 {} / 提案 {} / 回执 {} / 任务 {}"
            "</p>".format(
                report["user_version"],
                report["version_state"],
                html.escape(report["integrity"]),
                report["foreign_key_violations"],
                "ready" if report["fts_present"] else "missing",
                report["fts_rows"],
                report["memory_rows"],
                html.escape(
                    "、".join(report["missing_indexes"]) or "无"
                ),
                report["size_bytes"],
                report["wal_bytes"],
                report["counts"].get("messages", 0),
                report["counts"].get("memories", 0),
                report["counts"].get("action_proposals", 0),
                report["counts"].get("action_receipts", 0),
                report["counts"].get("work_tasks", 0),
            ),
            "<p>主动：{}<br>技能：{}<br>迁移前备份：{}</p>".format(
                html.escape(proactive_text),
                html.escape(skill_text),
                html.escape(store.migration_backup_path or "无"),
            ),
        ]
        if store.migration_backup_error:
            lines.append(
                "<p>迁移前备份失败：{}</p>".format(
                    html.escape(store.migration_backup_error)
                )
            )
        self.info.setHtml("".join(lines))
        self.backup_entries = list_backups(self.runtime.data_dir)
        _fill(
            self.backup_table,
            [
                [
                    entry["name"],
                    entry["created_at"],
                    entry["schema_version"],
                    entry["messages"],
                    "可读" if entry["readable"] else "不可读",
                ]
                for entry in self.backup_entries
            ],
        )

    def selected_backup(self):
        row = self.backup_table.currentRow()
        if row < 0 or row >= len(self.backup_entries):
            return None
        return self.backup_entries[row]

    def check_integrity(self):
        report = integrity_report(self.runtime.store.database_path)
        detail = (
            "integrity={}\nschema=v{} ({})\n外键错误={}\n"
            "FTS：{}（{} / {}）\n缺失索引：{}\n整体：{}"
        ).format(
            report["integrity"],
            report["user_version"],
            report["version_state"],
            report["foreign_key_violations"],
            "ready" if report["fts_present"] else "missing",
            report["fts_rows"],
            report["memory_rows"],
            "、".join(report["missing_indexes"]) or "无",
            "通过" if report["ok"] else "需要处理",
        )
        QMessageBox.information(self, "完整性检查", detail)

    def backup_now(self):
        info = create_backup(self.runtime.data_dir, reason="console")
        self.refresh()
        return info

    def on_backup(self):
        try:
            info = self.backup_now()
        except Exception as error:
            QMessageBox.warning(self, "备份失败", str(error))
            return
        QMessageBox.information(
            self,
            "备份完成",
            "已备份到：\n{}".format(info["path"]),
        )

    def verify_selected(self):
        entry = self.selected_backup()
        if entry is None:
            QMessageBox.information(self, "备份", "请先选择一个备份。")
            return
        result = verify_backup(entry["path"])
        detail = (
            "文件：{}\n状态：{}\nschema：v{}\n大小：{} 字节\n消息：{}"
        ).format(
            Path(entry["path"]).name,
            "有效" if result["valid"] else "无效：{}".format(
                result["reason"] or "未知原因"
            ),
            result["schema_version"],
            result["size_bytes"],
            result["counts"].get("messages", "-"),
        )
        QMessageBox.information(self, "校验备份", detail)

    def restore_selected(self):
        entry = self.selected_backup()
        if entry is None:
            QMessageBox.information(self, "恢复备份", "请先选择一个备份。")
            return
        result = verify_backup(entry["path"])
        if not result["valid"]:
            QMessageBox.warning(
                self,
                "恢复备份",
                "备份无效：{}".format(result["reason"] or "未知原因"),
            )
            return
        if QMessageBox.question(
            self,
            "恢复备份",
            "将用所选备份覆盖当前数据库。恢复前会自动备份当前状态。"
            "恢复后需要重启应用。确定继续吗？",
        ) != QMessageBox.Yes:
            return
        try:
            info = restore_backup(
                self.runtime.data_dir,
                entry["path"],
            )
        except Exception as error:
            QMessageBox.warning(self, "恢复备份", str(error))
            return
        self.console.mark_restart("数据库已恢复，请重启应用。")
        self.refresh()
        QMessageBox.information(
            self,
            "恢复备份",
            "已恢复 schema v{}。\n恢复前备份：{}".format(
                info["schema_version"],
                info["safety_backup"] or "无",
            ),
        )

    def export_to(self, output_path=None):
        if output_path is None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            default = (
                Path(self.runtime.data_dir)
                / "exports"
                / "companion-export-{}.zip".format(stamp)
            )
            output_path, _ = QFileDialog.getSaveFileName(
                self,
                "导出数据",
                str(default),
                "Zip (*.zip)",
            )
            if not output_path:
                return None
        return export_data(
            self.runtime.data_dir,
            output_path=output_path,
        )

    def on_export(self):
        try:
            info = self.export_to()
        except Exception as error:
            QMessageBox.warning(self, "导出失败", str(error))
            return
        if info is None:
            return
        QMessageBox.information(
            self,
            "导出完成",
            "文件：{}\n大小：{} 字节\n工具结果：{} 个".format(
                info["path"],
                info["size_bytes"],
                info["tool_results"],
            ),
        )

    def rebuild_fts(self):
        ok = self.runtime.store.rebuild_memory_fts()
        self.refresh()
        QMessageBox.information(
            self,
            "重建 FTS",
            "索引已重建。" if ok else "重建失败，召回会降级为关键词。",
        )

    def delete_current_session(self):
        if QMessageBox.question(
            self,
            "删除当前会话",
            "将彻底删除会话 {} 的消息、记忆、提案、回执、任务、提醒、"
            "情绪与审计记录，并清理该会话的工具结果。此操作不可撤销。"
            "确定继续吗？".format(self.runtime.session_id),
        ) != QMessageBox.Yes:
            return
        result = purge_session(
            self.runtime.store,
            self.runtime.session_id,
            data_dir=self.runtime.data_dir,
        )
        self.console.mark_restart("当前会话已删除，请重启应用。")
        self.refresh()
        self.console.refresh_all()
        QMessageBox.information(
            self,
            "删除当前会话",
            "已删除消息 {} 条、记忆 {} 条、工具结果 {} 个。".format(
                result["counts"].get("messages", 0),
                result["counts"].get("memories", 0),
                result["tool_results"]["deleted"],
            ),
        )

    def delete_everything(self):
        text, ok = QInputDialog.getText(
            self,
            "彻底删除全部数据",
            "这会删除所有会话、记忆、提案、回执、任务、审计与工具结果。"
            "请输入 删除全部 以确认：",
        )
        if not ok or text.strip() != "删除全部":
            return
        include_backups = QMessageBox.question(
            self,
            "备份与导出",
            "是否同时删除本地备份（含文件覆盖前快照）与导出文件？"
            "选择“否”会保留它们。",
        ) == QMessageBox.Yes
        result = purge_all(
            self.runtime.store,
            data_dir=self.runtime.data_dir,
            include_backups=include_backups,
            include_exports=include_backups,
        )
        self.console.mark_restart("全部数据已删除，请重启应用。")
        self.refresh()
        self.console.refresh_all()
        QMessageBox.information(
            self,
            "彻底删除全部数据",
            "已删除会话 {} 个、消息 {} 条，清理工具结果 {} 个、文件 {} 个。".format(
                result["counts"].get("sessions", 0),
                result["counts"].get("messages", 0),
                result["tool_results"]["deleted"],
                result["removed_files"],
            ),
        )


class ManagementConsoleDialog(QDialog):
    PAGE_TYPES = (
        MemoryPage,
        RecallPage,
        CandidatePage,
        ExecutionPage,
        AffectPage,
        FailurePage,
        SettingsPage,
    )

    def __init__(self, runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.needs_restart = False
        self.setWindowTitle("管理台 · 数据与自检")
        self.resize(1040, 720)
        layout = QVBoxLayout(self)
        header = QLabel("管理台")
        header.setObjectName("heading")
        layout.addWidget(header)
        self.tabs = QTabWidget()
        self.pages = []
        for page_type in self.PAGE_TYPES:
            page = page_type(runtime, self)
            self.pages.append(page)
            self.tabs.addTab(page, page.title)
        layout.addWidget(self.tabs, 1)
        bottom = QHBoxLayout()
        self.restart_label = QLabel(
            "数据变更后请重启应用，避免界面继续使用旧状态。"
        )
        self.restart_label.setObjectName("muted")
        self.restart_label.setWordWrap(True)
        refresh = QPushButton("刷新全部")
        refresh.clicked.connect(self.refresh_all)
        close = QPushButton("关闭")
        close.clicked.connect(self.close)
        bottom.addWidget(self.restart_label, 1)
        bottom.addWidget(refresh)
        bottom.addWidget(close)
        layout.addLayout(bottom)
        self.refresh_all()

    def page(self, page_type):
        for page in self.pages:
            if isinstance(page, page_type):
                return page
        return None

    def refresh_all(self):
        for page in self.pages:
            page.refresh()
        self.restart_label.setText(
            "数据已变更，请重启应用后再继续。" if self.needs_restart
            else "数据变更后请重启应用，避免界面继续使用旧状态。"
        )

    def mark_restart(self, message):
        self.needs_restart = True
        self.restart_label.setText(message)
