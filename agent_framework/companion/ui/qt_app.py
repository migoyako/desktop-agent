"""PySide6 desktop companion window."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QPoint, QThread, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..models import AgentMode, ProposalStatus
from ..policy import mode_label


class AvatarWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(150, 145)
        self.status = "待机"
        self.mood = "calm"

    def set_state(self, status=None, mood=None):
        if status:
            self.status = status
        if mood:
            self.mood = mood
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        accent = QColor("#59c3c3")
        body = QColor("#edf7f7")
        ink = QColor("#14272d")
        if self.mood in ("low", "tired"):
            accent = QColor("#8aa9b8")
        elif self.mood in ("warm", "positive"):
            accent = QColor("#f2a65a")

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(55, 195, 195, 35))
        painter.drawEllipse(18, 114, 114, 22)

        painter.setBrush(body)
        painter.setPen(QPen(QColor("#a9d8d8"), 2))
        painter.drawRoundedRect(39, 58, 72, 62, 25, 25)
        painter.drawEllipse(37, 20, 76, 68)

        painter.setPen(QPen(accent, 4, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(75, 20, 75, 8)
        painter.setBrush(accent)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(69, 3, 12, 12)

        painter.setBrush(ink)
        eye_y = 48 if self.mood not in ("tired", "low") else 52
        painter.drawEllipse(57, eye_y, 8, 10)
        painter.drawEllipse(86, eye_y, 8, 10)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(59, eye_y + 1, 2, 3)
        painter.drawEllipse(88, eye_y + 1, 2, 3)

        painter.setPen(QPen(ink, 2, Qt.SolidLine, Qt.RoundCap))
        if self.mood in ("warm", "positive"):
            painter.drawArc(65, 56, 21, 16, 0, 180 * 16)
        elif self.mood in ("low", "concerned", "tired"):
            painter.drawLine(67, 68, 84, 68)
        else:
            painter.drawArc(66, 58, 19, 12, 180 * 16, 180 * 16)

        painter.setPen(QPen(accent, 4, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(42, 75, 29, 96)
        painter.drawLine(109, 75, 121, 96)
        painter.drawLine(58, 118, 54, 130)
        painter.drawLine(92, 118, 96, 130)

        painter.setPen(QColor("#517178"))
        painter.setFont(QFont("Microsoft YaHei UI", 8))
        painter.drawText(
            0,
            134,
            150,
            14,
            Qt.AlignCenter,
            self.status,
        )


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
                    "<li>{} · {} · 评分 {}</li>".format(
                        html.escape(item["created_at"]),
                        html.escape(item.get("source") or "-"),
                        "{:.3f}".format(score)
                        if score is not None
                        else "-",
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


class CompanionWindow(QMainWindow):
    def __init__(self, runtime):
        super().__init__()
        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(False)
        self.runtime = runtime
        self.mode = AgentMode.CHAT
        self.current_proposal = None
        self.memory_dialog = None
        self.affect_dialog = None
        self.persona_dialog = None
        self.workers = []
        self.drag_position = QPoint()
        self.persona_name = (
            runtime.persona.name if getattr(runtime, "persona", None) else "数字分身"
        )
        self.setWindowTitle(self.persona_name)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.resize(500, 760)
        self.setStyleSheet(
            """
            QMainWindow, QDialog {
                background: #10181d;
                color: #e7f2f3;
                font-family: "Microsoft YaHei UI";
                font-size: 13px;
            }
            QWidget#shell {
                background: #10181d;
                border: 1px solid #29424a;
                border-radius: 14px;
            }
            QLabel#heading {
                font-size: 16px;
                font-weight: 600;
                color: #effafa;
            }
            QLabel#muted {
                color: #8da7ad;
            }
            QPushButton {
                background: #20343b;
                border: 1px solid #38545d;
                border-radius: 6px;
                padding: 6px 10px;
                color: #e8f4f4;
            }
            QPushButton:hover { background: #29444d; }
            QPushButton:pressed { background: #17272d; }
            QPushButton#primary {
                background: #3fa9a9;
                border-color: #59c3c3;
                color: #071315;
                font-weight: 600;
            }
            QPushButton#active {
                background: #59c3c3;
                color: #071315;
                font-weight: 600;
            }
            QTextBrowser, QTextEdit, QLineEdit, QTableWidget {
                background: #142228;
                border: 1px solid #2b4148;
                border-radius: 6px;
                padding: 6px;
                color: #e5f0f1;
            }
            QFrame#confirm {
                background: #182a30;
                border: 1px solid #4d7b82;
                border-radius: 8px;
            }
            QHeaderView::section {
                background: #20343b;
                color: #dfecee;
                padding: 5px;
                border: 0;
            }
            """
        )
        shell = QWidget()
        shell.setObjectName("shell")
        self.setCentralWidget(shell)
        outer = QVBoxLayout(shell)
        outer.setContentsMargins(18, 14, 18, 16)
        outer.setSpacing(10)

        top = QHBoxLayout()
        self.name_label = QLabel("{}  /  Companion".format(self.persona_name))
        self.name_label.setObjectName("heading")
        close = QPushButton("关闭")
        close.clicked.connect(self.quit_application)
        top.addWidget(self.name_label)
        top.addStretch()
        top.addWidget(close)
        outer.addLayout(top)

        avatar_row = QHBoxLayout()
        avatar_row.addStretch()
        self.avatar = AvatarWidget()
        avatar_row.addWidget(self.avatar)
        avatar_row.addStretch()
        outer.addLayout(avatar_row)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("模式"))
        self.chat_button = QPushButton("陪伴")
        self.work_button = QPushButton("工作")
        self.chat_button.clicked.connect(lambda: self.set_mode(AgentMode.CHAT))
        self.work_button.clicked.connect(lambda: self.set_mode(AgentMode.WORK))
        mode_row.addWidget(self.chat_button)
        mode_row.addWidget(self.work_button)
        mode_row.addStretch()
        self.memory_button = QPushButton("记忆")
        self.affect_button = QPushButton("情绪")
        self.persona_button = QPushButton("人设")
        self.memory_button.clicked.connect(self.open_memory)
        self.affect_button.clicked.connect(self.open_affect)
        self.persona_button.clicked.connect(self.open_persona)
        mode_row.addWidget(self.memory_button)
        mode_row.addWidget(self.affect_button)
        mode_row.addWidget(self.persona_button)
        outer.addLayout(mode_row)

        workspace_row = QHBoxLayout()
        workspace_row.addWidget(QLabel("工作目录"))
        self.workspace_label = QLabel()
        self.workspace_label.setObjectName("muted")
        self.workspace_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        choose = QPushButton("选择")
        choose.clicked.connect(self.choose_workspace)
        workspace_row.addWidget(self.workspace_label, 1)
        workspace_row.addWidget(choose)
        outer.addLayout(workspace_row)

        self.chat = QTextBrowser()
        self.chat.setOpenExternalLinks(True)
        outer.addWidget(self.chat, 1)

        input_row = QHBoxLayout()
        self.input = QTextEdit()
        self.input.setPlaceholderText(
            "和{}说点什么，或在工作模式搜索并保存笔记".format(self.persona_name)
        )
        self.input.setFixedHeight(82)
        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("primary")
        self.send_button.setFixedWidth(82)
        self.send_button.clicked.connect(self.send)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.send_button)
        outer.addLayout(input_row)

        self.confirm_frame = QFrame()
        self.confirm_frame.setObjectName("confirm")
        confirm_layout = QVBoxLayout(self.confirm_frame)
        self.confirm_plan = QLabel()
        self.confirm_plan.setWordWrap(True)
        self.confirm_plan.setTextInteractionFlags(Qt.TextSelectableByMouse)
        confirm_buttons = QHBoxLayout()
        self.confirm_button = QPushButton("允许这次写入")
        self.confirm_button.setObjectName("primary")
        self.cancel_button = QPushButton("取消")
        self.confirm_button.clicked.connect(self.confirm_action)
        self.cancel_button.clicked.connect(self.cancel_action)
        confirm_buttons.addWidget(self.confirm_button)
        confirm_buttons.addWidget(self.cancel_button)
        confirm_buttons.addStretch()
        confirm_layout.addWidget(self.confirm_plan)
        confirm_layout.addLayout(confirm_buttons)
        self.confirm_frame.hide()
        outer.addWidget(self.confirm_frame)

        status_row = QHBoxLayout()
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.undo_button = QPushButton("撤销最近写入")
        self.undo_button.clicked.connect(self.undo_last)
        self.undo_button.hide()
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.undo_button)
        outer.addLayout(status_row)

        self.set_mode(self.runtime.current_mode())
        self.refresh_workspace()
        self.refresh_history()
        self.refresh_pending()

    def set_mode(self, mode):
        mode = AgentMode(mode)
        self.mode = mode
        self.runtime.set_mode(mode)
        if mode == AgentMode.CHAT:
            self.chat_button.setObjectName("active")
            self.work_button.setObjectName("")
            self.avatar.set_state(status="陪伴中")
        else:
            self.work_button.setObjectName("active")
            self.chat_button.setObjectName("")
            self.avatar.set_state(status="工作中")
        self.chat_button.style().unpolish(self.chat_button)
        self.chat_button.style().polish(self.chat_button)
        self.work_button.style().unpolish(self.work_button)
        self.work_button.style().polish(self.work_button)

    def refresh_workspace(self):
        workspace = self.runtime.workspace()
        self.workspace_label.setText(workspace or "未授权")
        self.workspace_label.setToolTip(workspace or "")

    def choose_workspace(self):
        selected = QFileDialog.getExistingDirectory(self, "选择允许分身读写的目录")
        if not selected:
            return
        try:
            self.runtime.select_workspace(selected)
            self.refresh_workspace()
            self.status.setText("工作目录已授权：{}".format(selected))
        except Exception as error:
            QMessageBox.warning(self, "目录不可用", str(error))

    def refresh_history(self):
        pieces = []
        for message in self.runtime.history():
            if message["role"] not in ("user", "assistant"):
                continue
            role = "你" if message["role"] == "user" else "分身"
            color = "#8ed7d7" if role == "你" else "#f2c28f"
            pieces.append(
                "<p><b style='color:{}'>{}</b><br>{}</p>".format(
                    color,
                    role,
                    html.escape(message["content"]).replace("\n", "<br>"),
                )
            )
        self.chat.setHtml("".join(pieces) or "<p>还没有对话。</p>")
        self.chat.verticalScrollBar().setValue(
            self.chat.verticalScrollBar().maximum()
        )

    def send(self):
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self.send_button.setEnabled(False)
        self.avatar.set_state(status="思考中")
        self.status.setText("正在处理...")

        def done(result):
            self.send_button.setEnabled(True)
            self.avatar.set_state(status="陪伴中" if self.mode == AgentMode.CHAT else "工作中")
            self.status.setText("")
            self.refresh_history()
            self.refresh_workspace()
            if result.get("proposal"):
                self.show_proposal(result["proposal"])
            self.refresh_affect_avatar()

        self.run_worker(
            lambda: self.runtime.handle_message(text, self.mode),
            done,
        )

    def show_proposal(self, proposal):
        self.current_proposal = proposal
        self.confirm_plan.setText(proposal.user_visible_plan)
        self.confirm_frame.show()
        self.undo_button.hide()
        self.avatar.set_state(status="等待确认")

    def refresh_pending(self):
        pending = self.runtime.pending_proposals()
        if pending:
            self.show_proposal(pending[0])

    def confirm_action(self):
        if not self.current_proposal:
            return
        action_id = self.current_proposal.action_id
        mode = self.mode
        self.confirm_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.avatar.set_state(status="写入中")

        def done(result):
            self.confirm_button.setEnabled(True)
            self.cancel_button.setEnabled(True)
            self.confirm_frame.hide()
            self.current_proposal = None
            self.refresh_history()
            self.refresh_workspace()
            receipt = result.get("receipt")
            if receipt:
                text = (
                    receipt.summary
                    if receipt.status == "completed"
                    else result.get("answer", receipt.summary)
                )
                self.status.setText(text)
                if receipt.undoable and receipt.undo_status == "available":
                    self.undo_button.show()
                else:
                    self.undo_button.hide()
            self.avatar.set_state(
                status="陪伴中" if self.mode == AgentMode.CHAT else "工作中"
            )

        self.run_worker(
            lambda: self.runtime.confirm_proposal(action_id, mode),
            done,
        )

    def cancel_action(self):
        if not self.current_proposal:
            return
        action_id = self.current_proposal.action_id
        self.runtime.cancel_proposal(action_id)
        self.current_proposal = None
        self.confirm_frame.hide()
        self.refresh_history()
        self.refresh_workspace()
        self.status.setText("已取消，没有写入文件。")

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
        self.run_worker(
            lambda: self.runtime.undo_receipt(receipt.receipt_id),
            lambda message: (
                self.status.setText(message),
                self.undo_button.hide(),
                self.refresh_history(),
            ),
        )

    def refresh_affect_avatar(self):
        state = self.runtime.affect_snapshot()
        self.avatar.set_state(mood=state["agent"]["mood_label"])

    def open_memory(self):
        self.open_side_dialog("memory_dialog", MemoryDialog)

    def open_affect(self):
        self.open_side_dialog("affect_dialog", AffectDialog)

    def open_persona(self):
        self.open_side_dialog("persona_dialog", PersonaDialog)

    def refresh_persona(self):
        self.persona_name = (
            self.runtime.persona.name
            if getattr(self.runtime, "persona", None)
            else "数字分身"
        )
        self.setWindowTitle(self.persona_name)
        self.name_label.setText(
            "{}  /  Companion".format(self.persona_name)
        )
        self.input.setPlaceholderText(
            "和{}说点什么，或在工作模式搜索并保存笔记".format(
                self.persona_name
            )
        )

    def open_side_dialog(self, attribute, dialog_type):
        dialog = getattr(self, attribute)
        if dialog is not None:
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            return dialog

        dialog = dialog_type(self.runtime, self)
        dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        dialog.setWindowModality(Qt.NonModal)
        dialog.setAttribute(Qt.WA_DeleteOnClose, False)
        dialog.setStyleSheet(self.styleSheet())
        setattr(self, attribute, dialog)
        if hasattr(dialog, "persona_changed"):
            dialog.persona_changed.connect(self.refresh_persona)

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
        available = screen.availableGeometry()
        parent = self.frameGeometry()
        preferred = dialog.size()
        margin = 16
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
        dialog.resize(width, height)
        dialog.move(x, y)

    def quit_application(self):
        self.close()

    def closeEvent(self, event):
        for attribute in ("memory_dialog", "affect_dialog", "persona_dialog"):
            dialog = getattr(self, attribute)
            if dialog is not None:
                dialog.close()
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
        self.send_button.setEnabled(True)
        self.confirm_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.avatar.set_state(status="遇到问题")
        self.status.setText(message)
        QMessageBox.warning(self, "操作失败", message)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.position().y() < 65:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and not self.drag_position.isNull():
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_position = QPoint()


def launch_qt(runtime):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = CompanionWindow(runtime)
    window.show()
    return app.exec()
