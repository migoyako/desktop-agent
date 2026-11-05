"""Safe file workspace and note action execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import difflib
import hashlib
import os
from pathlib import Path
import stat
import tempfile
from typing import Dict, List, Optional, Tuple

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


from .path_safety import (
    PathSafetyError,
    WorkspaceGuard,
    assert_not_link,
    resource_identity,
    resource_identity_valid,
)


FileActionError = PathSafetyError


MAX_NOTE_BYTES = 512 * 1024
ALLOWED_NOTE_SUFFIXES = {".md", ".txt"}
PROPOSAL_TTL_HOURS = 24
PREVIEW_DIFF_LINES = 80
PREVIEW_LINE_CHARS = 180


class UncertainExecutionError(FileActionError):
    """The action may have produced a side effect, but the outcome is unknown."""


def proposal_expired(expected_state: Dict, now=None) -> bool:
    value = (expected_state or {}).get("expires_at")
    if not value:
        return False
    try:
        expiry = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current > expiry


def diff_lines(before, after):
    """Line diff for confirmation cards; long content is clipped, never hidden."""
    rows: List[Tuple[str, str]] = []
    added = removed = 0
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            rows.extend(("ctx", line) for line in before[i1:i2])
        elif tag == "delete":
            removed += i2 - i1
            rows.extend(("del", line) for line in before[i1:i2])
        elif tag == "insert":
            added += j2 - j1
            rows.extend(("add", line) for line in after[j1:j2])
        else:
            removed += i2 - i1
            added += j2 - j1
            rows.extend(("del", line) for line in before[i1:i2])
            rows.extend(("add", line) for line in after[j1:j2])
    omitted = max(0, len(rows) - PREVIEW_DIFF_LINES)
    return (
        [(tag, _clip_preview_line(line)) for tag, line in rows[:PREVIEW_DIFF_LINES]],
        added,
        removed,
        omitted,
    )


def _clip_preview_line(line) -> str:
    text = str(line).replace("\t", "    ")
    if len(text) <= PREVIEW_LINE_CHARS:
        return text
    return text[: PREVIEW_LINE_CHARS - 1] + "…"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(path: Path) -> Optional[Dict]:
    if not path.exists():
        return None
    assert_not_link(path)
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


class NoteGuard(WorkspaceGuard):
    """Workspace guard narrowed to the note whitelist.

    The suffix rule lives in the subclass so a generic file executor cannot pick up
    the note whitelist by reusing the shared guard, and cannot drop it by forgetting
    to call a checker.
    """

    def resolve(self, relative_path: str) -> Path:
        target = super().resolve(relative_path)
        if target.suffix.casefold() not in ALLOWED_NOTE_SUFFIXES:
            raise FileActionError("第一版仅允许写入 .md 或 .txt 笔记。")
        return target

    def ensure_notes_directory(self) -> Path:
        notes = self.root / "notes"
        if notes.exists():
            assert_not_link(notes)
            if not notes.is_dir():
                raise FileActionError("notes 路径不是文件夹。")
        else:
            notes.mkdir()
            assert_not_link(notes)
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
        intent: str = "保存网页搜索笔记",
        ttl_hours: float = PROPOSAL_TTL_HOURS,
    ) -> ActionProposal:
        if len(content.encode("utf-8")) > MAX_NOTE_BYTES:
            raise FileActionError("笔记内容超过大小限制。")
        workspace = self.store.get_workspace()
        if not workspace:
            raise FileActionError("请先选择一个授权工作目录。")
        guard = NoteGuard(workspace)
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
        expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=float(ttl_hours))
        ).isoformat()
        preview = str(content)[:400]
        if len(str(content)) > 400:
            preview += "\n…"
        plan = (
            "在授权目录中写入一篇 Markdown 笔记。\n"
            "目标：{}\n"
            "大小：{} 字符\n"
            "已有文件：{}\n"
            "内容预览：\n{}".format(
                target_summary,
                len(content),
                "是，将先备份再覆盖" if expected else "否，将新建文件",
                preview,
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
            intent=intent,
            tool_name="write_note",
            raw_arguments=dict(arguments),
            normalized_arguments=arguments,
            expected_state={
                "target": expected,
                "workspace": resource_identity(guard.root),
                "expires_at": expires_at,
            },
            risk=RiskLevel.FS_WRITE,
            target_summary=target_summary,
            reversible=True,
            user_visible_plan=plan,
            confirmation_digest=digest,
            idempotency_key=new_id("idem"),
        )

    def preview(self, proposal: ActionProposal) -> Dict:
        """Read-only description of what confirm would change; never writes."""
        value = {
            "change": "unavailable",
            "workspace": "",
            "relative_path": str(
                (proposal.normalized_arguments or {}).get("relative_path") or ""
            ),
            "diff": [],
            "added": 0,
            "removed": 0,
            "truncated": False,
            "omitted_lines": 0,
            "error": "",
        }
        if proposal.tool_name != "write_note":
            value["error"] = "暂不支持预览这个动作。"
            return value
        snapshot = (proposal.expected_state or {}).get("workspace") or {}
        root = str(snapshot.get("root") or "")
        value["workspace"] = root
        if not root:
            value["error"] = "提案缺少工作区快照。"
            return value
        if not resource_identity_valid(snapshot):
            value["error"] = "工作目录身份已变化，原确认可能失效。"
            return value
        try:
            guard = NoteGuard(root)
            target = guard.resolve(value["relative_path"])
            before = ""
            if target.exists():
                fingerprint(target)
                before = target.read_bytes().decode("utf-8", errors="replace")
                value["change"] = "update"
            else:
                value["change"] = "create"
        except (FileActionError, OSError) as error:
            value["error"] = str(error)
            return value
        content = str((proposal.normalized_arguments or {}).get("content") or "")
        rows, added, removed, omitted = diff_lines(
            before.splitlines(),
            content.splitlines(),
        )
        value.update(
            {
                "diff": rows,
                "added": added,
                "removed": removed,
                "truncated": omitted > 0,
                "omitted_lines": omitted,
            }
        )
        return value

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
        if proposal_expired(proposal.expected_state):
            raise FileActionError("提案已过期，请重新生成。")
        workspace_snapshot = proposal.expected_state.get("workspace")
        if not workspace_snapshot:
            raise FileActionError("提案缺少工作区快照，请重新生成。")
        if not resource_identity_valid(workspace_snapshot):
            raise FileActionError("工作目录身份已变化，已拒绝执行。")
        guard = NoteGuard(workspace_snapshot["root"])
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
        content_sha256 = hashlib.sha256(data).hexdigest()
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
            self.store.mark_execution_state(
                proposal.action_id,
                "dispatching",
            )
            try:
                os.replace(temporary, str(target))
            except Exception as error:
                current = None
                try:
                    current = fingerprint(target)
                except Exception:
                    current = None
                if current and current.get("sha256") == content_sha256:
                    raise UncertainExecutionError(
                        "文件可能已经写入，但执行回执没有完成。"
                    ) from error
                raise FileActionError("写入失败：{}".format(error)) from error
            try:
                self.store.mark_execution_state(
                    proposal.action_id,
                    "replaced",
                )
            except Exception as error:
                raise UncertainExecutionError(
                    "文件已经替换，但执行状态无法记录。"
                ) from error
        finally:
            try:
                Path(temporary).unlink()
            except FileNotFoundError:
                pass
        try:
            after = fingerprint(target)
        except Exception as error:
            raise UncertainExecutionError(
                "文件已经替换，但无法确认最终状态。"
            ) from error
        finished = utc_now()
        result = {
            "target_path": str(target),
            "relative_path": relative_path,
            "backup_path": backup_path,
            "before": before,
            "after": after,
            "workspace": workspace_snapshot,
            "workspace_root": workspace_snapshot["root"],
            "content_sha256": content_sha256,
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
        workspace_snapshot = receipt.result.get("workspace")
        if not workspace_snapshot:
            raise FileActionError("回执缺少工作区快照，无法安全撤销。")
        if not resource_identity_valid(workspace_snapshot):
            raise FileActionError("工作目录身份已变化，无法安全撤销。")
        guard = NoteGuard(workspace_snapshot["root"])
        target = guard.resolve(receipt.result["relative_path"])
        recorded_target = receipt.result.get("target_path")
        if recorded_target and os.path.normcase(str(target)) != os.path.normcase(
            str(recorded_target)
        ):
            raise FileActionError("撤销目标与回执不一致。")
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
