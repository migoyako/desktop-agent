"""Qt worker that consumes runtime streaming events with cancellation support."""

from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal


class StreamWorker(QThread):
    event = Signal(dict)
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, runtime, text, mode, generation_id, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.text = text
        self.mode = mode
        self.generation_id = generation_id
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()
        self.requestInterruption()

    def run(self):
        result = None
        cancelled = False
        try:
            for item in self.runtime.stream_message(
                self.text,
                self.mode,
                generation_id=self.generation_id,
                cancel_event=self.cancel_event,
            ):
                if self.cancel_event.is_set():
                    cancelled = True
                    if item.get("kind") == "done":
                        result = item.get("result")
                        break
                    continue
                self.event.emit(item)
                if item.get("kind") == "done":
                    result = item.get("result")
            if result is None:
                result = {
                    "answer": "已停止生成。",
                    "proposal": None,
                    "receipt": None,
                    "task": None,
                    "task_receipt": None,
                    "cancelled": True,
                }
            if cancelled:
                result = dict(result)
                result["cancelled"] = True
            self.completed.emit(result)
        except Exception as error:
            if self.cancel_event.is_set():
                self.completed.emit(
                    {
                        "answer": "已停止生成。",
                        "proposal": None,
                        "receipt": None,
                        "task": None,
                        "task_receipt": None,
                        "cancelled": True,
                    }
                )
                return
            self.failed.emit(str(error) or error.__class__.__name__)
