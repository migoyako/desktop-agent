"""Pure gating rules for proactive messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple

from ..models import AgentMode
from .activity import (
    ACTIVITY_ACTIVE,
    ACTIVITY_PRIVATE,
    ACTIVITY_UNKNOWN,
)


DEFAULT_QUIET_HOURS = (22, 8)
DEFAULT_DAILY_LIMIT = 2
DEFAULT_MIN_INTERVAL_HOURS = 3.0
DEFAULT_MAX_UNANSWERED = 2


@dataclass(frozen=True)
class GateState:
    mode: AgentMode
    now: datetime
    daily_count: int = 0
    last_spoken_at: Optional[datetime] = None
    unanswered: int = 0
    activity: str = ACTIVITY_ACTIVE
    busy: bool = False
    quiet_hours: Optional[Tuple[int, int]] = DEFAULT_QUIET_HOURS
    daily_limit: int = DEFAULT_DAILY_LIMIT
    min_interval_hours: float = DEFAULT_MIN_INTERVAL_HOURS
    max_unanswered: int = DEFAULT_MAX_UNANSWERED


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str


def in_quiet_hours(hour: int, quiet_hours) -> bool:
    if not quiet_hours:
        return False
    start, end = int(quiet_hours[0]), int(quiet_hours[1])
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _aware(moment: Optional[datetime]) -> Optional[datetime]:
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.astimezone()
    return moment


def evaluate_gate(state: GateState) -> GateDecision:
    if AgentMode(state.mode) == AgentMode.QUIET:
        return GateDecision(False, "quiet_mode")
    if state.busy:
        return GateDecision(False, "busy")
    now = _aware(state.now)
    if in_quiet_hours(now.hour, state.quiet_hours):
        return GateDecision(False, "quiet_hours")
    if state.daily_count >= max(0, int(state.daily_limit)):
        return GateDecision(False, "daily_limit")
    last_spoken = _aware(state.last_spoken_at)
    if last_spoken is not None:
        elapsed = now - last_spoken
        if elapsed < timedelta(hours=float(state.min_interval_hours)):
            return GateDecision(False, "min_interval")
    if state.unanswered >= max(0, int(state.max_unanswered)):
        return GateDecision(False, "unanswered")
    if state.activity == ACTIVITY_PRIVATE:
        return GateDecision(False, "privacy")
    if state.activity == ACTIVITY_UNKNOWN:
        return GateDecision(False, "activity_unknown")
    if state.activity != ACTIVITY_ACTIVE:
        return GateDecision(False, "activity_{}".format(state.activity))
    return GateDecision(True, "ok")
