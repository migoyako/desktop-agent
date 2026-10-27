"""Derive unfinished topics from recent messages without calling a model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re


QUESTION_MARKERS = re.compile(
    r"[?？]|(?:吗|呢|怎么|为什么|如何|能不能|可不可以|是否|什么|哪里|哪儿|谁|几点|多少|多久)"
)
FAILURE_MARKERS = (
    "处理消息时遇到问题",
    "工具调用轮次达到上限",
    "工具调用次数达到上限",
    "我没有生成有效回复",
    "搜索没有完成",
    "写入提案没有保存成功",
    "没有保存成功",
)
MAX_TOPIC_CHARS = 120


@dataclass(frozen=True)
class UnfinishedTopic:
    source_message_id: int
    content: str
    created_at: str
    reason: str


def _parse_time(value):
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment


def _is_failure_reply(text) -> bool:
    content = str(text or "")
    return bool(content) and any(
        marker in content for marker in FAILURE_MARKERS
    )


def derive_unfinished_topics(
    history,
    now=None,
    max_age_hours: int = 24,
    limit: int = 3,
):
    """Return recent user questions that never got a usable reply.

    A topic is unfinished when the question is the last message, is followed
    by another user message, or is followed by a known failure/limit notice.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.astimezone()
    items = [
        item
        for item in history or []
        if item.get("role") in ("user", "assistant")
    ]
    cutoff = timedelta(hours=max(1, int(max_age_hours)))
    topics = []
    for index, item in enumerate(items):
        if item.get("role") != "user":
            continue
        content = str(item.get("content") or "").strip()
        if not content or not QUESTION_MARKERS.search(content):
            continue
        created = _parse_time(item.get("created_at"))
        if created is None or now - created > cutoff:
            continue
        following = items[index + 1] if index + 1 < len(items) else None
        reason = None
        if following is None or following.get("role") == "user":
            reason = "unanswered"
        elif following.get("role") == "assistant" and _is_failure_reply(
            following.get("content")
        ):
            reason = "failed_reply"
        if reason is None:
            continue
        topics.append(
            UnfinishedTopic(
                source_message_id=int(item.get("id") or 0),
                content=content[:MAX_TOPIC_CHARS],
                created_at=str(item.get("created_at") or ""),
                reason=reason,
            )
        )
    topics.sort(key=lambda topic: topic.source_message_id, reverse=True)
    return topics[: max(1, int(limit))]
