"""Disk-backed tool results for oversized outputs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Dict
import uuid

from .models import utc_now


RESULT_ID = re.compile(r"^tr_[0-9a-f]{32}$")
RESULT_DIR = "tool_results"
SPILL_BYTES = 16 * 1024
PREVIEW_CHARS = 2000
READ_DEFAULT_CHARS = 4000
READ_MAX_CHARS = 8000
MAX_STORE_BYTES = 4 * 1024 * 1024


class ToolResultStoreError(RuntimeError):
    """Raised when a stored tool result is missing or not readable."""


class ToolResultStore:
    def __init__(self, data_dir):
        self.root = Path(data_dir) / RESULT_DIR
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise ToolResultStoreError("工具结果目录不能是符号链接。")

    def save(self, session_id: str, tool_name: str, content: str) -> Dict:
        raw = str(content).encode("utf-8")
        stored = str(content)
        stored_truncated = len(raw) > MAX_STORE_BYTES
        if stored_truncated:
            stored = raw[:MAX_STORE_BYTES].decode("utf-8", errors="ignore")
        result_id = "tr_{}".format(uuid.uuid4().hex)
        payload = {
            "result_id": result_id,
            "session_id": session_id,
            "tool_name": tool_name,
            "created_at": utc_now(),
            "chars": len(str(content)),
            "bytes": len(raw),
            "stored_chars": len(stored),
            "stored_truncated": stored_truncated,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "content": stored,
        }
        target = self.root / "{}.json".format(result_id)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(self.root),
                prefix=".tmp-",
                suffix=".json",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False)
            os.replace(temp_path, target)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)
        return {
            key: payload[key]
            for key in (
                "result_id",
                "tool_name",
                "chars",
                "bytes",
                "stored_chars",
                "stored_truncated",
                "sha256",
            )
        }

    def read(self, session_id: str, result_id: str) -> Dict:
        path = self._path(result_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ToolResultStoreError("找不到这条工具结果。")
        except (OSError, ValueError):
            raise ToolResultStoreError("这条工具结果已损坏，无法读取。")
        if payload.get("session_id") != session_id:
            raise ToolResultStoreError("这条工具结果不属于当前会话。")
        content = payload.get("content")
        if not isinstance(content, str):
            raise ToolResultStoreError("这条工具结果缺少正文。")
        return payload

    def _path(self, result_id: str) -> Path:
        if not RESULT_ID.match(str(result_id or "")):
            raise ToolResultStoreError("工具结果句柄无效。")
        path = self.root / "{}.json".format(result_id)
        if path.is_symlink():
            raise ToolResultStoreError("工具结果文件不能是符号链接。")
        root = self.root.resolve()
        if root not in path.resolve().parents:
            raise ToolResultStoreError("工具结果路径越界。")
        return path


def compose_spill_notice(descriptor: Dict, preview: str) -> str:
    stored_note = (
        "\n存储时也已截断：完整输出超过 4MB。"
        if descriptor.get("stored_truncated")
        else ""
    )
    return (
        "[工具结果已落盘]\n"
        "状态：ok（工具成功，但内容过大，未全部返回）\n"
        "result_id：{}\n"
        "原始字符数：{}\n"
        "已保存字符数：{}{}\n"
        "注意：这里只返回前 {} 字符预览；未调用 read_tool_result 回读的部分"
        "不算已读，不能假设、概括或编造。\n"
        "继续读取：调用 read_tool_result(result_id=\"{}\", offset={}, limit={})。\n"
        "---- 预览 ----\n{}"
    ).format(
        descriptor.get("result_id", ""),
        descriptor.get("chars", 0),
        descriptor.get("stored_chars", 0),
        stored_note,
        min(len(preview), PREVIEW_CHARS),
        descriptor.get("result_id", ""),
        PREVIEW_CHARS,
        READ_DEFAULT_CHARS,
        preview,
    )


def compose_missing_store_notice(preview: str, error: str = "") -> str:
    return (
        "[工具结果过大，未能落盘]\n"
        "状态：truncated（只返回预览，完整内容不可回读）{}\n"
        "注意：以下仅为预览，未返回的内容不算已读，不能假设或编造。\n"
        "---- 预览 ----\n{}"
    ).format(
        "：{}".format(error) if error else "",
        preview,
    )
