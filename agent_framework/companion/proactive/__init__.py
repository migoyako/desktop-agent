"""Proactive companionship substrate: reminders, topics, activity and gating."""

from .activity import (
    ACTIVITY_ACTIVE,
    ACTIVITY_AWAY,
    ACTIVITY_FOCUS,
    ACTIVITY_GAME,
    ACTIVITY_IDLE,
    ACTIVITY_PRIVATE,
    ACTIVITY_UNKNOWN,
    ActivitySnapshot,
    WindowsActivityProbe,
    classify_activity,
    describe_activity,
)
from .engine import ProactiveEngine, ProactivePreview, ProactiveSource
from .gate import GateDecision, GateState, evaluate_gate
from .compose import compose_message
from .reminders import (
    ReminderDraft,
    ReminderService,
    format_due,
    parse_reminder_request,
    resolve_when,
)
from .repeat import (
    DEDUPE_HALF_LIFE_HOURS,
    content_hash,
    duplicate_score,
    is_duplicate,
    url_hash,
)
from .service import (
    MISSED_GRACE_MINUTES,
    PROACTIVE_REASON_LABELS,
    PROACTIVE_STATUS_LABELS,
    ProactiveService,
    reason_label,
    status_label,
)
from .topics import UnfinishedTopic, derive_unfinished_topics

__all__ = [
    "ACTIVITY_ACTIVE",
    "ACTIVITY_AWAY",
    "ACTIVITY_FOCUS",
    "ACTIVITY_GAME",
    "ACTIVITY_IDLE",
    "ACTIVITY_PRIVATE",
    "ACTIVITY_UNKNOWN",
    "ActivitySnapshot",
    "DEDUPE_HALF_LIFE_HOURS",
    "GateDecision",
    "GateState",
    "MISSED_GRACE_MINUTES",
    "PROACTIVE_REASON_LABELS",
    "PROACTIVE_STATUS_LABELS",
    "ProactiveEngine",
    "ProactivePreview",
    "ProactiveService",
    "ProactiveSource",
    "ReminderDraft",
    "ReminderService",
    "UnfinishedTopic",
    "WindowsActivityProbe",
    "classify_activity",
    "compose_message",
    "content_hash",
    "derive_unfinished_topics",
    "describe_activity",
    "duplicate_score",
    "evaluate_gate",
    "format_due",
    "is_duplicate",
    "parse_reminder_request",
    "reason_label",
    "resolve_when",
    "status_label",
    "url_hash",
]
