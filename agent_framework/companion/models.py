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
    SHELL = "shell"
    NETWORK = "network"
    INPUT_CONTROL = "input-control"


class ProposalStatus(str, Enum):
    DRAFTED = "drafted"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


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


@dataclass
class ExperienceRecord:
    experience_id: str
    session_id: str
    title: str
    summary: str
    source_message_id: Optional[int]
    receipt_id: Optional[str]
    created_at: str


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
