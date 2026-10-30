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
    "秒": 1,
    "秒钟": 1,
    "分钟": 60,
    "分": 60,
    "钟头": 3600,
    "小时": 3600,
    "天": 86400,
}

_CN_NUMBER = "[零一二两三四五六七八九十]{1,3}"
_UNIT = "分钟|秒钟|分|秒|钟头|小时|天"
_DAY_OFFSET = {"今天": 0, "今晚": 0, "明天": 1, "明早": 1, "后天": 2}

_CLOCK_TOKEN = (
    r"(?:(?:今天|今晚|明天|明早|后天)\s*)?"
    r"(?:(?:凌晨|早上|上午|中午|下午|傍晚|晚上|夜里)\s*)?"
    r"(?:\d{1,2}|" + _CN_NUMBER + r")\s*(?:点|时|[:：])\s*"
    r"(?:(?:半|\d{1,2}|" + _CN_NUMBER + r")\s*分?)?"
)

_RELATIVE_VALUE = r"(?P<value>半|\d{1,3}|" + _CN_NUMBER + r")\s*(?P<unit>" + _UNIT + r")"

REMINDER_PATTERNS = (
    re.compile(
        r"(?:提醒我|记得提醒我|提醒一下我|提醒)\s*(?:在)?\s*"
        + _RELATIVE_VALUE
        + r"\s*(?:后|之后|以后)\s*(?P<content>.+)"
    ),
    re.compile(
        _RELATIVE_VALUE
        + r"\s*(?:后|之后|以后)\s*(?:提醒我|叫我|喊我)\s*(?P<content>.+)"
    ),
    re.compile(
        r"(?:提醒我|记得提醒我|提醒一下我|叫我|喊我|提醒)\s*(?:在|到)?\s*"
        r"(?P<when>" + _CLOCK_TOKEN + r")\s*(?:左右)?\s*"
        r"(?:去|要|记得|别忘了)?\s*(?P<content>.*)"
    ),
    re.compile(
        r"(?P<when>" + _CLOCK_TOKEN + r")\s*(?:左右)?\s*"
        r"(?:提醒我|叫我|喊我|记得提醒我|提醒)\s*(?P<content>.+)"
    ),
)

_RELATIVE_STRICT = re.compile(_RELATIVE_VALUE + r"\s*(?:后|之后|以后)")
_RELATIVE_LOOSE = re.compile(_RELATIVE_VALUE + r"\s*(?:后|之后|以后)?")
_CLOCK_DETAIL = re.compile(
    r"(?:(?P<day>今天|今晚|明天|明早|后天)\s*)?"
    r"(?:(?P<period>凌晨|早上|上午|中午|下午|傍晚|晚上|夜里)\s*)?"
    r"(?P<hour>\d{1,2}|" + _CN_NUMBER + r")\s*(?:点|时|[:：])\s*"
    r"(?:(?P<minute>半|\d{1,2}|" + _CN_NUMBER + r")\s*分?)?"
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
    if "十" in token:
        head, _, tail = token.partition("十")
        tens = _CHINESE_DIGITS.get(head, 1) if head else 1
        ones = _CHINESE_DIGITS.get(tail, 0) if tail else 0
        return tens * 10 + ones
    if len(token) == 1:
        return _CHINESE_DIGITS.get(token, 0)
    return 0


def _clean_content(text: str) -> str:
    content = (text or "").strip().strip("，,。.；;！!？? ")
    if len(content) > MAX_REMINDER_CHARS:
        content = content[: MAX_REMINDER_CHARS - 1] + "…"
    return content


def _relative_seconds(value, unit):
    unit_seconds = _UNIT_SECONDS.get(unit)
    if unit_seconds is None:
        return None
    token = (value or "").strip()
    if token == "半":
        return 1800 if unit in ("小时", "钟头") else None
    amount = _parse_value(token)
    return amount * unit_seconds if amount > 0 else None


def _clock_due(match, now):
    hour = _parse_value(match.group("hour"))
    minute_token = (match.group("minute") or "").strip()
    if minute_token == "半":
        minute = 30
    elif minute_token:
        minute = _parse_value(minute_token)
    else:
        minute = 0
    if hour > 23 or minute > 59:
        return None
    period = match.group("period")
    if period in ("下午", "傍晚", "晚上") and 1 <= hour <= 11:
        hour += 12
    elif period == "中午" and hour < 11:
        hour += 12
    elif period in ("凌晨", "夜里") and hour == 12:
        hour = 0
    local_now = now.astimezone() if now.tzinfo else now.replace(tzinfo=timezone.utc)
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    candidate += timedelta(days=_DAY_OFFSET.get(match.group("day"), 0))
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate


def resolve_when(text, now=None):
    """Turn a relative or wall-clock phrase into an aware datetime."""
    text = (text or "").strip()
    if not text:
        return None
    now = now or datetime.now(timezone.utc)
    # A machine-formatted timestamp is literal; reading it first keeps the
    # wall-clock patterns from mistaking "2026-10-29T08:00" for 08:00 today.
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        moment = None
    if moment is not None:
        if moment.tzinfo is None:
            moment = moment.astimezone()
        return moment
    # "…后" always means a duration; a bare "15分" inside "下午2点15分" does not,
    # so the loose duration match only runs after the wall-clock reading fails.
    strict = _RELATIVE_STRICT.search(text)
    if strict:
        seconds = _relative_seconds(strict.group("value"), strict.group("unit"))
        if seconds:
            return now + timedelta(seconds=seconds)
        return None
    clock = _CLOCK_DETAIL.search(text)
    if clock:
        return _clock_due(clock, now)
    loose = _RELATIVE_LOOSE.search(text)
    if loose:
        seconds = _relative_seconds(loose.group("value"), loose.group("unit"))
        if seconds:
            return now + timedelta(seconds=seconds)
    return None


def parse_reminder_request(text, now=None):
    text = (text or "").strip()
    if not text:
        return None
    now = now or datetime.now(timezone.utc)
    for pattern in REMINDER_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        content = _clean_content(match.group("content"))
        if not content:
            continue
        when = match.groupdict().get("when")
        if when:
            due_at = resolve_when(when, now)
        else:
            seconds = _relative_seconds(
                match.group("value"), match.group("unit")
            )
            due_at = None if seconds is None else now + timedelta(seconds=seconds)
        if due_at is None:
            continue
        delay_seconds = int((due_at - now).total_seconds())
        if delay_seconds <= 0:
            continue
        return ReminderDraft(
            content=content,
            due_at=due_at,
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
