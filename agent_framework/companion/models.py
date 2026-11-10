"""Types shared by the desktop companion runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Dict, Optional
import uuid


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return "{}_{}".format(prefix, uuid.uuid4().hex[:16])


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def confirmation_digest(
    tool_name: str,
    arguments: Dict[str, Any],
    risk: str,
    target_summary: str,
) -> str:
    payload = {
        "tool_name": tool_name,
        "arguments": arguments,
        "risk": risk,
        "target_summary": target_summary,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class AgentMode(str, Enum):
    CHAT = "chat"
    WORK = "work"
    EXECUTE = "execute"
    QUIET = "quiet"


class RiskLevel(str, Enum):
    SAFE = "safe"
    FS_READ = "fs-read"
    FS_WRITE = "fs-write"
    FS_DELETE = "fs-delete"
    SHELL = "shell"
    NETWORK = "network"
    INPUT_CONTROL = "input-control"
    EXTERNAL = "external"
    APP_LAUNCH = "app-launch"
    LAUNCH_CHAIN = "launch-chain"


class ProposalStatus(str, Enum):
    DRAFTED = "drafted"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    NOT_EXECUTED = "not_executed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


# Which code runs a confirmed proposal. "note" is the original filesystem path:
# workspace snapshot, backup, undo. "mcp" only sends tools/call, so its receipt is
# never undoable and it must not touch the workspace guard at all. "file" is the
# general authorised-directory executor and narrows the note contract per operation
# (write / copy / move / recycle-bin delete) instead of reusing the note whitelist.
# "app" launches a registered application only; the command line comes from the
# catalog row, never from the model, so it cannot be used to run arbitrary code.
EXECUTOR_NOTE = "note"
EXECUTOR_FILE = "file"
EXECUTOR_MCP = "mcp"
EXECUTOR_APP = "app"
EXECUTOR_CHAIN = "chain"
PROPOSAL_EXECUTORS = (
    EXECUTOR_NOTE,
    EXECUTOR_FILE,
    EXECUTOR_MCP,
    EXECUTOR_APP,
    EXECUTOR_CHAIN,
)


@dataclass
class ActionProposal:
    action_id: str
    session_id: str
    mode: AgentMode
    intent: str
    tool_name: str
    raw_arguments: Dict[str, Any]
    normalized_arguments: Dict[str, Any]
    expected_state: Dict[str, Any]
    risk: RiskLevel
    target_summary: str
    reversible: bool
    user_visible_plan: str
    confirmation_digest: str
    idempotency_key: str
    status: ProposalStatus = ProposalStatus.AWAITING_CONFIRMATION
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    executor: str = EXECUTOR_NOTE


@dataclass
class ActionResult:
    receipt_id: str
    action_id: str
    session_id: str
    status: str
    summary: str
    result: Dict[str, Any]
    started_at: str
    finished_at: str
    error: str = ""
    undoable: bool = False
    undo_status: str = "available"
    created_at: str = field(default_factory=utc_now)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""


@dataclass
class MemoryRecord:
    memory_id: str
    session_id: str
    kind: str
    content: str
    source_message_id: Optional[int]
    confidence: float
    importance: float
    activation: float
    status: str
    created_at: str
    updated_at: str
    canonical_key: str = ""
    evidence_count: int = 0
    protected: bool = False
    forget_epoch: int = 0
    first_seen_at: str = ""
    last_seen_at: str = ""


@dataclass
class ExperienceRecord:
    experience_id: str
    session_id: str
    title: str
    summary: str
    source_message_id: Optional[int]
    receipt_id: Optional[str]
    created_at: str
    memory_id: str = ""
    status: str = "active"


@dataclass
class AffectRecord:
    affect_id: str
    session_id: str
    target: str
    mood_label: str
    valence: float
    arousal: float
    energy: Optional[float]
    confidence: float
    source_message_id: Optional[int]
    source: str
    created_at: str


@dataclass
class ReminderRecord:
    reminder_id: str
    session_id: str
    content: str
    due_at: str
    status: str
    source_message_id: Optional[int]
    created_at: str
    updated_at: str
    acknowledged_at: Optional[str] = None


REMINDER_PENDING = "pending"
REMINDER_DONE = "done"
REMINDER_DISMISSED = "dismissed"


@dataclass
class ProactiveState:
    session_id: str
    enabled: bool = True
    quiet_start: int = 22
    quiet_end: int = 8
    daily_limit: int = 2
    min_interval_hours: float = 3.0
    max_unanswered: int = 2
    include_memory: bool = False
    daily_count: int = 0
    daily_date: str = ""
    last_spoken_at: Optional[str] = None
    last_event_id: str = ""
    unanswered_count: int = 0
    updated_at: str = ""


@dataclass
class ProactiveEvent:
    event_id: str
    session_id: str
    source_type: str
    source_id: str
    source_message_id: Optional[int]
    trigger_reason: str
    content: str
    status: str
    reason: str
    message_id: Optional[int]
    content_hash: str
    url_hash: str
    read_at: Optional[str]
    created_at: str


PROACTIVE_DELIVERED = "delivered"
PROACTIVE_MISSED = "missed"


WORK_NORMAL = "NORMAL"
WORK_PLAN_DISCUSSING = "PLAN_DISCUSSING"
WORK_PLAN_REVIEW = "PLAN_REVIEW"
WORK_EXECUTING = "EXECUTING"
WORK_STATES = (
    WORK_NORMAL,
    WORK_PLAN_DISCUSSING,
    WORK_PLAN_REVIEW,
    WORK_EXECUTING,
)

APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_REJECTED = "rejected"
APPROVAL_EXPIRED = "expired"

FORWARD_AWAITING_CONFIRMATION = "awaiting_confirmation"
FORWARD_FORWARDING = "forwarding"
FORWARD_ACCEPTED = "accepted"
FORWARD_COMPLETED = "completed"
FORWARD_UNKNOWN = "unknown"
FORWARD_UNAVAILABLE = "unavailable"
FORWARD_STATUSES = (
    FORWARD_AWAITING_CONFIRMATION,
    FORWARD_FORWARDING,
    FORWARD_ACCEPTED,
    FORWARD_COMPLETED,
    FORWARD_UNKNOWN,
    FORWARD_UNAVAILABLE,
)


@dataclass
class TaskCard:
    title: str
    reason: str
    spoken_summary: str
    goal: str
    executor: str
    project: str


@dataclass
class WorkTask:
    task_id: str
    session_id: str
    state: str
    card: TaskCard
    source_message_id: Optional[int] = None
    info_asked: int = 0
    revision: int = 0
    approval_status: str = APPROVAL_PENDING
    approval_reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    expires_at: str = ""
    approved_at: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


@dataclass
class WorkReceipt:
    receipt_id: str
    task_id: str
    session_id: str
    status: str
    summary: str
    detail: str = ""
    external_id: str = ""
    attempts: int = 0
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
