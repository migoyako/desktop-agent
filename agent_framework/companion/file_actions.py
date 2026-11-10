"""Safe file workspace and note action execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import difflib
import hashlib
import os
from pathlib import Path
import secrets
import stat
from typing import Dict, List, Optional, Tuple

from .models import (
    ActionProposal,
    ActionResult,
    AgentMode,
    EXECUTOR_FILE,
    ProposalStatus,
    RiskLevel,
    confirmation_digest,
    new_id,
    utc_now,
)


from .access_control import (
    CAPABILITY_FS_DELETE,
    CAPABILITY_FS_READ,
    CAPABILITY_FS_WRITE,
    AccessControlError,
)
from .path_safety import (
    PathSafetyError,
    WorkspaceGuard,
    assert_not_link,
    resource_identity,
    resource_identity_valid,
)
from .recycle import RecycleBinError, send_to_recycle_bin
from .runtime_log import write_event


FileActionError = PathSafetyError


MAX_NOTE_BYTES = 512 * 1024
ALLOWED_NOTE_SUFFIXES = {".md", ".txt"}
PROPOSAL_TTL_HOURS = 24
PREVIEW_DIFF_LINES = 80
PREVIEW_LINE_CHARS = 180

FILE_OPERATION_WRITE = "write"
FILE_OPERATION_COPY = "copy"
FILE_OPERATION_MOVE = "move"
FILE_OPERATION_DELETE = "delete"
FILE_OPERATIONS = (
    FILE_OPERATION_WRITE,
    FILE_OPERATION_COPY,
    FILE_OPERATION_MOVE,
    FILE_OPERATION_DELETE,
)
# 一次写入或复制能搬动的正文字节数上限。
MAX_FILE_WRITE_BYTES = 4 * 1024 * 1024
# 超过这个大小就不再留副本：与其悄悄占满数据盘，不如把"撤销不了"写在提案里。
MAX_UNDO_BACKUP_BYTES = 32 * 1024 * 1024
# 移动和删除只改目录项，不读正文；这条线挡的是明显不正常的巨型文件。
MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
# 变化检测要算哈希的上限；再大的文件用"大小 + 修改时间"判断就够了。
MAX_STAMP_HASH_BYTES = 64 * 1024 * 1024
# 确认卡上的 Diff 只读这么多样本；更大的已有文件照实说"没读全文"。
MAX_PREVIEW_READ_BYTES = MAX_NOTE_BYTES


class UncertainExecutionError(FileActionError):
    """The action may have produced a side effect, but the outcome is unknown."""


class NotExecutedError(FileActionError):
    """Refused before the first side-effecting call, so the receipt is honest."""


# 名字撞车才重试；权限拒绝一次就停，见 _new_temp_file。
TEMP_CREATE_ATTEMPTS = 8


def _new_temp_file(directory: Path, prefix: str) -> Tuple[int, str]:
    """Fail fast: mkstemp spins TMP_MAX (2**31-1) retries in a denied Windows dir."""
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    for _ in range(TEMP_CREATE_ATTEMPTS):
        temporary = str(Path(directory) / (prefix + secrets.token_hex(8) + ".tmp"))
        try:
            descriptor = os.open(temporary, flags, 0o600)
        except FileExistsError:
            continue
        except PermissionError as error:
            raise NotExecutedError(
                "目录拒绝创建新文件（Windows 权限、完整性级别或安全软件限制），"
                "动作没有执行：{}".format(error)
            ) from error
        return descriptor, temporary
    raise NotExecutedError(
        "临时文件名连续 {} 次被占用，动作没有执行。".format(TEMP_CREATE_ATTEMPTS)
    )


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


def stamp(path: Path, max_bytes: int = MAX_FILE_BYTES) -> Optional[Dict]:
    """通用文件动作的身份快照；超过哈希上限就不再读全文。

    ``max_bytes`` 是"这个动作能不能处理这么大的文件"的拒绝线，不是哈希线：同一个文件
    用不同的 ``max_bytes`` 取到的快照内容一致（都不超限时），所以提案存的和执行后比的
    可以直接相等比较。
    """
    if not path.exists():
        return None
    assert_not_link(path)
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode):
        raise FileActionError("目标不是普通文件。")
    if value.st_nlink != 1:
        raise FileActionError("不允许操作硬链接文件。")
    if value.st_size > max_bytes:
        raise FileActionError("文件超过 {} 字节的大小限制。".format(max_bytes))
    return {
        "exists": True,
        "sha256": _sha256(path) if value.st_size <= MAX_STAMP_HASH_BYTES else "",
        "size": value.st_size,
        "mtime_ns": int(value.st_mtime_ns),
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

    def precheck(self, proposal: ActionProposal, current_workspace) -> Tuple[str, str]:
        """确认这一刻还要成立的那条笔记专属不变式：目标仍在当前工作区里。

        返回 ``(code, reason)``，``code`` 为空表示通过。工作区是聊天窗口选的那个目录，
        笔记只能写它里面的 ``notes/``；通用文件执行器不受它约束，所以它自己返回空。
        """
        snapshot = (proposal.expected_state or {}).get("workspace") or {}
        if not snapshot or not current_workspace:
            return "workspace_missing", "工作目录授权已撤销，原确认失效。"
        if os.path.normcase(os.path.realpath(current_workspace)) != os.path.normcase(
            str(snapshot.get("root", ""))
        ):
            return "workspace_changed", "工作目录已切换，原确认失效，请重新生成提案。"
        if not resource_identity_valid(snapshot):
            return (
                "workspace_identity_changed",
                "工作目录身份已变化，原确认失效。",
            )
        return "", ""

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
        descriptor, temporary = _new_temp_file(target.parent, ".companion-")
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
            descriptor, temporary = _new_temp_file(
                target.parent, ".companion-undo-"
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


def volume_of(path: Path) -> int:
    """文件所在的磁盘卷标识。

    同卷移动是改一次目录项，瞬时完成、中途不留副本；跨卷就是"复制一份再删原件"，
    中间态大不一样，所以本版本只在同卷内移动。Windows 上 ``st_dev`` 是卷序列号，
    同一卷里的不同目录给出同一个值，可以直接比较。
    """
    return int(path.lstat().st_dev)


def _nearest_existing(path: Path) -> Path:
    """往上找到第一个真实存在的祖先，用来在目标还没落盘时问出它在哪一卷。"""
    current = path
    while not current.exists() and current.parent != current:
        current = current.parent
    return current


def _display_path(root, relative_path: str) -> str:
    return str(Path(str(root)) / str(relative_path).replace("/", "\\"))


def _grant_snapshot(grant, capability: str) -> Dict:
    """把"这条动作依赖哪个目录、依赖它的哪一种能力"冻进提案。

    能力名也冻进去：撤销授权、重新授权成只读、目录被换走，这三种都必须在执行前
    复核时被抓出来，而不是等到写盘那一刻。
    """
    return {
        "grant_id": str(grant.grant_id),
        "root": str(grant.canonical_root),
        "capability": str(capability),
        "identity": resource_identity(grant.canonical_root),
    }


@dataclass
class FileActionService:
    """授权目录里的通用文件执行器：写入、复制、移动/重命名、放进回收站。

    与 ``NoteActionService`` 的差别不只是少了 ``notes/`` 白名单：这里的目标一律来自
    ``grant_id + 相对路径`` 或 ``file_ref``，提案和执行各查一次授权与目录身份，而
    "删除"只有系统回收站一条路——这个类里不存在永久删除的分支。
    """

    store: object
    data_dir: Path
    access: object
    file_index: object

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)

    # -- 授权与引用 ---------------------------------------------------------

    def writable_grants(self):
        """真正能承接通用写入的授权：只认用户显式授予的目录能力。"""
        return [
            grant
            for grant in self.access.grants_for_capability(
                CAPABILITY_FS_WRITE, exclude_legacy=True
            )
            if not self.file_index.is_agent_data_directory(grant.canonical_root)
        ]

    def deletable_grants(self):
        """真正能承接回收站删除的授权，旧工作区授权不算。"""
        return [
            grant
            for grant in self.access.grants_for_capability(
                CAPABILITY_FS_DELETE, exclude_legacy=True
            )
            if not self.file_index.is_agent_data_directory(grant.canonical_root)
        ]

    def _grant_for(self, grant_id, capability: str):
        try:
            grant = self.access.get_grant(str(grant_id or ""))
        except AccessControlError as error:
            raise NotExecutedError(str(error)) from error
        if grant is None:
            raise NotExecutedError("找不到这条目录授权，动作没有执行。")
        return self._checked_grant(grant, capability)

    def _refuse_implicit(self, grant, capability: str) -> None:
        """通用写入与删除只认用户为这个能力单独挑的目录。"""
        if capability not in (CAPABILITY_FS_WRITE, CAPABILITY_FS_DELETE):
            return
        if grant.legacy_workspace:
            raise NotExecutedError(
                "这条授权是旧版工作区自动提升来的，只代表笔记还能写；"
                "通用文件写入与删除需要把这个目录单独授予 {}。".format(capability)
            )
        if self.file_index.is_agent_data_directory(grant.canonical_root):
            raise NotExecutedError(
                "这个目录是本代理自己的数据目录，通用文件动作不接受它。"
            )

    def _checked_grant(self, grant, capability: str):
        try:
            matched, reason = self.access.grant_for_capability(
                grant.path, capability
            )
        except AccessControlError as error:
            raise NotExecutedError(str(error)) from error
        if matched is None:
            raise NotExecutedError(reason)
        self._refuse_implicit(matched, capability)
        guard = WorkspaceGuard(matched.canonical_root)
        return matched, guard

    def _source_for(self, file_ref, capability: str):
        row, grant, reason = self.file_index.resolve_reference(
            file_ref, capability
        )
        if grant is None or row is None:
            raise NotExecutedError(reason or "找不到这个文件引用，动作没有执行。")
        self._refuse_implicit(grant, capability)
        guard = WorkspaceGuard(grant.canonical_root)
        rel_path = str(row.get("rel_path") or "")
        try:
            path = guard.resolve(rel_path)
        except PathSafetyError as error:
            raise NotExecutedError(str(error)) from error
        return row, grant, guard, path

    def _recheck_snapshot(self, recorded: Dict):
        """执行前按提案里冻住的那份快照重新查一次授权，不接受任何新的授权 ID。"""
        if not recorded:
            raise NotExecutedError("提案缺少目录授权快照，动作没有执行。")
        capability = str(recorded.get("capability") or "")
        try:
            grant = self.access.get_grant(str(recorded.get("grant_id") or ""))
        except AccessControlError as error:
            raise NotExecutedError(str(error)) from error
        if grant is None:
            raise NotExecutedError("找不到这条目录授权，动作没有执行。")
        matched, guard = self._checked_grant(grant, capability)
        if os.path.normcase(str(guard.root)) != os.path.normcase(
            str(recorded.get("root") or "")
        ):
            raise NotExecutedError("授权目录与提案记录的不是同一个路径，动作没有执行。")
        if not resource_identity_valid(recorded.get("identity") or {}):
            raise NotExecutedError("目录身份已变化，原确认失效，请重新生成提案。")
        return matched, guard

    def _ensure_parent(self, target: Path, root: Path) -> None:
        """在授权目录内补齐目标文件的父目录，逐层拒绝符号链接。"""
        relative = target.parent.relative_to(root)
        current = root
        for part in relative.parts:
            current = current / part
            if current.exists():
                assert_not_link(current)
                if not current.is_dir():
                    raise NotExecutedError("目标路径的上一层不是文件夹，动作没有执行。")
            else:
                current.mkdir()
                assert_not_link(current)

    # -- 提案 ---------------------------------------------------------------

    def create_write_proposal(
        self,
        session_id: str,
        grant_id: str,
        relative_path: str,
        content: str,
        mode: AgentMode = AgentMode.EXECUTE,
        source_message_id: Optional[int] = None,
        intent: str = "写入授权目录中的文件",
        tool_name: str = "write_file",
        ttl_hours: float = PROPOSAL_TTL_HOURS,
    ) -> ActionProposal:
        data = str(content or "").encode("utf-8")
        if not data:
            raise NotExecutedError("写入内容不能为空。")
        if len(data) > MAX_FILE_WRITE_BYTES:
            raise NotExecutedError(
                "写入内容超过 {} 字节上限。".format(MAX_FILE_WRITE_BYTES)
            )
        grant, guard = self._grant_for(grant_id, CAPABILITY_FS_WRITE)
        target = guard.resolve(str(relative_path))
        before = stamp(target, MAX_UNDO_BACKUP_BYTES)
        target_summary = _display_path(guard.root, relative_path)
        preview = str(content)[:400]
        if len(str(content)) > 400:
            preview += "\n…"
        plan = (
            "在授权目录里{}一个文件。\n"
            "目标：{}\n"
            "大小：{} 字节\n"
            "已有文件：{}\n"
            "父目录：{}\n"
            "内容预览：\n{}"
        ).format(
            "覆盖" if before else "新建",
            target_summary,
            len(data),
            "是，将先备份再覆盖" if before else "否，将新建文件",
            "已存在" if target.parent.exists() else "将一并创建",
            preview,
        )
        return self._build(
            session_id=session_id,
            tool_name=tool_name,
            operation=FILE_OPERATION_WRITE,
            mode=mode,
            intent=intent,
            arguments={
                "grant_id": str(grant_id),
                "relative_path": str(relative_path),
                "content": str(content),
                "encoding": "utf-8",
                "source_message_id": source_message_id,
            },
            expected_state={
                "operation": FILE_OPERATION_WRITE,
                "expires_at": self._expires(ttl_hours),
                "target": before,
                "workspace": resource_identity(guard.root),
                "grants": {"target": _grant_snapshot(grant, CAPABILITY_FS_WRITE)},
            },
            risk=RiskLevel.FS_WRITE,
            target_summary=target_summary,
            plan=plan,
            reversible=True,
        )

    def _transfer_proposal(
        self,
        operation: str,
        session_id: str,
        file_ref: str,
        target_grant_id: str,
        target_rel_path: str,
        mode: AgentMode = AgentMode.EXECUTE,
        source_message_id: Optional[int] = None,
        intent: str = "",
        tool_name: Optional[str] = None,
        ttl_hours: float = PROPOSAL_TTL_HOURS,
    ) -> ActionProposal:
        copying = operation == FILE_OPERATION_COPY
        tool_name = str(tool_name or ("copy_file" if copying else "move_file"))
        intent = str(intent or ("复制文件到授权目录" if copying else "移动或重命名文件"))
        source_capability = (
            CAPABILITY_FS_READ if operation == FILE_OPERATION_COPY
            else CAPABILITY_FS_WRITE
        )
        row, source_grant, source_guard, source = self._source_for(
            file_ref, source_capability
        )
        before_source = stamp(source, self._caps(operation)[0])
        if before_source is None:
            raise NotExecutedError("引用指向的文件已不存在，动作没有执行。")
        target_grant, target_guard = self._grant_for(
            target_grant_id, CAPABILITY_FS_WRITE
        )
        target = target_guard.resolve(str(target_rel_path))
        before_target = stamp(target, self._caps(operation)[1])
        verb = "复制" if operation == FILE_OPERATION_COPY else "移动/重命名"
        source_summary = _display_path(source_guard.root, row["rel_path"])
        target_summary = "{} → {}".format(
            source_summary, _display_path(target_guard.root, target_rel_path)
        )
        if operation == FILE_OPERATION_MOVE and before_target:
            raise NotExecutedError(
                "目标位置已有同名文件，移动没有执行；请先改名或先把它移进回收站。"
            )
        source_device = volume_of(source)
        target_device = volume_of(
            _nearest_existing(target if before_target else target.parent)
        )
        if operation == FILE_OPERATION_MOVE and source_device != target_device:
            raise NotExecutedError(
                "源文件与目标位置不在同一个磁盘卷，跨卷移动要先复制再删，"
                "本版本不做，动作没有执行。"
            )
        notes = []
        if operation == FILE_OPERATION_MOVE and os.path.normcase(
            str(source_guard.root)
        ) != os.path.normcase(str(target_guard.root)):
            notes.append("源目录与目标目录不同，两次授权都要成立，移动才会执行。")
        plan = (
            "把{}一个文件。\n"
            "源：{}（{} 字节，修改于 {}）\n"
            "目标：{}\n"
            "目标已存在：{}\n"
            "父目录：{}\n"
            "{}"
        ).format(
            verb,
            source_summary,
            before_source["size"],
            row.get("mtime") or "未知",
            _display_path(target_guard.root, target_rel_path),
            "是，将先备份再覆盖" if before_target else "否，将新建",
            "已存在" if target.parent.exists() else "将一并创建",
            "\n".join(notes),
        )
        return self._build(
            session_id=session_id,
            tool_name=tool_name,
            operation=operation,
            mode=mode,
            intent=intent,
            arguments={
                "file_ref": str(file_ref),
                "target_grant_id": str(target_grant_id),
                "target_rel_path": str(target_rel_path),
                "source_message_id": source_message_id,
            },
            expected_state={
                "operation": operation,
                "expires_at": self._expires(ttl_hours),
                "source": before_source,
                "source_rel_path": str(row.get("rel_path") or ""),
                "target": before_target,
                "target_root": str(target_guard.root),
                "devices": {"source": source_device, "target": target_device},
                "workspace": resource_identity(target_guard.root),
                "grants": {
                    "source": _grant_snapshot(source_grant, source_capability),
                    "target": _grant_snapshot(target_grant, CAPABILITY_FS_WRITE),
                },
            },
            risk=RiskLevel.FS_WRITE,
            target_summary=target_summary,
            plan=plan,
            reversible=True,
        )

    def create_copy_proposal(self, *args, **kwargs) -> ActionProposal:
        return self._transfer_proposal(FILE_OPERATION_COPY, *args, **kwargs)

    def create_move_proposal(self, *args, **kwargs) -> ActionProposal:
        return self._transfer_proposal(FILE_OPERATION_MOVE, *args, **kwargs)

    def create_delete_proposal(
        self,
        session_id: str,
        file_ref: str,
        mode: AgentMode = AgentMode.EXECUTE,
        source_message_id: Optional[int] = None,
        intent: str = "把文件移入系统回收站",
        tool_name: str = "delete_file",
        ttl_hours: float = PROPOSAL_TTL_HOURS,
    ) -> ActionProposal:
        row, grant, guard, path = self._source_for(file_ref, CAPABILITY_FS_DELETE)
        before = stamp(path, MAX_FILE_BYTES)
        if before is None:
            raise NotExecutedError("引用指向的文件已不存在，动作没有执行。")
        backupable = int(before["size"]) <= MAX_UNDO_BACKUP_BYTES
        undo_reason = (
            ""
            if backupable
            else "文件超过 {} 字节，不复制备份；进回收站后本机没有可自动还原的副本。".format(
                MAX_UNDO_BACKUP_BYTES
            )
        )
        target_summary = _display_path(guard.root, row["rel_path"])
        plan = (
            "把一个文件移入系统回收站，不是永久删除。\n"
            "目标：{}\n"
            "大小：{} 字节\n"
            "备份：{}\n"
            "撤销：{}\n"
            "提示：文件会离开授权目录，进入这台电脑回收站里"
            "的原始位置，随时可以在资源管理器里还原。"
        ).format(
            target_summary,
            before["size"],
            "写入本机动作备份（{} 字节）".format(before["size"]) if backupable
            else "不做备份",
            "可用（从备份还原到原路径）" if backupable else undo_reason,
        )
        return self._build(
            session_id=session_id,
            tool_name=tool_name,
            operation=FILE_OPERATION_DELETE,
            mode=mode,
            intent=intent,
            arguments={
                "file_ref": str(file_ref),
                "source_message_id": source_message_id,
            },
            expected_state={
                "operation": FILE_OPERATION_DELETE,
                "expires_at": self._expires(ttl_hours),
                "source": before,
                "source_rel_path": str(row.get("rel_path") or ""),
                "target": None,
                "workspace": resource_identity(guard.root),
                "undo_reason": undo_reason,
                "grants": {"source": _grant_snapshot(grant, CAPABILITY_FS_DELETE)},
            },
            risk=RiskLevel.FS_DELETE,
            target_summary=target_summary,
            plan=plan,
            reversible=backupable,
        )

    @staticmethod
    def _expires(ttl_hours: float) -> str:
        return (
            datetime.now(timezone.utc) + timedelta(hours=float(ttl_hours))
        ).isoformat()

    def _build(
        self,
        *,
        session_id: str,
        tool_name: str,
        operation: str,
        mode: AgentMode,
        intent: str,
        arguments: Dict,
        expected_state: Dict,
        risk: RiskLevel,
        target_summary: str,
        plan: str,
        reversible: bool,
    ) -> ActionProposal:
        arguments = dict(arguments)
        arguments["operation"] = operation
        digest = confirmation_digest(
            tool_name, arguments, RiskLevel(risk).value, target_summary
        )
        return ActionProposal(
            action_id=new_id("act"),
            session_id=session_id,
            mode=mode,
            intent=intent,
            tool_name=tool_name,
            raw_arguments=dict(arguments),
            normalized_arguments=arguments,
            expected_state=expected_state,
            risk=risk,
            target_summary=target_summary,
            reversible=reversible,
            user_visible_plan=plan,
            confirmation_digest=digest,
            idempotency_key=new_id("idem"),
            executor=EXECUTOR_FILE,
        )

    def precheck(self, proposal: ActionProposal, current_workspace) -> Tuple[str, str]:
        """这里没有全局工作区可比：授权目录由提案自己带着，执行前逐条复核。"""
        return "", ""

    # -- 预览 ---------------------------------------------------------------

    def preview(self, proposal: ActionProposal) -> Dict:
        """只读地说明这次确认会改什么；不建目录、不写盘、不消费提案。"""
        state = proposal.expected_state or {}
        operation = str(state.get("operation") or "")
        arguments = proposal.normalized_arguments or {}
        value = {
            "change": operation or "unavailable",
            "operation": operation,
            "workspace": str((state.get("workspace") or {}).get("root") or ""),
            "relative_path": str(arguments.get("relative_path") or ""),
            "target_relative_path": str(arguments.get("target_rel_path") or ""),
            "source_path": "",
            "diff": [],
            "added": 0,
            "removed": 0,
            "truncated": False,
            "omitted_lines": 0,
            "error": "",
            "undo_reason": str(state.get("undo_reason") or ""),
        }
        if operation not in FILE_OPERATIONS:
            value["error"] = "暂不支持预览这个文件动作。"
            return value
        roles = state.get("grants") or {}
        for role in ("target", "source"):
            recorded = roles.get(role)
            if not recorded:
                continue
            try:
                self._recheck_snapshot(recorded)
            except (FileActionError, OSError) as error:
                value["error"] = str(error)
                return value
        try:
            if operation == FILE_OPERATION_WRITE:
                self._preview_write(proposal, value)
            elif operation in (FILE_OPERATION_COPY, FILE_OPERATION_MOVE):
                self._preview_transfer(proposal, value)
            else:
                self._preview_delete(proposal, value)
        except (FileActionError, OSError) as error:
            value["error"] = str(error)
        return value

    def _preview_write(self, proposal, value):
        recorded = (proposal.expected_state["grants"])["target"]
        _, guard = self._recheck_snapshot(recorded)
        target = guard.resolve(str(proposal.normalized_arguments["relative_path"]))
        current = stamp(target, MAX_FILE_BYTES)
        value["change"] = "update" if current else "create"
        value["workspace"] = str(guard.root)
        content = str(proposal.normalized_arguments.get("content") or "")
        before = ""
        if current:
            if int(current["size"]) > MAX_PREVIEW_READ_BYTES:
                value["error"] = "已有文件超过 {} 字节，不读取全文做 Diff。".format(
                    MAX_PREVIEW_READ_BYTES
                )
                return
            before = target.read_bytes().decode("utf-8", errors="replace")
        self._attach_diff(value, before, content)

    def _preview_transfer(self, proposal, value):
        state = proposal.expected_state
        source_recorded = state["grants"]["source"]
        target_recorded = state["grants"]["target"]
        _, source_guard = self._recheck_snapshot(source_recorded)
        _, target_guard = self._recheck_snapshot(target_recorded)
        source = source_guard.resolve(str(state.get("source_rel_path") or ""))
        target = target_guard.resolve(
            str(proposal.normalized_arguments["target_rel_path"])
        )
        value["source_path"] = str(source)
        value["workspace"] = str(target_guard.root)
        value["change"] = (
            "copy" if state.get("operation") == FILE_OPERATION_COPY else "move"
        )
        value["target_relative_path"] = str(
            proposal.normalized_arguments["target_rel_path"]
        )
        if target.exists():
            value["error"] = "目标位置已有同名文件，确认前请先改名。"
            return
        if not source.exists():
            value["error"] = "源文件已不在原位置，原确认可能失效。"

    def _preview_delete(self, proposal, value):
        state = proposal.expected_state
        recorded = state["grants"]["source"]
        _, guard = self._recheck_snapshot(recorded)
        source = guard.resolve(str(state.get("source_rel_path") or ""))
        value["source_path"] = str(source)
        value["workspace"] = str(guard.root)
        value["change"] = "delete"
        if not source.exists():
            value["error"] = "文件已不在原位置，原确认可能失效。"
            return
        value["diff"] = [("del", line) for line in "(整个文件将移入回收站)".splitlines()]
        value["removed"] = int((state.get("source") or {}).get("size") or 0)

    @staticmethod
    def _attach_diff(value, before, after):
        rows, added, removed, omitted = diff_lines(
            before.splitlines(), after.splitlines()
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

    # -- 执行 ---------------------------------------------------------------

    def execute(self, proposal: ActionProposal) -> ActionResult:
        started = utc_now()
        operation = str((proposal.normalized_arguments or {}).get("operation") or "")
        write_event(
            self.data_dir,
            "file.execute.start",
            action_id=proposal.action_id,
            tool_name=proposal.tool_name,
            operation=operation,
        )
        if operation not in FILE_OPERATIONS:
            raise NotExecutedError("不支持的文件动作：{}".format(operation or "未指定"))
        self._verify_digest(proposal)
        if proposal_expired(proposal.expected_state):
            raise NotExecutedError("提案已过期，请重新生成。")
        state = proposal.expected_state or {}
        handlers = {
            FILE_OPERATION_WRITE: self._run_write,
            FILE_OPERATION_COPY: self._run_transfer,
            FILE_OPERATION_MOVE: self._run_transfer,
            FILE_OPERATION_DELETE: self._run_delete,
        }
        payload, summary, undoable = handlers[operation](proposal, state)
        self._refresh_file_index(payload)
        write_event(
            self.data_dir,
            "file.execute.done",
            action_id=proposal.action_id,
            operation=operation,
        )
        payload["operation"] = operation
        payload["workspace_root"] = payload.get("target_root") or payload.get(
            "source_root", ""
        )
        return ActionResult(
            receipt_id=new_id("rcp"),
            action_id=proposal.action_id,
            session_id=proposal.session_id,
            status="completed",
            summary=summary,
            result=payload,
            started_at=started,
            finished_at=utc_now(),
            undoable=undoable,
            undo_status="available" if undoable else "unavailable",
        )

    def _refresh_file_index(self, payload: Dict) -> None:
        """Keep local search usable immediately after an authorized file change."""
        for key in ("target_root", "source_root"):
            root = str(payload.get(key) or "").strip()
            if not root:
                continue
            grant = self.access.find_grant(root)
            if grant is not None:
                self.file_index.schedule_index(grant.grant_id)
                return

    def _verify_digest(self, proposal: ActionProposal):
        expected = confirmation_digest(
            proposal.tool_name,
            proposal.normalized_arguments,
            RiskLevel(proposal.risk).value,
            proposal.target_summary,
        )
        if expected != proposal.confirmation_digest:
            raise NotExecutedError("动作参数与确认内容不一致，已拒绝执行。")

    def _reserve(self, action_id: str, backup_name: str, data: bytes) -> str:
        write_event(
            self.data_dir,
            "file.backup.start",
            action_id=action_id,
            bytes=len(data),
        )
        backup_dir = self.data_dir / "backups" / action_id
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / backup_name
        backup.write_bytes(data)
        if hashlib.sha256(backup.read_bytes()).hexdigest() != hashlib.sha256(
            data
        ).hexdigest():
            raise NotExecutedError("备份写入后校验失败，动作没有执行。")
        write_event(
            self.data_dir,
            "file.backup.done",
            action_id=action_id,
            bytes=len(data),
        )
        return str(backup)

    def _replace(self, target: Path, data: bytes, before, action_id: str) -> str:
        """临时文件 + fsync + 再校验 + 一次 ``os.replace``；返回备份路径。"""
        write_event(
            self.data_dir,
            "file.replace.start",
            action_id=action_id,
            target=str(target),
            bytes=len(data),
            has_before=bool(before),
        )
        backup_path = ""
        if before:
            source = target.read_bytes()
            if hashlib.sha256(source).hexdigest() != before["sha256"]:
                raise NotExecutedError("备份时文件内容对不上校验和，已停止写入。")
            backup_path = self._reserve(action_id, "before.bin", source)
        descriptor, temporary = _new_temp_file(target.parent, ".companion-")
        write_event(
            self.data_dir,
            "file.temp.created",
            action_id=action_id,
            temporary=str(temporary),
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            write_event(
                self.data_dir,
                "file.temp.fsynced",
                action_id=action_id,
                bytes=len(data),
            )
            if stamp(target, MAX_UNDO_BACKUP_BYTES) != before:
                raise NotExecutedError("写入前文件发生变化，已停止。")
            # 账本的中间状态只有 running/dispatching/replaced 三种（恢复逻辑按这三步
            # 判断"上次有没有走到没留回执的地方"），所以这里复用而不是另起名字。
            self.store.mark_execution_state(action_id, "dispatching")
            write_event(
                self.data_dir,
                "file.replace.dispatching",
                action_id=action_id,
            )
            try:
                os.replace(temporary, str(target))
            except Exception as error:
                if stamp(target, MAX_UNDO_BACKUP_BYTES) == before:
                    raise NotExecutedError(
                        "替换失败，目标文件没有变化：{}".format(error)
                    ) from error
                raise UncertainExecutionError(
                    "文件可能已经写入，但执行回执没有完成。"
                ) from error
            try:
                self.store.mark_execution_state(action_id, "replaced")
            except Exception as error:
                raise UncertainExecutionError(
                    "文件已经替换，但执行状态无法记录。"
                ) from error
            write_event(
                self.data_dir,
                "file.replace.done",
                action_id=action_id,
                target=str(target),
            )
        finally:
            try:
                Path(temporary).unlink()
            except FileNotFoundError:
                pass
        return backup_path

    def _run_write(self, proposal, state):
        recorded = state["grants"]["target"]
        _, guard = self._recheck_snapshot(recorded)
        relative_path = str(proposal.normalized_arguments["relative_path"])
        target = guard.resolve(relative_path)
        self._ensure_parent(target, guard.root)
        before = stamp(target, MAX_UNDO_BACKUP_BYTES)
        if before != state.get("target"):
            raise NotExecutedError("文件在确认后发生变化，写入没有执行。")
        content = str(proposal.normalized_arguments.get("content") or "")
        data = content.encode("utf-8")
        backup_path = self._replace(target, data, before, proposal.action_id)
        after = self._after_fingerprint(target, hashlib.sha256(data).hexdigest())
        return (
            {
                "target_path": str(target),
                "target_root": str(guard.root),
                "relative_path": relative_path,
                "backup_path": backup_path,
                "before": before,
                "after": after,
                "workspace": state.get("workspace"),
                "content_sha256": hashlib.sha256(data).hexdigest(),
                "content_preview": content[:300],
                "grants": state.get("grants") or {},
                "undo_reason": "",
            },
            "文件已写入：{}".format(target),
            True,
        )

    def _run_transfer(self, proposal, state):
        operation = str(state.get("operation") or FILE_OPERATION_MOVE)
        source_recorded = state["grants"]["source"]
        target_recorded = state["grants"]["target"]
        _, source_guard = self._recheck_snapshot(source_recorded)
        _, target_guard = self._recheck_snapshot(target_recorded)
        relative_source = str(state.get("source_rel_path") or "")
        relative_target = str(proposal.normalized_arguments["target_rel_path"])
        source = source_guard.resolve(relative_source)
        target = target_guard.resolve(relative_target)
        before_source = stamp(source, self._caps(operation)[0])
        if before_source != state.get("source"):
            raise NotExecutedError("源文件在确认后发生变化，动作没有执行。")
        before_target = stamp(target, self._caps(operation)[1])
        if before_target != state.get("target"):
            raise NotExecutedError("目标位置在确认后发生变化，动作没有执行。")
        if operation == FILE_OPERATION_COPY:
            self._ensure_parent(target, target_guard.root)
            data = source.read_bytes()
            if hashlib.sha256(data).hexdigest() != before_source["sha256"]:
                raise NotExecutedError("读取源文件时内容发生变化，复制没有执行。")
            backup_path = self._replace(
                target, data, before_target, proposal.action_id
            )
            after = self._after_fingerprint(target, hashlib.sha256(data).hexdigest())
            payload = {
                "target_path": str(target),
                "target_root": str(target_guard.root),
                "source_path": str(source),
                "source_root": str(source_guard.root),
                "relative_path": relative_target,
                "source_rel_path": relative_source,
                "backup_path": backup_path,
                "before": before_target,
                "source_fingerprint": before_source,
                "after": after,
                "workspace": state.get("workspace"),
                "content_sha256": hashlib.sha256(data).hexdigest(),
                "grants": state.get("grants") or {},
                "undo_reason": "",
            }
            return payload, "文件已复制到：{}".format(target), True
        live_source = volume_of(source)
        live_target = volume_of(
            _nearest_existing(target if before_target else target.parent)
        )
        recorded_devices = state.get("devices") or {}
        # 确认到执行之间目标可能被换到别的卷（可移动盘重插、目录被替换成链接），
        # 卷号一变，os.replace 就不再是"改一次目录项"。
        if live_source != recorded_devices.get("source") or live_target != (
            recorded_devices.get("target")
        ):
            raise NotExecutedError(
                "源文件与目标位置不再同一个磁盘卷，跨卷移动要先复制再删，"
                "本版本不做，动作没有执行。"
            )
        if before_target:
            raise NotExecutedError(
                "目标位置已有同名文件，移动没有执行；请先改名或先把它移进回收站。"
            )
        self._ensure_parent(target, target_guard.root)
        backup_path = ""
        self.store.mark_execution_state(proposal.action_id, "dispatching")
        try:
            os.replace(str(source), str(target))
        except OSError as error:
            if source.exists() and not target.exists():
                raise NotExecutedError(
                    "移动失败，文件仍在原处：{}".format(error)
                ) from error
            raise UncertainExecutionError(
                "文件位置不确定，请检查源路径与目标路径。"
            ) from error
        try:
            self.store.mark_execution_state(proposal.action_id, "replaced")
        except Exception as error:
            raise UncertainExecutionError(
                "文件已经移动，但执行状态无法记录。"
            ) from error
        if not target.is_file() or source.exists():
            raise UncertainExecutionError(
                "移动后状态与预期不一致，请检查源路径与目标路径。"
            )
        after = stamp(target, MAX_FILE_BYTES)
        if after != before_source:
            raise UncertainExecutionError(
                "移动后的文件与确认时的源文件对不上，请检查两个路径。"
            )
        payload = {
            "target_path": str(target),
            "target_root": str(target_guard.root),
            "source_path": str(source),
            "source_root": str(source_guard.root),
            "relative_path": relative_target,
            "source_rel_path": relative_source,
            "backup_path": backup_path,
            "before": None,
            "after": after,
            "workspace": state.get("workspace"),
            "content_sha256": after["sha256"] if after else "",
            "grants": state.get("grants") or {},
            "undo_reason": "",
        }
        return payload, "文件已移动到：{}".format(target), True

    def _run_delete(self, proposal, state):
        recorded = state["grants"]["source"]
        _, guard = self._recheck_snapshot(recorded)
        relative_source = str(state.get("source_rel_path") or "")
        source = guard.resolve(relative_source)
        before = stamp(source, MAX_FILE_BYTES)
        if before != state.get("source"):
            raise NotExecutedError("文件在确认后发生变化，删除没有执行。")
        undo_reason = str(state.get("undo_reason") or "")
        backup_path = ""
        if not undo_reason:
            data = source.read_bytes()
            if hashlib.sha256(data).hexdigest() != before["sha256"]:
                raise NotExecutedError("备份时文件内容发生变化，删除没有执行。")
            # 备份先落盘、账本后推进：中间任何一步失败，回执都能如实说"没动过文件"。
            backup_path = self._reserve(proposal.action_id, "before.bin", data)
        self.store.mark_execution_state(proposal.action_id, "dispatching")
        try:
            report = send_to_recycle_bin([str(source)])
        except RecycleBinError as error:
            if not source.exists():
                raise UncertainExecutionError(
                    "系统回收站报错（{}），但文件已不在原处，"
                    "无法确认它是否进了回收站。".format(error)
                ) from error
            raise NotExecutedError("删除没有执行：{}".format(error)) from error
        if source.exists():
            raise UncertainExecutionError(
                "系统回收站返回成功，但文件仍在原处，请手动确认。"
            )
        after = None
        payload = {
            "source_path": str(source),
            "source_root": str(guard.root),
            "source_rel_path": relative_source,
            "target_path": str(source),
            "backup_path": backup_path,
            "before": before,
            "after": after,
            "workspace": state.get("workspace"),
            "content_sha256": before["sha256"],
            "shell_code": report.get("shell_code", 0),
            "recycled": True,
            "grants": state.get("grants") or {},
            "undo_reason": undo_reason,
        }
        return payload, "文件已移入系统回收站：{}".format(source), not undo_reason

    @staticmethod
    def _caps(operation: str) -> Tuple[int, int]:
        """源与目标各自的大小拒绝线。

        复制要读正文，所以源受写入上限约束、目标受"能不能备份"约束；移动和删除只改
        目录项，不需要那条限制。哈希上限是另一条独立线，见 ``stamp``。
        """
        if operation == FILE_OPERATION_COPY:
            return MAX_FILE_WRITE_BYTES, MAX_UNDO_BACKUP_BYTES
        if operation in (FILE_OPERATION_MOVE, FILE_OPERATION_DELETE):
            return MAX_FILE_BYTES, MAX_FILE_BYTES
        return MAX_UNDO_BACKUP_BYTES, MAX_UNDO_BACKUP_BYTES

    def _after_fingerprint(self, target: Path, expected_sha256: str):
        try:
            after = stamp(target, MAX_UNDO_BACKUP_BYTES)
        except FileActionError as error:
            raise UncertainExecutionError(
                "文件已经落盘，但最终状态无法确认：{}".format(error)
            ) from error
        if not after or after.get("sha256") != expected_sha256:
            raise UncertainExecutionError(
                "文件已经落盘，但校验与预期不一致，请检查目标文件。"
            )
        return after

    # -- 撤销 ---------------------------------------------------------------

    def undo(self, receipt: ActionResult):
        if not receipt.undoable or receipt.undo_status != "available":
            reason = str(receipt.result.get("undo_reason") or "")
            raise NotExecutedError(
                reason or "这条回执不可撤销或已经撤销。"
            )
        if receipt.status != "completed":
            raise FileActionError("只有已完成的动作可以撤销。")
        operation = str(receipt.result.get("operation") or "")
        if operation == FILE_OPERATION_WRITE:
            return self._undo_replace(receipt, operation)
        if operation in (FILE_OPERATION_COPY, FILE_OPERATION_MOVE):
            return self._undo_transfer(receipt, operation)
        if operation == FILE_OPERATION_DELETE:
            return self._undo_delete(receipt)
        raise FileActionError("这条回执的动作类型无法撤销：{}".format(operation))

    def _require_same_grant(self, receipt, role):
        recorded = (receipt.result.get("grants") or {}).get(role)
        if not recorded:
            raise FileActionError("回执缺少目录授权快照，无法安全撤销。")
        snapshot = receipt.result.get("workspace") or {}
        if not resource_identity_valid(snapshot):
            raise FileActionError("工作目录身份已变化，无法安全撤销。")
        return self._recheck_snapshot(recorded)

    def _undo_replace(self, receipt, operation):
        _, guard = self._require_same_grant(receipt, "target")
        relative_path = str(receipt.result["relative_path"])
        target = guard.resolve(relative_path)
        if os.path.normcase(str(target)) != os.path.normcase(
            str(receipt.result.get("target_path") or "")
        ):
            raise FileActionError("撤销目标与回执不一致。")
        expected_after = receipt.result.get("after")
        if stamp(target, MAX_FILE_BYTES) != expected_after:
            raise FileActionError("文件已被其他程序修改，不能自动撤销。")
        before = receipt.result.get("before")
        if before:
            backup_path = receipt.result.get("backup_path")
            if not backup_path or not Path(backup_path).exists():
                raise FileActionError("找不到可用的写入前备份。")
            data = Path(backup_path).read_bytes()
            if hashlib.sha256(data).hexdigest() != before["sha256"]:
                raise FileActionError("备份校验失败，不能撤销。")
            self._restore(target, data)
            self.store.update_receipt_undo_status(receipt.receipt_id, "undone")
            return "已撤销写入：{}".format(target)
        target.unlink()
        self.store.update_receipt_undo_status(receipt.receipt_id, "undone")
        return "已删除这次新建的文件：{}".format(target)

    def _undo_transfer(self, receipt, operation):
        _, source_guard = self._require_same_grant(receipt, "source")
        _, target_guard = self._require_same_grant(receipt, "target")
        source = source_guard.resolve(str(receipt.result["source_rel_path"]))
        target = target_guard.resolve(str(receipt.result["relative_path"]))
        if os.path.normcase(str(target)) != os.path.normcase(
            str(receipt.result.get("target_path") or "")
        ):
            raise FileActionError("撤销目标与回执不一致。")
        expected_after = receipt.result.get("after")
        if stamp(target, MAX_FILE_BYTES) != expected_after:
            raise FileActionError("目标文件已被其他程序修改，不能自动撤销。")
        if operation == FILE_OPERATION_COPY:
            before = receipt.result.get("before")
            if before:
                backup_path = receipt.result.get("backup_path")
                if not backup_path or not Path(backup_path).exists():
                    raise FileActionError("找不到可用的覆盖前备份。")
                data = Path(backup_path).read_bytes()
                if hashlib.sha256(data).hexdigest() != before["sha256"]:
                    raise FileActionError("备份校验失败，不能撤销。")
                self._restore(target, data)
            else:
                target.unlink()
            self.store.update_receipt_undo_status(receipt.receipt_id, "undone")
            return "已撤销复制：{}".format(target)
        if source.exists():
            raise FileActionError("源路径已经有同名文件，不能自动把移动撤销回去。")
        os.replace(str(target), str(source))
        self.store.update_receipt_undo_status(receipt.receipt_id, "undone")
        return "已撤销移动：{} → {}".format(target, source)

    def _undo_delete(self, receipt):
        _, guard = self._require_same_grant(receipt, "source")
        source = guard.resolve(str(receipt.result["source_rel_path"]))
        if source.exists():
            raise FileActionError("文件已经回到原位置，不需要再撤销。")
        backup_path = receipt.result.get("backup_path")
        before = receipt.result.get("before")
        if not backup_path or not before:
            raise FileActionError(
                str(receipt.result.get("undo_reason") or "")
                or "这条回执没有可还原的备份。"
            )
        data = Path(backup_path).read_bytes()
        if hashlib.sha256(data).hexdigest() != before["sha256"]:
            raise FileActionError("备份校验失败，不能撤销。")
        self._ensure_parent(source, guard.root)
        self._restore(source, data)
        self.store.update_receipt_undo_status(receipt.receipt_id, "undone")
        return "已从备份还原：{}".format(source)

    def _restore(self, target: Path, data: bytes) -> None:
        descriptor, temporary = _new_temp_file(target.parent, ".companion-undo-")
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
