"""Tkinter compatibility host used when PySide6 is not installed."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from ..models import AgentMode


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
        self.jobs = queue.Queue()
        self.root = tk.Tk()
        self.root.title("数字分身")
        self.root.geometry("500x760")
        self.root.minsize(430, 640)
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", True)
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
            text="数字分身  /  Companion",
            bg=BG,
            fg=TEXT,
            font=("Microsoft YaHei UI", 12, "bold"),
        )
        title.pack(side="left")
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
        self.undo_button = ttk.Button(buttons, text="撤销最近写入", command=self.undo_last)
        self.confirm_button.pack(side="left")
        self.cancel_button.pack(side="left", padx=6)
        self.undo_button.pack(side="left", padx=6)

        self.status = tk.Label(
            self.root,
            text="",
            anchor="w",
            bg=BG,
            fg=MUTED,
            wraplength=450,
            font=("Microsoft YaHei UI", 8),
        )
        self.status.pack(fill="x", padx=18, pady=(0, 10))
        self.set_mode(AgentMode.CHAT)

    def set_mode(self, mode):
        self.mode = mode
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
            self.status.configure(text=receipt.summary if receipt else "")
            if receipt:
                self.undo_button.pack(side="left", padx=6)
            self.avatar.set_state(status="工作中")

        self.run_async(
            lambda: self.runtime.confirm_proposal(action_id),
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

    def open_memory(self):
        window = tk.Toplevel(self.root)
        window.title("记忆与共同经历")
        window.geometry("760x560")
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
            columns=("id", "kind", "content", "status"),
            show="headings",
            height=12,
        )
        for column, label, width in [
            ("id", "ID", 120),
            ("kind", "类型", 110),
            ("content", "内容", 360),
            ("status", "状态", 90),
        ]:
            table.heading(column, text=label)
            table.column(column, width=width, stretch=column == "content")
        table.pack(fill="both", expand=True, padx=14)
        memories = self.runtime.memories()
        for memory in memories:
            table.insert(
                "",
                "end",
                iid=memory.memory_id,
                values=(
                    memory.memory_id,
                    memory.kind,
                    memory.content,
                    memory.status,
                ),
            )

        def selected():
            values = table.selection()
            if not values:
                messagebox.showinfo("记忆", "请先选择一条记忆。")
                return None
            return values[0]

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
                table.item(
                    memory_id,
                    values=(
                        memory_id,
                        table.item(memory_id, "values")[1],
                        replacement.strip(),
                        "confirmed",
                    ),
                )

        def forget():
            memory_id = selected()
            if not memory_id:
                return
            if messagebox.askyesno("忘记记忆", "确认忘记选中的记忆？", parent=window):
                self.runtime.forget_memory(memory_id)
                table.item(
                    memory_id,
                    values=(
                        memory_id,
                        table.item(memory_id, "values")[1],
                        table.item(memory_id, "values")[2],
                        "forgotten",
                    ),
                )

        row = tk.Frame(window, bg=BG)
        row.pack(fill="x", padx=14, pady=8)
        ttk.Button(row, text="纠正选中记忆", command=correct).pack(side="left")
        ttk.Button(row, text="忘记选中记忆", command=forget).pack(side="left", padx=6)
        tk.Label(
            window,
            text="共同经历",
            bg=BG,
            fg=TEXT,
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(anchor="w", padx=14)
        experiences = tk.Text(
            window,
            height=7,
            bg=PANEL,
            fg=TEXT,
            wrap="word",
            relief="flat",
            padx=8,
            pady=6,
        )
        experiences.pack(fill="x", padx=14, pady=(4, 12))
        rows = self.runtime.experiences()
        experiences.insert(
            "end",
            "\n\n".join(
                "{}：{}".format(item.title, item.summary) for item in rows
            )
            or "还没有共同经历记录。",
        )
        experiences.configure(state="disabled")

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
