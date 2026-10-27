"""Local reminders: deterministic creation, due queries and closure."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re


MAX_REMINDER_CHARS = 200

_CHINESE_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

_UNIT_SECONDS = {
    "分钟": 60,
    "分": 60,
    "钟头": 3600,
    "小时": 3600,
    "天": 86400,
}

REMINDER_PATTERNS = (
    re.compile(
        r"(?:提醒我|记得提醒我|提醒一下我|提醒)"
        r"\s*(?:在)?\s*(?P<value>\d{1,3}|[零一二两三四五六七八九十]+)"
        r"\s*(?P<unit>分钟|分|钟头|小时|天)\s*(?:后|之后|以后)\s*"
        r"(?P<content>.+)"
    ),
    re.compile(
        r"(?P<value>\d{1,3}|[零一二两三四五六七八九十]+)"
        r"\s*(?P<unit>分钟|分|钟头|小时|天)\s*(?:后|之后|以后)\s*"
        r"(?:提醒我|叫我|喊我)\s*(?P<content>.+)"
    ),
)


@dataclass
class ReminderDraft:
    content: str
    due_at: datetime
    delay_seconds: int


def _parse_value(token: str) -> int:
    token = (token or "").strip()
    if token.isdigit():
        return int(token)
    if len(token) == 1:
        return _CHINESE_DIGITS.get(token, 0)
    return 0


def _clean_content(text: str) -> str:
    content = (text or "").strip().strip("，,。.；;！!？? ")
    if len(content) > MAX_REMINDER_CHARS:
        content = content[: MAX_REMINDER_CHARS - 1] + "…"
    return content


def parse_reminder_request(text, now=None):
    text = (text or "").strip()
    if not text:
        return None
    now = now or datetime.now(timezone.utc)
    for pattern in REMINDER_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        amount = _parse_value(match.group("value"))
        unit_seconds = _UNIT_SECONDS.get(match.group("unit"))
        content = _clean_content(match.group("content"))
        if amount <= 0 or unit_seconds is None or not content:
            continue
        delay_seconds = amount * unit_seconds
        return ReminderDraft(
            content=content,
            due_at=now + timedelta(seconds=delay_seconds),
            delay_seconds=delay_seconds,
        )
    return None


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def format_due(due_at: str, now=None) -> str:
    try:
        moment = datetime.fromisoformat(str(due_at))
    except ValueError:
        return str(due_at)
    if moment.tzinfo is None:
        moment = moment.astimezone()
    local = moment.astimezone()
    now = now or datetime.now().astimezone()
    if local.date() == now.date():
        return "今天 {}".format(local.strftime("%H:%M"))
    return local.strftime("%m-%d %H:%M")


class ReminderService:
    def __init__(self, store):
        self.store = store

    def create(self, session_id, content, due_at, source_message_id=None):
        content = _clean_content(content)
        if not content:
            raise ValueError("提醒内容不能为空。")
        if not isinstance(due_at, datetime):
            raise TypeError("due_at 必须是 datetime。")
        return self.store.add_reminder(
            session_id=session_id,
            content=content,
            due_at=iso_utc(due_at),
            source_message_id=source_message_id,
        )

    def due(self, session_id, now=None, limit=5):
        now = now or datetime.now(timezone.utc)
        return self.store.due_reminders(session_id, iso_utc(now), limit=limit)

    def next_due(self, session_id):
        return self.store.next_reminder_due(session_id)

    def pending(self, session_id):
        return self.store.list_reminders(
            session_id,
            status="pending",
            limit=50,
        )

    def complete(self, reminder_id):
        return self.store.close_reminder(reminder_id, "done")

    def dismiss(self, reminder_id):
        return self.store.close_reminder(reminder_id, "dismissed")
