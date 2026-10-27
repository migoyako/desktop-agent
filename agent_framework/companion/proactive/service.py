"""Scheduling, persistence and anti-repeat for proactive messages."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from ..models import AgentMode, PROACTIVE_DELIVERED, PROACTIVE_MISSED
from .activity import (
    ACTIVITY_LABELS,
    ACTIVITY_UNKNOWN,
    WindowsActivityProbe,
    classify_activity,
)
from .compose import compose_message
from .engine import (
    SOURCE_REMINDER,
    ProactiveEngine,
    ProactiveSource,
)
from .gate import GateState
from .reminders import ReminderService, format_due
from .repeat import source_hashes


MISSED_GRACE_MINUTES = 30
MAX_EVENTS = 50

PROACTIVE_REASON_LABELS = {
    "ok": "允许发言",
    "no_source": "没有可追溯的来源",
    "duplicate": "近期已经说过类似内容",
    "disabled": "主动已暂停",
    "quiet_mode": "安静模式",
    "busy": "正在对话或生成",
    "quiet_hours": "免打扰时段",
    "daily_limit": "已达到每日上限",
    "min_interval": "距上次主动发言太近",
    "unanswered": "连续多次未回复",
    "privacy": "当前是隐私界面",
    "activity_unknown": "无法确认当前活动状态",
    "activity_idle": "你暂时没有操作",
    "activity_away": "你已离开",
    "activity_focus": "你正在专注或全屏工作",
    "activity_game": "你正在游戏",
    "activity_private": "当前是隐私界面",
    "missed_window": "错过了送达窗口",
}

PROACTIVE_STATUS_LABELS = {
    PROACTIVE_DELIVERED: "已送达",
    PROACTIVE_MISSED: "错过未读",
}


def reason_label(reason: str) -> str:
    return PROACTIVE_REASON_LABELS.get(str(reason), str(reason))


def status_label(status: str) -> str:
    return PROACTIVE_STATUS_LABELS.get(str(status), str(status))


def _parse_time(value):
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment


def _local_date(now) -> str:
    return now.astimezone().date().isoformat()


class ProactiveService:
    def __init__(
        self,
        store,
        engine: ProactiveEngine,
        reminders: ReminderService,
        activity_probe=None,
        session_id: str = "main",
        missed_grace_minutes: int = MISSED_GRACE_MINUTES,
    ):
        self.store = store
        self.engine = engine
        self.reminders = reminders
        self.activity_probe = activity_probe or WindowsActivityProbe()
        self.session_id = session_id
        self.missed_grace = timedelta(
            minutes=max(1, int(missed_grace_minutes))
        )

    def state(self):
        return self.store.get_proactive_state(self.session_id)

    def activity(self):
        snapshot = self.activity_probe.snapshot()
        return classify_activity(snapshot), snapshot

    def status(self, now=None) -> dict:
        now = now or datetime.now().astimezone()
        state = self.state()
        activity, snapshot = self.activity()
        next_due = self.reminders.next_due(self.session_id)
        last_event = self.store.latest_proactive_event(self.session_id)
        today = _local_date(now)
        return {
            "enabled": state.enabled,
            "activity": activity,
            "activity_label": ACTIVITY_LABELS.get(
                activity,
                ACTIVITY_LABELS[ACTIVITY_UNKNOWN],
            ),
            "idle_seconds": round(float(snapshot.idle_seconds or 0.0), 1),
            "daily_count": (
                state.daily_count if state.daily_date == today else 0
            ),
            "daily_limit": state.daily_limit,
            "unanswered": state.unanswered_count,
            "max_unanswered": state.max_unanswered,
            "unread": self.store.count_unread_proactive_events(
                self.session_id
            ),
            "last_spoken_at": state.last_spoken_at,
            "last_event_id": state.last_event_id,
            "next_due": (
                None
                if next_due is None
                else {
                    "reminder_id": next_due.reminder_id,
                    "content": next_due.content,
                    "due_at": next_due.due_at,
                    "display": format_due(next_due.due_at, now=now),
                }
            ),
            "last_event": (
                None
                if last_event is None
                else {
                    "event_id": last_event.event_id,
                    "source_type": last_event.source_type,
                    "trigger_reason": last_event.trigger_reason,
                    "status": last_event.status,
                    "status_label": status_label(last_event.status),
                    "created_at": last_event.created_at,
                }
            ),
            "settings": {
                "quiet_start": state.quiet_start,
                "quiet_end": state.quiet_end,
                "daily_limit": state.daily_limit,
                "min_interval_hours": state.min_interval_hours,
                "max_unanswered": state.max_unanswered,
                "include_memory": state.include_memory,
            },
        }

    def update_settings(self, **fields):
        allowed = {
            "enabled",
            "quiet_start",
            "quiet_end",
            "daily_limit",
            "min_interval_hours",
            "max_unanswered",
            "include_memory",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(
                "未知的主动设置：{}".format("、".join(sorted(unknown)))
            )
        if "quiet_start" in fields or "quiet_end" in fields:
            state = self.state()
            start = int(fields.get("quiet_start", state.quiet_start))
            end = int(fields.get("quiet_end", state.quiet_end))
            if not (0 <= start <= 23 and 0 <= end <= 23):
                raise ValueError("免打扰时段必须在 0 到 23 点之间。")
        if "daily_limit" in fields and not 0 <= int(fields["daily_limit"]) <= 20:
            raise ValueError("每日上限必须在 0 到 20 之间。")
        if "min_interval_hours" in fields:
            hours = float(fields["min_interval_hours"])
            if not 0 <= hours <= 168:
                raise ValueError("最小间隔必须在 0 到 168 小时之间。")
        if "max_unanswered" in fields and not 0 <= int(
            fields["max_unanswered"]
        ) <= 20:
            raise ValueError("未回复阈值必须在 0 到 20 之间。")
        return self.store.update_proactive_state(self.session_id, **fields)

    def events(self, limit: int = 20):
        return self.store.list_proactive_events(
            self.session_id,
            limit=max(1, min(int(limit), MAX_EVENTS)),
        )

    def unread_events(self, limit: int = 20):
        events = self.events(limit=MAX_EVENTS)
        return [event for event in events if event.read_at is None][:limit]

    def mark_read(self, event_id: Optional[str] = None) -> int:
        return self.store.mark_proactive_read(self.session_id, event_id)

    def on_user_message(self) -> int:
        self.store.update_proactive_state(
            self.session_id,
            unanswered_count=0,
        )
        return self.mark_read()

    def _gate_state(self, state, now, activity) -> GateState:
        today = _local_date(now)
        quiet_hours = (
            None
            if int(state.quiet_start) == int(state.quiet_end)
            else (state.quiet_start, state.quiet_end)
        )
        return GateState(
            mode=self.store.get_session_mode(self.session_id)
            or AgentMode.CHAT,
            now=now,
            daily_count=(
                state.daily_count if state.daily_date == today else 0
            ),
            last_spoken_at=_parse_time(state.last_spoken_at),
            unanswered=state.unanswered_count,
            activity=activity,
            quiet_hours=quiet_hours,
            daily_limit=state.daily_limit,
            min_interval_hours=state.min_interval_hours,
            max_unanswered=state.max_unanswered,
        )

    def preview(self, gate_state=None, now=None) -> dict:
        now = now or datetime.now().astimezone()
        state = self.state()
        activity, _snapshot = self.activity()
        if not state.enabled:
            return {
                "speak": False,
                "reason": "disabled",
                "reason_label": reason_label("disabled"),
                "activity": activity,
                "enabled": False,
                "source": None,
                "sources": [],
            }
        gate = gate_state or self._gate_state(state, now, activity)
        preview = self.engine.preview(
            self.session_id,
            now=now,
            activity=activity,
            gate_state=gate,
            include_memory=state.include_memory,
        )
        return self._preview_dict(preview, activity, state.enabled)

    def tick(self, now=None) -> dict:
        now = now or datetime.now().astimezone()
        state = self.store.ensure_proactive_state(self.session_id)
        if not state.enabled:
            return {
                "spoke": False,
                "reason": "disabled",
                "reason_label": reason_label("disabled"),
                "missed": [],
            }
        missed = self._record_missed(now)
        state = self.state()
        activity, _snapshot = self.activity()
        gate = self._gate_state(state, now, activity)
        preview = self.engine.preview(
            self.session_id,
            now=now,
            activity=activity,
            gate_state=gate,
            include_memory=state.include_memory,
        )
        if not preview.speak:
            result = self._preview_dict(preview, activity, state.enabled)
            result["spoke"] = False
            result["missed"] = [self._event_dict(item) for item in missed]
            return result

        source = preview.source
        content = compose_message(source)
        mode = self.store.get_session_mode(self.session_id) or AgentMode.CHAT
        message_id = self.store.add_message(
            self.session_id,
            "assistant",
            content,
            mode,
        )
        content_digest, url_digest = source_hashes(source)
        event = self.store.add_proactive_event(
            session_id=self.session_id,
            source_type=source.source_type,
            source_id=source.source_id,
            source_message_id=source.source_message_id,
            trigger_reason=source.trigger_reason,
            content=content,
            status=PROACTIVE_DELIVERED,
            reason="ok",
            message_id=message_id,
            content_hash=content_digest,
            url_hash=url_digest,
            created_at=now.astimezone(timezone.utc).isoformat(),
        )
        if source.source_type == SOURCE_REMINDER:
            self.reminders.complete(source.source_id)
        today = _local_date(now)
        daily_count = (
            state.daily_count if state.daily_date == today else 0
        ) + 1
        self.store.update_proactive_state(
            self.session_id,
            daily_count=daily_count,
            daily_date=today,
            last_spoken_at=event.created_at,
            last_event_id=event.event_id,
            unanswered_count=state.unanswered_count + 1,
        )
        return {
            "spoke": True,
            "reason": "ok",
            "reason_label": reason_label("ok"),
            "message": content,
            "message_id": message_id,
            "activity": activity,
            "trigger_reason": source.trigger_reason,
            "source": self._source_dict(source),
            "event": self._event_dict(event),
            "missed": [self._event_dict(item) for item in missed],
            "unread": self.store.count_unread_proactive_events(
                self.session_id
            ),
        }

    def _record_missed(self, now):
        missed = []
        for reminder in self.reminders.due(
            self.session_id,
            now=now,
            limit=10,
        ):
            due = _parse_time(reminder.due_at)
            if due is None or now - due < self.missed_grace:
                continue
            if self.store.proactive_source_exists(
                self.session_id,
                SOURCE_REMINDER,
                reminder.reminder_id,
            ):
                self.reminders.dismiss(reminder.reminder_id)
                continue
            source = ProactiveSource(
                source_type=SOURCE_REMINDER,
                source_id=reminder.reminder_id,
                content="提醒：{}".format(reminder.content),
                trigger_reason="错过了送达窗口：{}".format(
                    format_due(reminder.due_at, now=now)
                ),
                created_at=reminder.created_at,
                source_message_id=reminder.source_message_id,
            )
            content_digest, url_digest = source_hashes(source)
            event = self.store.add_proactive_event(
                session_id=self.session_id,
                source_type=source.source_type,
                source_id=source.source_id,
                source_message_id=source.source_message_id,
                trigger_reason=source.trigger_reason,
                content=compose_message(source),
                status=PROACTIVE_MISSED,
                reason="missed_window",
                content_hash=content_digest,
                url_hash=url_digest,
                created_at=now.astimezone(timezone.utc).isoformat(),
            )
            self.reminders.dismiss(reminder.reminder_id)
            missed.append(event)
        return missed

    def _preview_dict(self, preview, activity, enabled):
        source = preview.source
        return {
            "speak": preview.speak,
            "reason": preview.reason,
            "reason_label": reason_label(preview.reason),
            "activity": activity,
            "enabled": enabled,
            "source": (
                None if source is None else self._source_dict(source)
            ),
            "sources": [
                self._source_dict(item) for item in preview.sources
            ],
        }

    @staticmethod
    def _source_dict(source):
        return {
            "source_type": source.source_type,
            "source_id": source.source_id,
            "content": source.content,
            "trigger_reason": source.trigger_reason,
            "source_message_id": source.source_message_id,
        }

    @staticmethod
    def _event_dict(event):
        return {
            "event_id": event.event_id,
            "source_type": event.source_type,
            "source_id": event.source_id,
            "trigger_reason": event.trigger_reason,
            "content": event.content,
            "status": event.status,
            "status_label": status_label(event.status),
            "reason": event.reason,
            "message_id": event.message_id,
            "read_at": event.read_at,
            "created_at": event.created_at,
        }
