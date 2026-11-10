"""Small append-only JSONL runtime log for local diagnosis.

The app normally runs under ``pythonw`` with no visible console. A bounded,
line-oriented log gives each turn an auditable sequence of stage transitions.
Only operational metadata is recorded; file content and credentials are never
written here.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from typing import Any, Dict, Optional


MAX_LOG_BYTES = 5 * 1024 * 1024
_LOCK = threading.RLock()


def log_path(data_dir) -> Path:
    return Path(data_dir) / "logs" / "companion.jsonl"


def write_event(data_dir, event: str, **fields: Any) -> None:
    """Best-effort structured log; logging failure never breaks execution."""
    try:
        path = log_path(data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: Dict[str, Any] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "event": str(event),
            "thread": threading.current_thread().name,
        }
        payload.update(fields)
        line = json.dumps(
            payload,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        ) + "\n"
        with _LOCK:
            if path.is_file() and path.stat().st_size >= MAX_LOG_BYTES:
                rotated = path.with_suffix(".jsonl.1")
                try:
                    os.replace(str(path), str(rotated))
                except OSError:
                    pass
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line)
                stream.flush()
    except Exception:
        return
