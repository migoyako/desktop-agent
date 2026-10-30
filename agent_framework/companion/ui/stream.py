"""Qt worker that consumes application events with cancellation support."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal


class StreamWorker(QThread):
    event = Signal(dict)
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, application, run, parent=None):
        super().__init__(parent)
        self.application = application
        self.run_context = run
        self.generation_id = run.run_id

    def cancel(self):
        self.application.request_cancel(self.run_context.run_id)
        self.requestInterruption()

    def run(self):
        result = None
        try:
            for event in self.application.stream_turn(self.run_context):
                item = event.as_dict()
                if self.run_context.cancel_event.is_set():
                    if item.get("kind") == "done":
                        result = (item.get("result") or {})
                        break
                    continue
                self.event.emit(item)
                if item.get("kind") == "done":
                    result = item.get("result") or {}
            if result is None:
                raise RuntimeError("回复流在完成事件前结束。")
            if self.run_context.cancel_event.is_set():
                result = dict(result)
                result["cancelled"] = True
            self.completed.emit(result)
        except Exception as error:
            self.failed.emit(str(error) or error.__class__.__name__)
