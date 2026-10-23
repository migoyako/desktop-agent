"""Safe file workspace and note action execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path, PureWindowsPath
import stat
import tempfile
from typing import Dict, Optional

from .models import (
    ActionProposal,
    ActionResult,
    AgentMode,
    ProposalStatus,
    RiskLevel,
    confirmation_digest,
    new_id,
    utc_now,
)


MAX_NOTE_BYTES = 512 * 1024
ALLOWED_NOTE_SUFFIXES = {".md", ".txt"}
BLOCKED_NAMES = {
    ".git",
    ".codex",
    ".agents",
    ".ssh",
    ".aws",
    ".env",
    "node_modules",
}


class FileActionError(RuntimeError):
    pass


def _is_reparse_point(path: Path) -> bool:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return False
    return bool(
        getattr(value, "st_file_attributes", 0) & 0x400
    )


def _assert_not_link(path: Path):
    if path.is_symlink() or _is_reparse_point(path):
        raise FileActionError("不允许访问符号链接或目录联接：{}".format(path))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(path: Path) -> Optional[Dict]:
    if not path.exists():
        return None
    _assert_not_link(path)
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode):
        raise FileActionError("目标不是普通文件。")
    if value.st_size > MAX_NOTE_BYTES:
        raise FileActionError("笔记文件超过大小限制。")
    if value.st_nlink != 1:
        raise FileActionError("不允许操作硬链接文件。")
    return {
        "exists": True,
        "sha256": _sha256(path),
        "size": value.st_size,
        "mode": stat.S_IMODE(value.st_mode),
    }


class WorkspaceGuard:
    """Restricts file actions to one explicitly granted directory."""

    def __init__(self, root):
        raw_root = Path(root)
        if not raw_root.is_absolute():
            raise FileActionError("工作目录必须是绝对路径。")
        text = str(raw_root)
        if text.startswith(("\\\\", "//")):
            raise FileActionError("不允许授权网络共享目录。")
        if raw_root == Path(raw_root.anchor):
            raise FileActionError("不能授权磁盘根目录。")
        if not raw_root.exists() or not raw_root.is_dir():
            raise FileActionError("授权目录不存在或不是文件夹。")
        for part in (raw_root, *raw_root.parents):
            _assert_not_link(part)
        self.root = raw_root.resolve()

    def resolve(self, relative_path: str) -> Path:
        normalized = relative_path.replace("\\", "/")
        pure = PureWindowsPath(normalized)
        if pure.is_absolute() or pure.drive:
            raise FileActionError("文件路径必须相对于授权目录。")
        parts = normalized.split("/")
        if not parts or len(parts) > 8:
            raise FileActionError("文件路径层级过多。")
        for part in parts:
            if (
                part in ("", ".", "..")
                or part.rstrip(" .") != part
                or part.casefold() in BLOCKED_NAMES
                or part.casefold().startswith(".env")
                or any(char in part for char in ':< >"|?*'.replace(" ", ""))
                or any(ord(char) < 32 for char in part)
                or (
                    hasattr(PureWindowsPath(part), "is_reserved")
                    and PureWindowsPath(part).is_reserved()
                )
            ):
                raise FileActionError("文件路径包含越界或不允许的名称。")
        target = self.root.joinpath(*parts)
        current = self.root
        for part in parts[:-1]:
            current = current / part
            if current.exists():
                _assert_not_link(current)
                if not current.is_dir():
                    raise FileActionError("父路径不是文件夹。")
        parent = target.parent
        while parent != self.root:
            if parent.exists():
                _assert_not_link(parent)
            parent = parent.parent
        try:
            common = os.path.commonpath(
                [str(self.root), str(target.resolve(strict=False))]
            )
        except ValueError as error:
            raise FileActionError("文件路径超出授权目录。") from error
        if os.path.normcase(common) != os.path.normcase(str(self.root)):
            raise FileActionError("文件路径超出授权目录。")
        if target.suffix.casefold() not in ALLOWED_NOTE_SUFFIXES:
            raise FileActionError("第一版仅允许写入 .md 或 .txt 笔记。")
        if target.exists():
            _assert_not_link(target)
        return target

    def ensure_notes_directory(self) -> Path:
        notes = self.root / "notes"
        if notes.exists():
            _assert_not_link(notes)
            if not notes.is_dir():
                raise FileActionError("notes 路径不是文件夹。")
        else:
            notes.mkdir()
            _assert_not_link(notes)
        return notes


def safe_note_filename(query: str) -> str:
    cleaned = "".join(
        "-" if char in '<>:"/\\|?*' or ord(char) < 32 else char
        for char in query
    )
    cleaned = "-".join(cleaned.strip(" .").split())
    cleaned = cleaned[:48].strip(" .-") or "note"
    if cleaned.casefold() in {
        "con", "prn", "aux", "nul",
        "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
        "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
    }:
        cleaned = "note-" + cleaned
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    return "notes/{}-{}.md".format(stamp, cleaned)


@dataclass
class NoteActionService:
    store: object
    data_dir: Path

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)

    def create_proposal(
        self,
        session_id: str,
        query: str,
        content: str,
        mode: AgentMode = AgentMode.WORK,
        source_message_id: Optional[int] = None,
    ) -> ActionProposal:
        if len(content.encode("utf-8")) > MAX_NOTE_BYTES:
            raise FileActionError("笔记内容超过大小限制。")
        workspace = self.store.get_workspace()
        if not workspace:
            raise FileActionError("请先选择一个授权工作目录。")
        guard = WorkspaceGuard(workspace)
        relative_path = safe_note_filename(query)
        target = guard.resolve(relative_path)
        expected = fingerprint(target)
        arguments = {
            "relative_path": relative_path,
            "content": content,
            "encoding": "utf-8",
            "source_message_id": source_message_id,
        }
        target_summary = "{}\\{}".format(
            guard.root,
            relative_path.replace("/", "\\"),
        )
        plan = (
            "在授权目录中写入一篇 Markdown 笔记。\n"
            "目标：{}\n"
            "大小：{} 字符\n"
            "已有文件：{}".format(
                target_summary,
                len(content),
                "是，将先备份再覆盖" if expected else "否，将新建文件",
            )
        )
        digest = confirmation_digest(
            "write_note",
            arguments,
            RiskLevel.FS_WRITE.value,
            target_summary,
        )
        return ActionProposal(
            action_id=new_id("act"),
            session_id=session_id,
            mode=mode,
            intent="保存网页搜索笔记",
            tool_name="write_note",
            raw_arguments=dict(arguments),
            normalized_arguments=arguments,
            expected_state={"target": expected},
            risk=RiskLevel.FS_WRITE,
            target_summary=target_summary,
            reversible=True,
            user_visible_plan=plan,
            confirmation_digest=digest,
            idempotency_key=new_id("idem"),
        )

    def execute(self, proposal: ActionProposal) -> ActionResult:
        started = utc_now()
        if proposal.tool_name != "write_note":
            raise FileActionError("不支持的动作：{}".format(proposal.tool_name))
        expected_digest = confirmation_digest(
            proposal.tool_name,
            proposal.normalized_arguments,
            RiskLevel(proposal.risk).value,
            proposal.target_summary,
        )
        if expected_digest != proposal.confirmation_digest:
            raise FileActionError("动作参数与确认内容不一致，已拒绝执行。")
        workspace = self.store.get_workspace()
        if not workspace:
            raise FileActionError("工作目录授权已撤销。")
        guard = WorkspaceGuard(workspace)
        relative_path = proposal.normalized_arguments["relative_path"]
        if not str(relative_path).replace("\\", "/").startswith("notes/"):
            raise FileActionError("第一版只允许把笔记写入 notes 子目录。")
        content = proposal.normalized_arguments["content"]
        target = guard.resolve(relative_path)
        guard.ensure_notes_directory()
        before = fingerprint(target)
        if before != proposal.expected_state.get("target"):
            raise FileActionError("文件在确认前已发生变化，已停止写入。")
        backup_path = ""
        if before:
            backup_dir = self.data_dir / "backups" / proposal.action_id
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / "before.bin"
            data = target.read_bytes()
            if hashlib.sha256(data).hexdigest() != before["sha256"]:
                raise FileActionError("备份时文件发生变化，已停止写入。")
            backup.write_bytes(data)
            backup_path = str(backup)
        data = str(content).encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(
            prefix=".companion-",
            suffix=".tmp",
            dir=str(target.parent),
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            final_before = fingerprint(target)
            if final_before != before:
                raise FileActionError("写入前文件发生变化，已停止。")
            os.replace(temporary, str(target))
        finally:
            try:
                Path(temporary).unlink()
            except FileNotFoundError:
                pass
        after = fingerprint(target)
        finished = utc_now()
        result = {
            "target_path": str(target),
            "relative_path": relative_path,
            "backup_path": backup_path,
            "before": before,
            "after": after,
            "content_preview": str(content)[:300],
        }
        return ActionResult(
            receipt_id=new_id("rcp"),
            action_id=proposal.action_id,
            session_id=proposal.session_id,
            status="completed",
            summary="笔记已写入：{}".format(target),
            result=result,
            started_at=started,
            finished_at=finished,
            undoable=True,
        )

    def undo(self, receipt: ActionResult):
        if not receipt.undoable or receipt.undo_status != "available":
            raise FileActionError("这条回执不可撤销或已经撤销。")
        if receipt.status != "completed":
            raise FileActionError("只有已完成的动作可以撤销。")
        workspace = self.store.get_workspace()
        if not workspace:
            raise FileActionError("工作目录授权已撤销，无法安全撤销。")
        guard = WorkspaceGuard(workspace)
        target = guard.resolve(receipt.result["relative_path"])
        expected_after = receipt.result.get("after")
        if fingerprint(target) != expected_after:
            raise FileActionError("文件已被其他程序修改，不能自动撤销。")
        before = receipt.result.get("before")
        backup_path = receipt.result.get("backup_path")
        if before:
            if not backup_path or not Path(backup_path).exists():
                raise FileActionError("找不到可用的写入前备份。")
            data = Path(backup_path).read_bytes()
            if hashlib.sha256(data).hexdigest() != before["sha256"]:
                raise FileActionError("备份校验失败，不能撤销。")
            descriptor, temporary = tempfile.mkstemp(
                prefix=".companion-undo-",
                suffix=".tmp",
                dir=str(target.parent),
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, str(target))
            finally:
                try:
                    Path(temporary).unlink()
                except FileNotFoundError:
                    pass
        else:
            target.unlink()
        self.store.update_receipt_undo_status(receipt.receipt_id, "undone")
        return "已撤销写入：{}".format(target)
