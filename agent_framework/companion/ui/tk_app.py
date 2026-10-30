"""Tkinter compatibility host used when PySide6 is not installed."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from ..models import AgentMode
from ..proactive import status_label
from ..work import FORWARD_LABELS, STATE_LABELS


BG = "#10181d"
PANEL = "#142228"
PANEL_2 = "#1b2c33"
LINE = "#34515a"
TEXT = "#e7f2f3"
MUTED = "#8da7ad"
ACCENT = "#59c3c3"
WARM = "#f2a65a"


class TkAvatar(tk.Canvas):
    def __init__(self, parent):
        super().__init__(
            parent,
            width=150,
            height=145,
            bg=BG,
            highlightthickness=0,
        )
        self.status = "待机"
        self.mood = "calm"
        self.draw()

    def set_state(self, status=None, mood=None):
        if status:
            self.status = status
        if mood:
            self.mood = mood
        self.draw()

    def draw(self):
        self.delete("all")
        accent = ACCENT
        if self.mood in ("low", "tired"):
            accent = "#8aa9b8"
        elif self.mood in ("warm", "positive"):
            accent = WARM
        self.create_oval(25, 116, 125, 134, fill="#182a30", outline="")
        self.create_rounded_rect = None
        self.create_oval(37, 20, 113, 88, fill="#edf7f7", outline="#a9d8d8", width=2)
        self.create_rectangle(39, 58, 111, 118, fill="#edf7f7", outline="")
        self.create_arc(
            39,
            90,
            111,
            122,
            start=180,
            extent=180,
            style="arc",
            outline="#a9d8d8",
        )
        self.create_oval(38, 71, 52, 107, fill="#edf7f7", outline="#a9d8d8")
        self.create_oval(98, 71, 112, 107, fill="#edf7f7", outline="#a9d8d8")
        self.create_line(75, 20, 75, 8, fill=accent, width=4)
        self.create_oval(69, 3, 81, 15, fill=accent, outline="")
        eye_y = 49 if self.mood not in ("tired", "low") else 53
        self.create_oval(57, eye_y, 65, eye_y + 10, fill="#14272d", outline="")
        self.create_oval(86, eye_y, 94, eye_y + 10, fill="#14272d", outline="")
        self.create_oval(59, eye_y + 2, 61, eye_y + 5, fill="white", outline="")
        self.create_oval(88, eye_y + 2, 90, eye_y + 5, fill="white", outline="")
        if self.mood in ("warm", "positive"):
            self.create_arc(65, 55, 86, 72, start=0, extent=-180, outline="#14272d", width=2)
        elif self.mood in ("low", "concerned", "tired"):
            self.create_line(67, 68, 84, 68, fill="#14272d", width=2)
        else:
            self.create_arc(66, 58, 85, 71, start=180, extent=180, outline="#14272d", width=2)
        self.create_line(43, 77, 30, 98, fill=accent, width=4)
        self.create_line(108, 77, 120, 98, fill=accent, width=4)
        self.create_line(58, 116, 54, 130, fill=accent, width=4)
        self.create_line(92, 116, 96, 130, fill=accent, width=4)
        self.create_text(75, 138, text=self.status, fill=MUTED, font=("Microsoft YaHei UI", 8))


class CompanionTkApp:
    def __init__(self, runtime):
        self.runtime = runtime
        self.mode = AgentMode.CHAT
        self.current_proposal = None
        self.proactive_inflight = False
        self.jobs = queue.Queue()
        self.persona_name = (
            runtime.persona.name if getattr(runtime, "persona", None) else "数字分身"
        )
        self.root = tk.Tk()
        self.root.title(self.persona_name)
        self.root.geometry("500x760")
        self.root.minsize(430, 640)
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", False)
        try:
            self.root.attributes("-alpha", 0.98)
        except tk.TclError:
            pass
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
        self.setup_style()
        self.build()
        self.refresh_workspace()
        self.refresh_history()
        self.refresh_pending()
        self.root.after(60, self.poll_jobs)
        self.root.after(60000, self.poll_proactive)

    def setup_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "TButton",
            background=PANEL_2,
            foreground=TEXT,
            bordercolor=LINE,
            focuscolor=PANEL_2,
            padding=(8, 5),
            font=("Microsoft YaHei UI", 9),
        )
        style.map("TButton", background=[("active", "#29444d")])
        style.configure(
            "Primary.TButton",
            background=ACCENT,
            foreground="#071315",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map("Primary.TButton", background=[("active", "#6fd2d2")])
        style.configure(
            "TLabel",
            background=BG,
            foreground=TEXT,
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "Muted.TLabel",
            background=BG,
            foreground=MUTED,
            font=("Microsoft YaHei UI", 8),
        )
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT)
        style.configure("Treeview.Heading", background=PANEL_2, foreground=TEXT)

    def build(self):
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=18, pady=(14, 2))
        title = tk.Label(
            top,
            text="{}  /  Companion".format(self.persona_name),
            bg=BG,
            fg=TEXT,
            font=("Microsoft YaHei UI", 12, "bold"),
        )
        title.pack(side="left")
        self.title_label = title
        close = ttk.Button(top, text="关闭", command=self.root.destroy)
        close.pack(side="right")
        top.bind("<ButtonPress-1>", self.start_drag)
        top.bind("<B1-Motion>", self.drag)
        title.bind("<ButtonPress-1>", self.start_drag)
        title.bind("<B1-Motion>", self.drag)

        self.avatar = TkAvatar(self.root)
        self.avatar.pack(pady=(4, 0))

        mode_row = tk.Frame(self.root, bg=BG)
        mode_row.pack(fill="x", padx=18, pady=4)
        tk.Label(mode_row, text="模式", bg=BG, fg=TEXT).pack(side="left")
        self.chat_mode = ttk.Button(mode_row, text="陪伴", command=lambda: self.set_mode(AgentMode.CHAT))
        self.work_mode = ttk.Button(mode_row, text="工作", command=lambda: self.set_mode(AgentMode.WORK))
        self.chat_mode.pack(side="left", padx=(8, 4))
        self.work_mode.pack(side="left")
        ttk.Button(mode_row, text="记忆", command=self.open_memory).pack(side="right")
        ttk.Button(mode_row, text="情绪", command=self.open_affect).pack(side="right", padx=6)
        ttk.Button(mode_row, text="人设", command=self.open_persona).pack(side="right")
        ttk.Button(mode_row, text="主动", command=self.open_proactive).pack(side="right", padx=6)
        ttk.Button(mode_row, text="任务", command=self.open_work).pack(side="right")

        workspace_row = tk.Frame(self.root, bg=BG)
        workspace_row.pack(fill="x", padx=18, pady=(4, 6))
        tk.Label(workspace_row, text="工作目录", bg=BG, fg=MUTED).pack(side="left")
        self.workspace_label = tk.Label(
            workspace_row,
            text="未授权",
            anchor="w",
            bg=BG,
            fg=MUTED,
            font=("Microsoft YaHei UI", 8),
        )
        self.workspace_label.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(workspace_row, text="选择", command=self.choose_workspace).pack(side="right")

        chat_frame = tk.Frame(self.root, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        chat_frame.pack(fill="both", expand=True, padx=18, pady=4)
        self.chat = tk.Text(
            chat_frame,
            bg=PANEL,
            fg=TEXT,
            insertbackground=TEXT,
            wrap="word",
            borderwidth=0,
            padx=10,
            pady=8,
            font=("Microsoft YaHei UI", 9),
            state="disabled",
        )
        scroll = ttk.Scrollbar(chat_frame, command=self.chat.yview)
        self.chat.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.chat.pack(side="left", fill="both", expand=True)
        self.chat.tag_configure("user", foreground="#8ed7d7", font=("Microsoft YaHei UI", 9, "bold"))
        self.chat.tag_configure("assistant", foreground="#f2c28f", font=("Microsoft YaHei UI", 9, "bold"))
        self.chat.tag_configure("body", foreground=TEXT, font=("Microsoft YaHei UI", 9))

        input_row = tk.Frame(self.root, bg=BG)
        input_row.pack(fill="x", padx=18, pady=6)
        self.input = tk.Text(
            input_row,
            height=4,
            bg=PANEL,
            fg=TEXT,
            insertbackground=TEXT,
            wrap="word",
            relief="flat",
            padx=8,
            pady=6,
            font=("Microsoft YaHei UI", 9),
        )
        self.input.pack(side="left", fill="x", expand=True)
        self.send_button = ttk.Button(
            input_row,
            text="发送",
            style="Primary.TButton",
            command=self.send,
            width=8,
        )
        self.send_button.pack(side="right", fill="y", padx=(8, 0))
        self.input.bind("<Control-Return>", lambda event: (self.send(), "break")[1])

        self.confirm_frame = tk.Frame(
            self.root,
            bg=PANEL_2,
            highlightbackground="#4d7b82",
            highlightthickness=1,
        )
        self.confirm_text = tk.Label(
            self.confirm_frame,
            text="",
            justify="left",
            anchor="w",
            wraplength=430,
            bg=PANEL_2,
            fg=TEXT,
            font=("Microsoft YaHei UI", 9),
        )
        self.confirm_text.pack(fill="x", padx=10, pady=8)
        buttons = tk.Frame(self.confirm_frame, bg=PANEL_2)
        buttons.pack(fill="x", padx=10, pady=(0, 8))
        self.confirm_button = ttk.Button(buttons, text="允许这次写入", style="Primary.TButton", command=self.confirm_action)
        self.cancel_button = ttk.Button(buttons, text="取消", command=self.cancel_action)
        self.confirm_button.pack(side="left")
        self.cancel_button.pack(side="left", padx=6)

        self.status_row = tk.Frame(self.root, bg=BG)
        self.status_row.pack(fill="x", padx=18, pady=(0, 10))
        self.status = tk.Label(
            self.status_row,
            text="",
            anchor="w",
            bg=BG,
            fg=MUTED,
            wraplength=330,
            font=("Microsoft YaHei UI", 8),
        )
        self.status.pack(side="left", fill="x", expand=True)
        self.undo_button = ttk.Button(
            self.status_row,
            text="撤销最近写入",
            command=self.undo_last,
        )
        self.set_mode(self.runtime.current_mode())

    def set_mode(self, mode):
        mode = AgentMode(mode)
        self.mode = mode
        self.runtime.set_mode(mode)
        if mode == AgentMode.CHAT:
            self.chat_mode.configure(style="Primary.TButton")
            self.work_mode.configure(style="TButton")
            self.avatar.set_state(status="陪伴中")
        else:
            self.work_mode.configure(style="Primary.TButton")
            self.chat_mode.configure(style="TButton")
            self.avatar.set_state(status="工作中")

    def start_drag(self, event):
        self.drag_x = event.x_root - self.root.winfo_x()
        self.drag_y = event.y_root - self.root.winfo_y()

    def drag(self, event):
        self.root.geometry(
            "+{}+{}".format(
                event.x_root - self.drag_x,
                event.y_root - self.drag_y,
            )
        )

    def refresh_workspace(self):
        workspace = self.runtime.workspace()
        self.workspace_label.configure(text=workspace or "未授权")

    def choose_workspace(self):
        selected = filedialog.askdirectory(title="选择允许分身读写的目录")
        if not selected:
            return
        try:
            self.runtime.select_workspace(selected)
            self.refresh_workspace()
            self.status.configure(text="工作目录已授权：" + selected)
        except Exception as error:
            messagebox.showwarning("目录不可用", str(error))

    def refresh_history(self):
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        for message in self.runtime.history():
            if message["role"] not in ("user", "assistant"):
                continue
            tag = "user" if message["role"] == "user" else "assistant"
            label = "你" if message["role"] == "user" else "分身"
            self.chat.insert("end", label + "\n", tag)
            self.chat.insert("end", message["content"] + "\n\n", "body")
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def send(self):
        text = self.input.get("1.0", "end").strip()
        if not text:
            return
        self.input.delete("1.0", "end")
        self.send_button.configure(state="disabled")
        self.avatar.set_state(status="思考中")
        self.status.configure(text="正在处理...")

        def work():
            return self.runtime.handle_message(text, self.mode)

        def success(result):
            self.send_button.configure(state="normal")
            self.avatar.set_state(status="陪伴中" if self.mode == AgentMode.CHAT else "工作中")
            self.status.configure(text="")
            self.refresh_history()
            if result.get("proposal"):
                self.show_proposal(result["proposal"])
            task = result.get("task")
            if task is not None:
                self.status.configure(text="任务卡已生成，请批准、修改或拒绝。")
                self.open_work()
            self.refresh_avatar_mood()

        self.run_async(work, success)

    def show_proposal(self, proposal):
        self.current_proposal = proposal
        self.confirm_text.configure(text=proposal.user_visible_plan)
        self.confirm_frame.pack(fill="x", padx=18, pady=(0, 6), before=self.status)
        self.undo_button.pack_forget()
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
        self.confirm_button.configure(state="disabled")
        self.cancel_button.configure(state="disabled")
        self.avatar.set_state(status="写入中")

        def success(result):
            self.confirm_button.configure(state="normal")
            self.cancel_button.configure(state="normal")
            self.confirm_frame.pack_forget()
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
                self.status.configure(text=text)
                if receipt.undoable and receipt.undo_status == "available":
                    self.undo_button.pack(side="right", padx=(6, 0))
                else:
                    self.undo_button.pack_forget()
            else:
                self.status.configure(text="")
                self.undo_button.pack_forget()
            self.avatar.set_state(
                status="陪伴中" if self.mode == AgentMode.CHAT else "工作中"
            )

        self.run_async(
            lambda: self.runtime.confirm_proposal(action_id, mode),
            success,
        )

    def cancel_action(self):
        if not self.current_proposal:
            return
        self.runtime.cancel_proposal(self.current_proposal.action_id)
        self.current_proposal = None
        self.confirm_frame.pack_forget()
        self.refresh_history()
        self.status.configure(text="已取消，没有写入文件。")

    def undo_last(self):
        receipts = [
            receipt
            for receipt in self.runtime.latest_receipts(10)
            if receipt.undoable and receipt.undo_status == "available"
        ]
        if not receipts:
            self.status.configure(text="没有可撤销的写入。")
            return
        receipt = receipts[0]

        def success(message):
            self.status.configure(text=message)
            self.confirm_frame.pack_forget()
            self.refresh_history()

        self.run_async(
            lambda: self.runtime.undo_receipt(receipt.receipt_id),
            success,
        )

    def refresh_avatar_mood(self):
        state = self.runtime.affect_snapshot()
        self.avatar.set_state(mood=state["agent"]["mood_label"])

    def refresh_persona(self):
        self.persona_name = (
            self.runtime.persona.name
            if getattr(self.runtime, "persona", None)
            else "数字分身"
        )
        self.root.title(self.persona_name)
        self.title_label.config(
            text="{}  /  Companion".format(self.persona_name)
        )

    def persona_summary(self, info):
        source = info.get("source")
        if not isinstance(source, dict):
            source = {}
        license_info = info.get("license")
        if not isinstance(license_info, dict):
            license_info = {}
        lines = [
            "名称：{}（v{}）".format(
                info.get("name") or "数字分身",
                info.get("version") or "-",
            ),
            "ID：{}".format(info.get("id") or "-"),
            "稳定前缀哈希：{}".format(info.get("stable_hash") or "-"),
            "世界书：{} 条 / {} 字符".format(
                info.get("worldbook_entries", 0),
                info.get("worldbook_chars", 0),
            ),
        ]
        if not info.get("loaded"):
            lines.append(
                "人设包未加载：{}".format(
                    info.get("error") or "目录不存在"
                )
            )
        for label, key in (
            ("作品", "work"),
            ("权利方", "owner"),
            ("版本", "release"),
            ("设定截止", "canon_cutoff"),
        ):
            value = source.get(key)
            if value:
                lines.append("{}：{}".format(label, value))
        for label, key in (
            ("角色 IP", "character_ip"),
            ("使用边界", "derivative_policy"),
        ):
            value = license_info.get(key)
            if value:
                lines.append("{}：{}".format(label, value))
        files = info.get("files")
        if isinstance(files, dict) and files:
            lines.append(
                "文件：{}".format(
                    "、".join(
                        "{} {:.1f} KB".format(name, size / 1024.0)
                        for name, size in files.items()
                    )
                )
            )
            lines.append(
                "总大小：{:.1f} KB".format(
                    info.get("total_bytes", 0) / 1024.0
                )
            )
        return "\n".join(lines)

    def open_persona(self):
        info = self.runtime.persona_info()
        preferences = info.get("preferences")
        if not isinstance(preferences, dict):
            preferences = {}
        window = tk.Toplevel(self.root)
        window.title("人设与设定")
        window.geometry("580x700")
        window.configure(bg=BG)
        tk.Label(
            window,
            text="人设包",
            bg=BG,
            fg=TEXT,
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w", padx=14, pady=(12, 6))
        info_box = tk.Text(
            window,
            height=12,
            bg=PANEL,
            fg=TEXT,
            wrap="word",
            relief="flat",
            padx=8,
            pady=6,
        )
        info_box.insert("1.0", self.persona_summary(info))
        info_box.configure(state="disabled")
        info_box.pack(fill="x", padx=14)

        tk.Label(
            window,
            text="称呼（留空表示恢复默认）",
            bg=BG,
            fg=MUTED,
        ).pack(anchor="w", padx=14, pady=(8, 2))
        address = tk.Entry(
            window,
            bg=PANEL,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
        )
        address.insert(0, str(preferences.get("address") or ""))
        address.pack(fill="x", padx=14, ipady=4)

        tk.Label(
            window,
            text="关系",
            bg=BG,
            fg=MUTED,
        ).pack(anchor="w", padx=14, pady=(8, 2))
        relationship = tk.Text(
            window,
            height=3,
            bg=PANEL,
            fg=TEXT,
            insertbackground=TEXT,
            wrap="word",
            relief="flat",
            padx=6,
            pady=4,
        )
        relationship.insert(
            "1.0",
            str(preferences.get("relationship") or ""),
        )
        relationship.pack(fill="x", padx=14)

        tk.Label(
            window,
            text="边界与雷点",
            bg=BG,
            fg=MUTED,
        ).pack(anchor="w", padx=14, pady=(8, 2))
        boundaries = tk.Text(
            window,
            height=3,
            bg=PANEL,
            fg=TEXT,
            insertbackground=TEXT,
            wrap="word",
            relief="flat",
            padx=6,
            pady=4,
        )
        boundaries.insert(
            "1.0",
            str(preferences.get("boundaries") or ""),
        )
        boundaries.pack(fill="x", padx=14)

        tk.Label(
            window,
            text="这些设定以受保护人设记忆保存，优先于角色默认；"
            "留空表示清除，且不能覆盖安全规则。",
            bg=BG,
            fg=MUTED,
            wraplength=520,
            justify="left",
        ).pack(anchor="w", padx=14, pady=(6, 2))
        status = tk.Label(window, text="", bg=BG, fg=MUTED, wraplength=520)
        status.pack(anchor="w", padx=14)

        def save():
            try:
                self.runtime.set_persona_preference(
                    "address",
                    address.get(),
                )
                self.runtime.set_persona_preference(
                    "relationship",
                    relationship.get("1.0", "end-1c"),
                )
                self.runtime.set_persona_preference(
                    "boundaries",
                    boundaries.get("1.0", "end-1c"),
                )
            except ValueError as error:
                messagebox.showwarning("人设设定", str(error), parent=window)
                return
            self.refresh_persona()
            status.config(text="已保存。空白的设定项已清除。")

        def import_pack():
            path = filedialog.askdirectory(
                title="选择人设包目录",
                parent=window,
            )
            if not path:
                return
            try:
                preview = self.runtime.preview_persona_import(path)
            except Exception as error:
                messagebox.showwarning(
                    "导入人设包",
                    str(error),
                    parent=window,
                )
                return
            detail = self.persona_summary(preview)
            warnings = preview.get("warnings") or []
            if warnings:
                detail += "\n\n" + "\n".join(str(item) for item in warnings)
            overwrite = bool(preview.get("target_exists"))
            question = detail + (
                "\n\n同 ID 人设包已存在。覆盖后会用新文件替换当前版本，"
                "已有用户记忆不受影响。继续吗？"
                if overwrite
                else "\n\n只复制人设文本文件，确定导入吗？"
            )
            if not messagebox.askyesno(
                "导入人设包",
                question,
                parent=window,
            ):
                return
            try:
                imported = self.runtime.import_persona(
                    path,
                    overwrite=overwrite,
                )
            except Exception as error:
                messagebox.showwarning(
                    "导入人设包",
                    str(error),
                    parent=window,
                )
                return
            self.refresh_persona()
            status.config(
                text="已导入并切换到“{}”。当前哈希：{}".format(
                    imported.get("name"),
                    str(imported.get("stable_hash") or "")[:12],
                )
            )
            info_box.configure(state="normal")
            info_box.delete("1.0", "end")
            info_box.insert("1.0", self.persona_summary(imported))
            info_box.configure(state="disabled")

        actions = tk.Frame(window, bg=BG)
        actions.pack(fill="x", padx=14, pady=10)
        ttk.Button(actions, text="保存设定", command=save).pack(side="left")
        ttk.Button(
            actions,
            text="导入人设包…",
            command=import_pack,
        ).pack(side="left", padx=6)
        ttk.Button(actions, text="关闭", command=window.destroy).pack(
            side="right"
        )

    def open_memory(self):
        window = tk.Toplevel(self.root)
        window.title("记忆与共同经历")
        window.geometry("820x700")
        window.configure(bg=BG)
        tk.Label(
            window,
            text="记忆账本",
            bg=BG,
            fg=TEXT,
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w", padx=14, pady=(12, 6))
        table = ttk.Treeview(
            window,
            columns=("id", "kind", "content", "status", "protected"),
            show="headings",
            height=9,
        )
        for column, label, width in [
            ("id", "ID", 120),
            ("kind", "类型", 100),
            ("content", "内容", 330),
            ("status", "状态", 80),
            ("protected", "保护", 50),
        ]:
            table.heading(column, text=label)
            table.column(column, width=width, stretch=column == "content")
        table.pack(fill="both", expand=True, padx=14)

        detail = tk.Text(
            window,
            height=10,
            bg=PANEL,
            fg=TEXT,
            wrap="word",
            relief="flat",
            padx=8,
            pady=6,
        )
        detail.pack(fill="both", expand=False, padx=14, pady=(8, 4))

        def selected(show_message=True):
            values = table.selection()
            if not values:
                if show_message:
                    messagebox.showinfo("记忆", "请先选择一条记忆。")
                return None
            return values[0]

        def show_details(event=None):
            memory_id = selected(show_message=False)
            detail.configure(state="normal")
            detail.delete("1.0", "end")
            if not memory_id:
                detail.insert("end", "选择一条记忆查看来源、候选与召回记录。")
                detail.configure(state="disabled")
                return
            try:
                details = self.runtime.memory_details(memory_id)
            except ValueError as error:
                detail.insert("end", str(error))
                detail.configure(state="disabled")
                return
            memory = details["memory"]
            lines = [
                memory.content,
                "ID：{}  类型：{}  状态：{}  保护：{}".format(
                    memory.memory_id,
                    memory.kind,
                    memory.status,
                    "是" if memory.protected else "否",
                ),
                "规范键：{}  证据：{} 条  重要度：{:.2f}".format(
                    memory.canonical_key or "-",
                    memory.evidence_count,
                    memory.importance,
                ),
            ]
            if details["evidence"]:
                lines.append("\n来源证据：")
                for item in details["evidence"]:
                    lines.append(
                        "- 消息 #{}（{}）原文：{}".format(
                            item["message_id"],
                            item["source_role"],
                            item.get("message_content", ""),
                        )
                    )
            if details["candidates"]:
                lines.append("\n候选记录：")
                for item in details["candidates"]:
                    lines.append(
                        "- {} 评分 {:.2f}：{}".format(
                            item["status"],
                            item["score"],
                            item["reason"],
                        )
                    )
            if details["recalls"]:
                lines.append("\n最近召回：")
                for item in details["recalls"]:
                    score = item.get("score")
                    lines.append(
                        "- {} 来源 {} 评分 {} {}".format(
                            item["created_at"],
                            item.get("source") or "-",
                            "{:.3f}".format(score)
                            if score is not None
                            else "-",
                            "已采用" if item.get("used_in_reply") else "未采用",
                        )
                    )
            if details["derived"]:
                lines.append(
                    "\n派生记忆：{}".format("、".join(details["derived"]))
                )
            detail.insert("end", "\n".join(lines))
            detail.configure(state="disabled")

        experiences = tk.Text(
            window,
            height=5,
            bg=PANEL,
            fg=TEXT,
            wrap="word",
            relief="flat",
            padx=8,
            pady=6,
        )
        experiences.pack(fill="x", padx=14, pady=(4, 8))

        def render():
            table.delete(*table.get_children())
            for memory in self.runtime.memories():
                table.insert(
                    "",
                    "end",
                    iid=memory.memory_id,
                    values=(
                        memory.memory_id,
                        memory.kind,
                        memory.content,
                        memory.status,
                        "是" if memory.protected else "",
                    ),
                )
            experiences.configure(state="normal")
            experiences.delete("1.0", "end")
            rows = self.runtime.experiences()
            experiences.insert(
                "end",
                "\n\n".join(
                    "{}：{}".format(item.title, item.summary)
                    for item in rows
                )
                or "还没有共同经历记录。",
            )
            experiences.configure(state="disabled")
            show_details()

        def correct():
            memory_id = selected()
            if not memory_id:
                return
            current = table.item(memory_id, "values")[2]
            replacement = simpledialog.askstring(
                "纠正记忆",
                "新的内容：",
                initialvalue=current,
                parent=window,
            )
            if replacement and replacement.strip():
                self.runtime.correct_memory(memory_id, replacement.strip())
                render()

        def forget():
            memory_id = selected()
            if not memory_id:
                return
            if messagebox.askyesno(
                "忘记记忆",
                "确认忘记选中的记忆？",
                parent=window,
            ):
                self.runtime.forget_memory(memory_id)
                render()

        def toggle_protected():
            memory_id = selected()
            if not memory_id:
                return
            details = self.runtime.memory_details(memory_id)
            self.runtime.set_memory_protected(
                memory_id,
                not details["memory"].protected,
            )
            render()

        def add_persona():
            content = simpledialog.askstring(
                "新增人设",
                "希望长期保留、不会被自动淘汰的人设：",
                parent=window,
            )
            if content and content.strip():
                self.runtime.remember_persona(content.strip())
                render()

        def import_chat():
            path = filedialog.askopenfilename(
                parent=window,
                title="导入旧聊天",
                filetypes=[
                    ("聊天记录", "*.json *.jsonl *.ndjson"),
                    ("所有文件", "*.*"),
                ],
            )
            if not path:
                return
            try:
                estimate = self.runtime.estimate_chat_import(path)
            except Exception as error:
                messagebox.showwarning("导入旧聊天", str(error), parent=window)
                return
            detail_text = (
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
            if not messagebox.askyesno(
                "导入成本预估",
                detail_text + "\n\n确定继续吗？",
                parent=window,
            ):
                return
            try:
                result = self.runtime.import_chat(path)
            except Exception as error:
                messagebox.showwarning("导入旧聊天", str(error), parent=window)
                return
            messagebox.showinfo(
                "导入旧聊天",
                "状态：{}\n导入消息：{} 条\n新记忆：{} 条\n"
                "跳过重复：{} 条".format(
                    result["status"],
                    result["imported_messages"],
                    result["imported_memories"],
                    result["skipped_duplicates"],
                ),
                parent=window,
            )
            render()

        row = tk.Frame(window, bg=BG)
        row.pack(fill="x", padx=14, pady=8)
        ttk.Button(row, text="纠正选中记忆", command=correct).pack(side="left")
        ttk.Button(row, text="忘记选中记忆", command=forget).pack(
            side="left",
            padx=6,
        )
        ttk.Button(row, text="切换受保护", command=toggle_protected).pack(
            side="left"
        )
        ttk.Button(row, text="新增人设", command=add_persona).pack(
            side="left",
            padx=6,
        )
        ttk.Button(row, text="导入旧聊天", command=import_chat).pack(side="left")
        table.bind("<<TreeviewSelect>>", show_details)
        render()

    def open_affect(self):
        window = tk.Toplevel(self.root)
        window.title("情绪状态")
        window.geometry("500x290")
        window.configure(bg=BG)
        label = tk.Label(
            window,
            text="",
            justify="left",
            bg=BG,
            fg=TEXT,
            font=("Microsoft YaHei UI", 10),
        )
        label.pack(anchor="w", padx=16, pady=16)

        def refresh():
            state = self.runtime.affect_snapshot()
            user = state["user"]
            agent = state["agent"]
            label.configure(
                text=(
                    "你的状态估计：{}，置信度 {:.2f}\n"
                    "角色状态：{}，精力 {}"
                ).format(
                    user["mood_label"],
                    user["confidence"],
                    agent["mood_label"],
                    "未知"
                    if agent["energy"] is None
                    else "{:.2f}".format(agent["energy"]),
                )
            )

        def correct(mood):
            self.runtime.correct_affect(mood)
            refresh()
            self.refresh_avatar_mood()

        tk.Label(
            window,
            text="情绪只是估计，你可以直接纠正。",
            bg=BG,
            fg=MUTED,
        ).pack(anchor="w", padx=16)
        row = tk.Frame(window, bg=BG)
        row.pack(fill="x", padx=16, pady=14)
        for text, mood in [
            ("我现在没事", "neutral"),
            ("我有点低落", "low"),
            ("我只是累了", "tired"),
            ("我心情不错", "positive"),
        ]:
            ttk.Button(
                row,
                text=text,
                command=lambda value=mood: correct(value),
            ).pack(side="left", padx=(0, 6))
        refresh()

    def open_work(self):
        window = tk.Toplevel(self.root)
        window.title("工作任务卡")
        window.geometry("760x600")
        window.configure(bg=BG)
        note = tk.Label(
            window,
            text="批准前不会执行或转发；超时不自动通过；结果不确定不会重发。",
            justify="left",
            anchor="w",
            bg=BG,
            fg=MUTED,
        )
        note.pack(fill="x", padx=16, pady=(14, 4))
        body = tk.Frame(window, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=6)
        listbox = tk.Listbox(
            body,
            bg=PANEL,
            fg=TEXT,
            selectbackground="#2d5058",
            borderwidth=0,
            font=("Microsoft YaHei UI", 9),
            width=28,
        )
        listbox.pack(side="left", fill="y")
        detail = tk.Text(
            body,
            bg=PANEL,
            fg=TEXT,
            wrap="word",
            borderwidth=0,
            padx=10,
            pady=8,
            font=("Microsoft YaHei UI", 9),
        )
        detail.pack(side="left", fill="both", expand=True, padx=(8, 0))
        tasks = []

        def selected():
            selection = listbox.curselection()
            if not selection:
                return None
            index = selection[0]
            if 0 <= index < len(tasks):
                return tasks[index]
            return None

        def show_selected(_event=None):
            task = selected()
            detail.configure(state="normal")
            detail.delete("1.0", "end")
            if task is None:
                detail.insert("end", "选择一条任务查看计划。\n")
                detail.configure(state="disabled")
                return
            receipt = self.runtime.work_receipt(task.task_id)
            detail.insert("end", "标题：{}\n".format(task.card.title))
            detail.insert("end", "目标：{}\n".format(task.card.goal))
            detail.insert("end", "理由：{}\n".format(task.card.reason))
            detail.insert(
                "end",
                "执行器：{}　项目：{}\n".format(
                    task.card.executor,
                    task.card.project,
                ),
            )
            detail.insert("end", "口语播报：{}\n".format(task.card.spoken_summary))
            detail.insert(
                "end",
                "计划状态：{}　修订：{}　已追问：{} 次\n".format(
                    STATE_LABELS.get(task.state, task.state),
                    task.revision,
                    task.info_asked,
                ),
            )
            if task.approval_reason:
                detail.insert("end", "审批说明：{}\n".format(task.approval_reason))
            if receipt:
                detail.insert(
                    "end",
                    "转发回执：{}（尝试 {} 次）\n{}\n".format(
                        FORWARD_LABELS.get(receipt.status, receipt.status),
                        receipt.attempts,
                        receipt.summary,
                    ),
                )
                if receipt.detail:
                    detail.insert("end", "回执详情：{}\n".format(receipt.detail))
                if receipt.status == "unknown":
                    detail.insert("end", "注意：结果不确定，系统不会自动重发。\n")
            detail.configure(state="disabled")

        def refresh():
            selected_id = ""
            current = selected()
            if current is not None:
                selected_id = current.task_id
            tasks[:] = self.runtime.work_tasks(20)
            listbox.delete(0, "end")
            target = 0
            for index, task in enumerate(tasks):
                listbox.insert(
                    "end",
                    "{}｜{}".format(
                        STATE_LABELS.get(task.state, task.state),
                        task.card.title,
                    ),
                )
                if task.task_id == selected_id:
                    target = index
            if tasks:
                listbox.selection_set(target)
                listbox.activate(target)
            show_selected()

        def new_task():
            text = simpledialog.askstring(
                "新建任务卡",
                "描述你想完成的工作：",
                parent=window,
            )
            if not text or not text.strip():
                return
            try:
                self.runtime.create_work_task(text, force=True)
            except Exception as error:
                note.configure(text="创建失败：{}".format(error))
                return
            note.configure(text="任务卡已生成。")
            self.refresh_history()
            refresh()

        def approve():
            task = selected()
            if task is None:
                return
            if not messagebox.askyesno(
                "批准计划",
                "批准后才会调用执行器；结果不确定时不会自动重发。继续吗？",
                parent=window,
            ):
                return
            try:
                result = self.runtime.approve_work_task(task.task_id)
            except Exception as error:
                note.configure(text="批准失败：{}".format(error))
                return
            note.configure(text=result.get("answer", "已完成。").splitlines()[0])
            self.refresh_history()
            refresh()

        def revise():
            task = selected()
            if task is None:
                return
            text = simpledialog.askstring(
                "修改计划",
                "说明要修改的目标、对象或步骤：",
                parent=window,
            )
            if not text or not text.strip():
                return
            try:
                result = self.runtime.revise_work_task(task.task_id, text)
            except Exception as error:
                note.configure(text="修改失败：{}".format(error))
                return
            note.configure(text=result.get("answer", "计划已更新。").splitlines()[0])
            refresh()

        def reject():
            task = selected()
            if task is None:
                return
            if not messagebox.askyesno(
                "拒绝计划",
                "拒绝后不会执行或转发这个任务。继续吗？",
                parent=window,
            ):
                return
            try:
                result = self.runtime.reject_work_task(
                    task.task_id,
                    "用户在任务面板拒绝",
                )
            except Exception as error:
                note.configure(text="拒绝失败：{}".format(error))
                return
            note.configure(text=result.get("answer", "已拒绝。"))
            self.refresh_history()
            refresh()

        listbox.bind("<<ListboxSelect>>", show_selected)
        row = tk.Frame(window, bg=BG)
        row.pack(fill="x", padx=16, pady=(0, 12))
        ttk.Button(row, text="新建任务卡", style="Primary.TButton", command=new_task).pack(side="left")
        ttk.Button(row, text="批准计划", command=approve).pack(side="left", padx=6)
        ttk.Button(row, text="修改计划", command=revise).pack(side="left")
        ttk.Button(row, text="拒绝", command=reject).pack(side="left", padx=6)
        ttk.Button(row, text="刷新", command=refresh).pack(side="left")
        refresh()

    def open_proactive(self):
        window = tk.Toplevel(self.root)
        window.title("主动陪伴")
        window.geometry("700x560")
        window.configure(bg=BG)
        summary = tk.Label(
            window,
            text="",
            justify="left",
            anchor="w",
            bg=BG,
            fg=TEXT,
            font=("Microsoft YaHei UI", 9),
        )
        summary.pack(fill="x", padx=16, pady=(14, 6))
        note = tk.Label(
            window,
            text="",
            justify="left",
            anchor="w",
            bg=BG,
            fg=MUTED,
        )
        note.pack(fill="x", padx=16)
        text = tk.Text(
            window,
            bg=PANEL,
            fg=TEXT,
            wrap="word",
            borderwidth=0,
            padx=10,
            pady=8,
            font=("Microsoft YaHei UI", 9),
            height=14,
        )
        text.pack(fill="both", expand=True, padx=16, pady=8)

        def refresh():
            status = self.runtime.proactive_status()
            next_due = status["next_due"]
            lines = [
                "状态：{}｜活动：{}".format(
                    "开启" if status["enabled"] else "已暂停",
                    status["activity_label"],
                ),
                "今日 {}/{}｜未回复 {}/{}｜未读 {}｜下一条提醒：{}".format(
                    status["daily_count"],
                    status["daily_limit"],
                    status["unanswered"],
                    status["max_unanswered"],
                    status["unread"],
                    (
                        "{}（{}）".format(
                            next_due["content"],
                            next_due["display"],
                        )
                        if next_due
                        else "无"
                    ),
                ),
            ]
            summary.configure(text="\n".join(lines))
            text.configure(state="normal")
            text.delete("1.0", "end")
            events = self.runtime.proactive_events(20)
            if not events:
                text.insert("end", "还没有主动发言记录。\n")
            for event in events:
                state = status_label(event.status)
                if event.read_at is None:
                    state += " / 未读"
                text.insert(
                    "end",
                    "[{}] {}｜{}\n原因：{}\n内容：{}\n\n".format(
                        event.created_at[:16],
                        event.source_type,
                        state,
                        event.trigger_reason,
                        event.content,
                    ),
                )
            text.configure(state="disabled")

        def toggle():
            status = self.runtime.proactive_status()
            self.runtime.set_proactive_enabled(not status["enabled"])
            note.configure(text="主动状态已更新。")
            refresh()

        def check_now():
            try:
                result = self.runtime.proactive_tick()
            except Exception as error:
                note.configure(text="主动检查失败：{}".format(error))
                return
            if result.get("spoke"):
                note.configure(
                    text="刚刚主动发言：{}".format(
                        result.get("trigger_reason") or "已送达"
                    )
                )
                self.refresh_history()
            elif result.get("missed"):
                note.configure(
                    text="有 {} 条提醒错过送达窗口，已标记未读。".format(
                        len(result["missed"])
                    )
                )
            else:
                note.configure(
                    text="现在不发言：{}".format(
                        result.get("reason_label") or result.get("reason")
                    )
                )
            refresh()

        def mark_all_read():
            count = self.runtime.mark_proactive_read()
            note.configure(text="已标记 {} 条事件为已读。".format(count))
            refresh()

        row = tk.Frame(window, bg=BG)
        row.pack(fill="x", padx=16, pady=(0, 12))
        ttk.Button(row, text="暂停/恢复", command=toggle).pack(side="left")
        ttk.Button(row, text="现在检查", command=check_now).pack(
            side="left",
            padx=6,
        )
        ttk.Button(row, text="全部已读", command=mark_all_read).pack(
            side="left"
        )
        ttk.Button(row, text="刷新", command=refresh).pack(side="left", padx=6)
        refresh()

    def poll_proactive(self):
        if not self.root.winfo_exists():
            return
        self.root.after(60000, self.poll_proactive)
        if self.proactive_inflight:
            return
        if str(self.send_button["state"]) == "disabled":
            return
        try:
            status = self.runtime.proactive_status()
        except Exception as error:
            self.status.configure(text="主动检查失败：{}".format(error))
            return
        if not status["enabled"]:
            return
        self.proactive_inflight = True

        def success(result):
            self.proactive_inflight = False
            if result.get("spoke"):
                self.refresh_history()
                self.refresh_avatar_mood()
                self.status.configure(
                    text="主动：{}".format(
                        result.get("trigger_reason") or "已送达"
                    )
                )
            elif result.get("missed"):
                self.status.configure(
                    text="有 {} 条错过的提醒，已标记未读。".format(
                        len(result["missed"])
                    )
                )

        def work():
            return self.runtime.proactive_tick()

        self.run_async(work, success)

    def run_async(self, function, on_success):
        def worker():
            try:
                self.jobs.put((True, function(), on_success))
            except Exception as error:
                self.jobs.put((False, str(error), on_success))

        threading.Thread(target=worker, daemon=True).start()

    def poll_jobs(self):
        try:
            while True:
                success, value, callback = self.jobs.get_nowait()
                if success:
                    callback(value)
                else:
                    self.worker_failed(value)
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(60, self.poll_jobs)

    def worker_failed(self, message):
        self.proactive_inflight = False
        self.send_button.configure(state="normal")
        self.confirm_button.configure(state="normal")
        self.cancel_button.configure(state="normal")
        self.avatar.set_state(status="遇到问题")
        self.status.configure(text=message)
        messagebox.showwarning("操作失败", message, parent=self.root)

    def run(self):
        self.root.mainloop()


def launch_tk(runtime):
    CompanionTkApp(runtime).run()
