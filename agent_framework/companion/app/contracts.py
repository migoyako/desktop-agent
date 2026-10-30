"""Stable command, run, event, and approval contracts for application adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import threading
from typing import Any, Dict, List, Optional, Tuple

from ..file_actions import workspace_identity
from ..models import ActionProposal, AgentMode, new_id, utc_now


class ApplicationError(RuntimeError):
    """Base error raised by application-layer commands."""


class InvalidTurnError(ApplicationError):
    pass


class RunAlreadyActiveError(ApplicationError):
    pass


class RunNotFoundError(ApplicationError):
    pass


class SessionBusyError(ApplicationError):
    pass


class ApprovalUnavailableError(ApplicationError):
    pass


class MessageUnavailableError(ApplicationError):
    pass


class TurnPersistenceError(ApplicationError):
    pass


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class RunStage(str, Enum):
    PENDING = "pending"
    THINKING = "thinking"
    TOOL = "tool"
    ANSWERING = "answering"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class EventKind(str, Enum):
    STARTED = "started"
    DELTA = "delta"
    RESET = "reset"
    TOOL = "tool"
    ERROR = "error"
    DONE = "done"


@dataclass(frozen=True)
class WorkspaceSnapshot:
    root: str
    device: str
    file_id: str

    @classmethod
    def capture(cls, root) -> Optional["WorkspaceSnapshot"]:
        if not root:
            return None
        value = workspace_identity(root)
        return cls(
            root=str(value["root"]),
            device=str(value["device"]),
            file_id=str(value["file_id"]),
        )

    def as_dict(self) -> Dict[str, str]:
        return {
            "root": self.root,
            "device": self.device,
            "file_id": self.file_id,
        }


@dataclass(frozen=True)
class StartTurnCommand:
    text: str
    mode: Optional[AgentMode] = None


@dataclass(frozen=True)
class RegenerateMessageCommand:
    message_id: int


@dataclass
class RunContext:
    run_id: str
    session_id: str
    mode: AgentMode
    text: str
    previous_message_id: int
    workspace: Optional[WorkspaceSnapshot] = None
    regenerate_of: int = 0
    source_message_id: int = 0
    started_at: str = field(default_factory=utc_now)
    status: RunStatus = RunStatus.PENDING
    error: str = ""
    result: Optional[Dict[str, Any]] = None
    sequence: int = 0
    stage: str = RunStage.PENDING.value
    detail: str = ""
    tool: str = ""
    steps: List["RunStep"] = field(default_factory=list, repr=False, compare=False)
    cancel_event: threading.Event = field(
        default_factory=threading.Event,
        repr=False,
        compare=False,
    )
    finished_event: threading.Event = field(
        default_factory=threading.Event,
        repr=False,
        compare=False,
    )
    _finish_lock: threading.Lock = field(
        default_factory=threading.Lock,
        repr=False,
        compare=False,
    )

    @property
    def finished(self) -> bool:
        return self.finished_event.is_set()

    def request_cancel(self) -> bool:
        if self.finished:
            return False
        self.cancel_event.set()
        return True

    def complete(
        self,
        status: RunStatus,
        result: Optional[Dict[str, Any]] = None,
        error: str = "",
    ):
        with self._finish_lock:
            if self.finished:
                return
            self.status = RunStatus(status)
            self.result = dict(result or {})
            self.error = str(error or "")
            self.finished_event.set()

    def wait(self, timeout: Optional[float] = None) -> RunStatus:
        self.finished_event.wait(timeout)
        return self.status

    def as_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "mode": self.mode.value,
            "previous_message_id": self.previous_message_id,
            "workspace": self.workspace.as_dict() if self.workspace else None,
            "started_at": self.started_at,
            "status": self.status.value,
            "error": self.error,
            "sequence": self.sequence,
            "stage": self.stage,
            "detail": self.detail,
            "tool": self.tool,
        }


@dataclass(frozen=True)
class ApplicationEvent:
    kind: str
    run_id: str
    session_id: str
    sequence: int
    payload: Dict[str, Any] = field(default_factory=dict)
    stage: str = ""

    def as_dict(self) -> Dict[str, Any]:
        value = dict(self.payload)
        value.update(
            {
                "kind": self.kind,
                "run_id": self.run_id,
                "generation_id": self.run_id,
                "session_id": self.session_id,
                "sequence": self.sequence,
                "stage": self.stage,
            }
        )
        return value


@dataclass(frozen=True)
class ApprovalPreview:
    """Read-only description of the change a confirmation would make."""

    change: str = "unavailable"
    workspace: str = ""
    relative_path: str = ""
    diff: Tuple[Tuple[str, str], ...] = ()
    added: int = 0
    removed: int = 0
    truncated: bool = False
    omitted_lines: int = 0
    error: str = ""

    @classmethod
    def from_value(cls, value) -> "ApprovalPreview":
        value = value if isinstance(value, dict) else {}
        rows = []
        for item in value.get("diff") or ():
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                rows.append((str(item[0]), str(item[1])))
        return cls(
            change=str(value.get("change") or "unavailable"),
            workspace=str(value.get("workspace") or ""),
            relative_path=str(value.get("relative_path") or ""),
            diff=tuple(rows),
            added=int(value.get("added") or 0),
            removed=int(value.get("removed") or 0),
            truncated=bool(value.get("truncated")),
            omitted_lines=int(value.get("omitted_lines") or 0),
            error=str(value.get("error") or ""),
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "change": self.change,
            "workspace": self.workspace,
            "relative_path": self.relative_path,
            "diff": [list(row) for row in self.diff],
            "added": self.added,
            "removed": self.removed,
            "truncated": self.truncated,
            "omitted_lines": self.omitted_lines,
            "error": self.error,
        }


@dataclass(frozen=True)
class ApprovalView:
    action_id: str
    session_id: str
    mode: str
    status: str
    risk: str
    target_summary: str
    plan: str
    confirmation_digest: str
    expires_at: str
    reversible: bool
    created_at: str
    tool: str = ""
    workspace: str = ""
    expired: bool = False
    preview: Optional[ApprovalPreview] = None

    @classmethod
    def from_proposal(
        cls,
        proposal: ActionProposal,
        preview=None,
        expired=None,
        workspace=None,
    ) -> "ApprovalView":
        snapshot = (proposal.expected_state or {}).get("workspace") or {}
        return cls(
            action_id=proposal.action_id,
            session_id=proposal.session_id,
            mode=AgentMode(proposal.mode).value,
            status=proposal.status.value,
            risk=proposal.risk.value,
            target_summary=proposal.target_summary,
            plan=proposal.user_visible_plan,
            confirmation_digest=proposal.confirmation_digest,
            expires_at=str(
                (proposal.expected_state or {}).get("expires_at") or ""
            ),
            reversible=bool(proposal.reversible),
            created_at=proposal.created_at,
            tool=str(proposal.tool_name or ""),
            workspace=str(
                workspace if workspace is not None else snapshot.get("root") or ""
            ),
            expired=bool(expired),
            preview=ApprovalPreview.from_value(preview),
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "session_id": self.session_id,
            "mode": self.mode,
            "status": self.status,
            "risk": self.risk,
            "target_summary": self.target_summary,
            "plan": self.plan,
            "confirmation_digest": self.confirmation_digest,
            "expires_at": self.expires_at,
            "reversible": self.reversible,
            "created_at": self.created_at,
            "tool": self.tool,
            "workspace": self.workspace,
            "expired": self.expired,
            "preview": self.preview.as_dict() if self.preview else None,
        }


@dataclass(frozen=True)
class TranscriptMessage:
    message_id: int
    role: str
    text: str
    created_at: str
    mode: str = ""


@dataclass(frozen=True)
class RunStep:
    """One collapsed stage transition of a run; repeated deltas never add rows."""

    sequence: int
    stage: str
    at: str
    detail: str = ""
    tool: str = ""


@dataclass(frozen=True)
class RunView:
    run_id: str
    session_id: str
    mode: str
    status: str
    stage: str
    sequence: int
    started_at: str
    detail: str = ""
    tool: str = ""
    steps: Tuple[RunStep, ...] = ()


@dataclass(frozen=True)
class MessageActions:
    """Which transcript rows may offer the message-level commands."""

    regenerate_id: int = 0
    source_id: int = 0


@dataclass(frozen=True)
class ProjectionSection:
    title: str
    lines: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextView:
    """Context window usage as numbers; adapters must not parse display text for them."""

    context_used: int
    context_limit: int
    compressed: bool = False

    @property
    def percent(self) -> Optional[int]:
        if self.context_limit <= 0:
            return None
        return min(100, int(round(100.0 * self.context_used / self.context_limit)))


@dataclass(frozen=True)
class UiProjection:
    """Read-only state a chat adapter renders; no runtime objects leak through."""

    session_id: str
    messages: Tuple[TranscriptMessage, ...] = ()
    run: Optional[RunView] = None
    approvals: Tuple[ApprovalView, ...] = ()
    inspector: Tuple[ProjectionSection, ...] = ()
    context: Optional[ContextView] = None
    message_actions: MessageActions = field(default_factory=MessageActions)


def start_command(text, mode=None) -> StartTurnCommand:
    return StartTurnCommand(text=str(text or ""), mode=mode and AgentMode(mode))


def new_run_id() -> str:
    return new_id("run")
