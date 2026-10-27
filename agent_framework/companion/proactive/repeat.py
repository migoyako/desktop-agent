"""Content/URL hashing and half-life based anti-repeat rules."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import re


DEDUPE_HALF_LIFE_HOURS = {
    "reminder": 0.0,
    "unfinished_topic": 24.0,
    "memory": 72.0,
}
DEDUPE_THRESHOLD = 0.5

URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
NORMALIZE_PATTERN = re.compile(
    r"[\s，,。.!！?？；;：:\"'“”‘’（）()\[\]【】<>《》\-_/\\]+"
)


def normalize_content(text) -> str:
    return NORMALIZE_PATTERN.sub("", str(text or "")).lower()


def hash_text(text) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def content_hash(text) -> str:
    return hash_text(normalize_content(text))


def url_hash(text) -> str:
    urls = sorted(
        url.rstrip("，,。.；;）)】]")
        for url in URL_PATTERN.findall(str(text or ""))
    )
    if not urls:
        return ""
    return hash_text("\n".join(urls))


def source_hashes(source):
    return content_hash(source.content), url_hash(source.content)


def _event_value(event, name):
    if isinstance(event, dict):
        return event.get(name)
    return getattr(event, name, None)


def _parse_time(value):
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment


def duplicate_score(source_type, source_content, events, now=None):
    """Return 0..1 similarity weight of a previously spoken source.

    A source decays by half every ``DEDUPE_HALF_LIFE_HOURS[source_type]``
    hours; 1.0 means just spoken, 0.5 one half-life ago. Reminders use a zero
    half-life because repeating the same text is an explicit user request.
    """
    half_life = float(
        DEDUPE_HALF_LIFE_HOURS.get(source_type, 0.0) or 0.0
    )
    if half_life <= 0:
        return 0.0
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.astimezone()
    current_hash = content_hash(source_content)
    current_url = url_hash(source_content)
    best = 0.0
    for event in events or []:
        if _event_value(event, "source_type") != source_type:
            continue
        if _event_value(event, "status") not in ("delivered", "missed"):
            continue
        event_hash = _event_value(event, "content_hash") or content_hash(
            _event_value(event, "content")
        )
        event_url = _event_value(event, "url_hash") or url_hash(
            _event_value(event, "content")
        )
        matched = event_hash == current_hash or (
            bool(current_url) and event_url == current_url
        )
        if not matched:
            continue
        created = _parse_time(_event_value(event, "created_at"))
        if created is None:
            continue
        age_hours = max(0.0, (now - created).total_seconds() / 3600.0)
        best = max(best, 0.5 ** (age_hours / half_life))
    return best


def is_duplicate(source, events, now=None) -> bool:
    return (
        duplicate_score(
            source.source_type,
            source.content,
            events,
            now=now,
        )
        >= DEDUPE_THRESHOLD
    )


def recent_events(events, since_hours=168):
    now = datetime.now().astimezone()
    cutoff = now - timedelta(hours=max(0, int(since_hours)))
    kept = []
    for event in events or []:
        created = _parse_time(_event_value(event, "created_at"))
        if created is not None and created >= cutoff:
            kept.append(event)
    return kept
