"""Resumable import of legacy chat exports into evidence memory."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import re

from .models import AgentMode


SUPPORTED_ROLES = {"user", "assistant", "system", "tool"}
ROLEPLAY_TITLE = re.compile(r"角色扮演|语c|语C|皮下|ooc|OOC")
ROLEPLAY_START = re.compile(
    r"(?:角色扮演|语c|语C|皮上|开始(?:角色)?扮演|设定(?:我|你|是)[：:]?|"
    r"我来扮演|你扮演|剧本杀|跑团|扮演(?:我|你|一个|一下))"
)
ROLEPLAY_END = re.compile(
    r"(?:结束(?:角色)?扮演|不演了|出戏|回到现实|皮下发言)"
)


class ChatImportError(ValueError):
    pass


class ImportConfirmationRequired(ChatImportError):
    def __init__(self, estimate):
        super().__init__(
            "导入会写入消息和记忆。请先查看成本预估，再显式确认导入。"
        )
        self.estimate = estimate


def _content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _message_unit(item):
    if not isinstance(item, dict):
        return None
    role = item.get("role")
    if not role:
        author = item.get("author")
        if isinstance(author, dict):
            role = author.get("role")
        elif isinstance(author, str):
            role = author
    content = _content_text(item.get("content"))
    if role not in SUPPORTED_ROLES or not (content or "").strip():
        return None
    return {
        "role": role,
        "content": content.strip(),
        "conversation": str(
            item.get("conversation_id")
            or item.get("conversation")
            or item.get("title")
            or ""
        ),
    }


def _flatten(data):
    if isinstance(data, list):
        if data and all(
            isinstance(item, dict) and "messages" in item for item in data
        ):
            units = []
            for conversation in data:
                title = str(
                    conversation.get("title")
                    or conversation.get("id")
                    or ""
                )
                for item in conversation.get("messages") or []:
                    unit = _message_unit(item)
                    if unit is not None:
                        unit["conversation"] = title or unit["conversation"]
                        units.append(unit)
            return units
        return [
            unit
            for unit in (_message_unit(item) for item in data)
            if unit is not None
        ]
    if isinstance(data, dict):
        if "messages" in data:
            return _flatten([data])
        if "conversations" in data:
            return _flatten(data.get("conversations") or [])
    raise ChatImportError(
        "无法识别聊天文件格式：需要消息数组、包含 messages 的对象，"
        "或包含 messages 的会话数组。"
    )


def load_chat_units(path):
    path = Path(path)
    if not path.is_file():
        raise ChatImportError("聊天文件不存在：{}".format(path))
    raw = path.read_bytes()
    if not raw.strip():
        return [], hashlib.sha256(raw).hexdigest()
    fingerprint = hashlib.sha256(raw).hexdigest()
    if path.suffix.casefold() in (".jsonl", ".ndjson"):
        units = []
        for line_number, line in enumerate(
            raw.decode("utf-8-sig").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except ValueError as error:
                raise ChatImportError(
                    "第 {} 行不是有效 JSON：{}".format(line_number, error)
                )
            unit = _message_unit(item)
            if unit is not None:
                units.append(unit)
        return units, fingerprint
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except ValueError as error:
        raise ChatImportError("聊天文件不是有效 JSON：{}".format(error))
    return _flatten(data), fingerprint


def message_hash(unit):
    payload = "{}\n{}".format(unit["role"], unit["content"])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def roleplay_flags(units):
    flags = []
    active = False
    conversation = None
    for unit in units:
        current = unit.get("conversation") or ""
        if current != conversation:
            conversation = current
            active = bool(ROLEPLAY_TITLE.search(current))
        text = unit.get("content") or ""
        if ROLEPLAY_END.search(text):
            flags.append(active)
            active = False
            continue
        if unit.get("role") == "user" and ROLEPLAY_START.search(text):
            active = True
        flags.append(active)
    return flags


class ChatImportService:
    def __init__(self, store, memory):
        self.store = store
        self.memory = memory

    def estimate(self, path, target_session_id="main") -> dict:
        units, fingerprint = load_chat_units(path)
        flags = roleplay_flags(units)
        user_units = [unit for unit in units if unit["role"] == "user"]
        memory_units = [
            unit
            for unit, roleplay in zip(units, flags)
            if unit["role"] == "user" and not roleplay
        ]
        estimated_memories = 0
        for unit in memory_units:
            try:
                estimated_memories += len(self.memory._extract(unit["content"]))
            except Exception:
                estimated_memories += 0
        job = self.store.get_import_job(
            self._fingerprint(fingerprint, target_session_id)
        )
        return {
            "source": str(path),
            "file_fingerprint": fingerprint,
            "messages": len(units),
            "user_messages": len(user_units),
            "conversations": len(
                {unit["conversation"] for unit in units if unit["conversation"]}
            ),
            "estimated_memories": estimated_memories,
            "estimated_seconds": round(len(units) * 0.002, 2),
            "resume_from": int(job["cursor"]) if job else 0,
            "already_finished": bool(
                job and job["status"] == "completed"
            ),
        }

    @staticmethod
    def _fingerprint(file_fingerprint, target_session_id):
        payload = "{}\n{}".format(file_fingerprint, target_session_id)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def run(
        self,
        path,
        target_session_id="main",
        max_messages=None,
        approved=False,
    ) -> dict:
        estimate = self.estimate(path, target_session_id)
        if not approved:
            raise ImportConfirmationRequired(estimate)
        if self.store.get_session_mode(target_session_id) is None:
            self.store.ensure_session(target_session_id, AgentMode.CHAT)
        units, file_fingerprint = load_chat_units(path)
        flags = roleplay_flags(units)
        fingerprint = self._fingerprint(file_fingerprint, target_session_id)
        job = self.store.get_import_job(fingerprint)
        if job is None:
            job_id = self.store.create_import_job(
                fingerprint,
                Path(path).name,
                target_session_id,
                len(units),
            )
            job = self.store.get_import_job(fingerprint)
        else:
            job_id = job["job_id"]
        if job["status"] == "completed" or int(job["cursor"]) >= len(units):
            self.store.update_import_job(
                job_id,
                len(units),
                job["imported_messages"],
                job["imported_memories"],
                job["skipped_duplicates"],
                "completed",
            )
            return self._summary(job_id, "completed", len(units))

        cursor = max(0, min(int(job["cursor"]), len(units)))
        imported_messages = int(job["imported_messages"])
        imported_memories = int(job["imported_memories"])
        skipped = int(job["skipped_duplicates"])
        import_session_id = "import_{}".format(job_id)
        processed = 0
        status = "completed"
        while cursor < len(units):
            unit = units[cursor]
            digest = message_hash(unit)
            if self.store.import_message_seen(digest):
                skipped += 1
                cursor += 1
                continue
            message_id = self.store.add_message(
                import_session_id,
                unit["role"],
                unit["content"],
                AgentMode.CHAT,
            )
            self.store.record_import_message(
                digest,
                job_id,
                message_id,
                unit["role"],
            )
            imported_messages += 1
            if unit["role"] == "user" and not flags[cursor]:
                try:
                    imported_memories += len(
                        self.memory.observe_user_message(
                            target_session_id,
                            message_id,
                            unit["content"],
                        )
                    )
                except ValueError:
                    pass
            cursor += 1
            processed += 1
            if (
                max_messages is not None
                and processed >= max(1, int(max_messages))
                and cursor < len(units)
            ):
                status = "paused"
                break
        self.store.update_import_job(
            job_id,
            cursor,
            imported_messages,
            imported_memories,
            skipped,
            status,
        )
        return self._summary(job_id, status, len(units))

    def _summary(self, job_id, status, total):
        job = self.store.get_import_job_by_id(job_id) or {}
        return {
            "job_id": job_id,
            "status": status,
            "cursor": int(job.get("cursor", 0)),
            "total": int(total),
            "imported_messages": int(job.get("imported_messages", 0)),
            "imported_memories": int(job.get("imported_memories", 0)),
            "skipped_duplicates": int(job.get("skipped_duplicates", 0)),
            "import_session_id": "import_{}".format(job_id),
        }
