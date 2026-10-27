"""Collect traceable proactive sources and apply the gating chain."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from typing import List, Optional

from ..models import AgentMode
from .activity import ACTIVITY_ACTIVE
from .gate import GateDecision, GateState, evaluate_gate
from .reminders import ReminderService, format_due
from .repeat import content_hash, is_duplicate
from .topics import derive_unfinished_topics


SOURCE_REMINDER = "reminder"
SOURCE_UNFINISHED_TOPIC = "unfinished_topic"
SOURCE_MEMORY = "memory"
MEMORY_SOURCE_MAX_AGE_HOURS = 48


@dataclass(frozen=True)
class ProactiveSource:
    source_type: str
    source_id: str
    content: str
    trigger_reason: str
    created_at: str = ""
    source_message_id: Optional[int] = None


@dataclass
class ProactivePreview:
    speak: bool
    reason: str
    gate: GateDecision
    sources: List[ProactiveSource] = field(default_factory=list)
    source: Optional[ProactiveSource] = None


def _parse_time(value):
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment


class ProactiveEngine:
    """Preview what the companion would say, without generating or sending it."""

    def __init__(self, store, reminders=None, topic_limit=3):
        self.store = store
        self.reminders = reminders or ReminderService(store)
        self.topic_limit = max(1, int(topic_limit))

    def collect_sources(
        self,
        session_id: str,
        now=None,
        include_memory: bool = False,
        apply_dedupe: bool = True,
    ):
        now = now or datetime.now(timezone.utc)
        sources = []
        for reminder in self.reminders.due(session_id, now=now, limit=5):
            sources.append(
                ProactiveSource(
                    source_type=SOURCE_REMINDER,
                    source_id=reminder.reminder_id,
                    content="提醒：{}".format(reminder.content),
                    trigger_reason="提醒到点：{}".format(
                        format_due(reminder.due_at, now=now)
                    ),
                    created_at=reminder.created_at,
                )
            )
        history = self.store.list_messages(session_id, limit=40)
        for topic in derive_unfinished_topics(
            history,
            now=now,
            limit=self.topic_limit,
        ):
            reason = (
                "上次提问还没有回复"
                if topic.reason == "unanswered"
                else "上次回复失败，问题还没有收尾"
            )
            sources.append(
                ProactiveSource(
                    source_type=SOURCE_UNFINISHED_TOPIC,
                    source_id="msg_{}".format(topic.source_message_id),
                    content=topic.content,
                    trigger_reason=reason,
                    created_at=topic.created_at,
                    source_message_id=topic.source_message_id,
                )
            )
        if include_memory:
            sources.extend(self._memory_sources(session_id, now))
        if apply_dedupe:
            sources = self.filter_duplicates(sources, session_id, now)
        return sources

    def _memory_sources(self, session_id: str, now):
        cutoff = timedelta(hours=MEMORY_SOURCE_MAX_AGE_HOURS)
        seen = set()
        sources = []
        for log in self.store.list_recall_logs(session_id, limit=40):
            if not log.get("used_in_reply"):
                continue
            created = _parse_time(log.get("created_at"))
            if created is None or now - created > cutoff:
                continue
            try:
                fact_ids = json.loads(log.get("fact_ids") or "[]")
            except ValueError:
                continue
            for memory_id in fact_ids:
                if memory_id in seen:
                    continue
                seen.add(memory_id)
                memory = self.store.get_memory(memory_id)
                if memory is None or memory.status != "confirmed":
                    continue
                content = " ".join(str(memory.content or "").split())
                if not content:
                    continue
                sources.append(
                    ProactiveSource(
                        source_type=SOURCE_MEMORY,
                        source_id=memory.memory_id,
                        content=content,
                        trigger_reason="最近对话里用到了这条记忆",
                        created_at=memory.updated_at,
                        source_message_id=memory.source_message_id,
                    )
                )
                return sources
        return sources

    def filter_duplicates(self, sources, session_id: str, now):
        events = self.store.list_proactive_events(session_id, limit=100)
        kept = []
        seen = set()
        for source in sources:
            digest = content_hash(source.content)
            if digest in seen:
                continue
            if is_duplicate(source, events, now=now):
                continue
            seen.add(digest)
            kept.append(source)
        return kept

    def preview(
        self,
        session_id: str,
        now=None,
        activity: str = ACTIVITY_ACTIVE,
        gate_state: Optional[GateState] = None,
        include_memory: bool = False,
    ) -> ProactivePreview:
        now = now or datetime.now(timezone.utc)
        raw_sources = self.collect_sources(
            session_id,
            now=now,
            include_memory=include_memory,
            apply_dedupe=False,
        )
        sources = self.filter_duplicates(raw_sources, session_id, now)
        if not sources:
            reason = "duplicate" if raw_sources else "no_source"
            return ProactivePreview(
                speak=False,
                reason=reason,
                gate=GateDecision(False, reason),
            )
        state = gate_state or GateState(
            mode=self.store.get_session_mode(session_id) or AgentMode.CHAT,
            now=now,
            activity=activity,
        )
        decision = evaluate_gate(state)
        return ProactivePreview(
            speak=decision.allowed,
            reason=decision.reason,
            gate=decision,
            sources=sources,
            source=sources[0] if decision.allowed else None,
        )
