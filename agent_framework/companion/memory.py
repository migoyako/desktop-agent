"""Evidence-oriented long-term memory for the first companion version."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Iterable, List


CHINESE = re.compile(r"[\u4e00-\u9fff]+")
TOKENS = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+")


def tokenize(text: str) -> List[str]:
    values = []
    lowered = (text or "").casefold()
    for part in TOKENS.findall(lowered):
        if CHINESE.fullmatch(part):
            if len(part) == 1:
                values.append(part)
            elif len(part) == 2:
                values.append(part)
            else:
                values.extend(part[index : index + 2] for index in range(len(part) - 1))
        else:
            values.append(part)
    return values


def effective_activation(
    activation: float,
    importance: float,
    created_at: str,
    now=None,
) -> float:
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        elapsed_days = max(0.0, (current - created).total_seconds() / 86400.0)
        half_life = 30.0 * (1.0 + 2.0 * float(importance))
        return max(0.0, min(1.0, activation * (2 ** (-elapsed_days / half_life))))
    except (TypeError, ValueError):
        return activation


def recall_score(query: str, content: str, activation: float) -> float:
    query_tokens = set(tokenize(query))
    content_tokens = set(tokenize(content))
    if not query_tokens or not content_tokens:
        return 0.0
    overlap = len(query_tokens & content_tokens) / float(len(query_tokens))
    return overlap * (0.75 + 0.25 * max(0.0, min(activation, 1.0)))


class MemoryService:
    def __init__(self, store):
        self.store = store

    def observe_user_message(
        self,
        session_id: str,
        message_id: int,
        text: str,
    ):
        text = (text or "").strip()
        def clean(value):
            return (value or "").strip(" \t\r\n。！？")
        if not text:
            return []
        created = []
        explicit = re.search(r"(?:请)?记住[：:，,\s]*(.+)", text)
        preference = re.search(
            r"((?:我|本人)(?:喜欢|偏好|更喜欢|不喜欢|习惯|希望).+?)(?:[。！？\n]|$)",
            text,
        )
        identity = re.search(
            r"((?:我|本人)(?:叫|是|的(?:名字|生日|职业|工作).{0,12}是).+?)(?:[。！？\n]|$)",
            text,
        )
        candidates = []
        if explicit:
            candidates.append(("fact", clean(explicit.group(1)), "confirmed", 0.8))
        if preference:
            candidates.append(("preference", clean(preference.group(1)), "confirmed", 0.75))
        if identity:
            candidates.append(("fact", clean(identity.group(1)), "candidate", 0.6))
        existing = {
            clean(item.content)
            for item in self.store.list_memories(session_id)
            if item.status != "forgotten"
        }
        seen = set(existing)
        for kind, content, status, confidence in candidates:
            if not content or content in existing:
                continue
            if content in seen:
                continue
            seen.add(content)
            created.append(
                self.store.add_memory(
                    session_id=session_id,
                    kind=kind,
                    content=content,
                    source_message_id=message_id,
                    confidence=confidence,
                    importance=0.7 if kind == "preference" else 0.6,
                    status=status,
                )
            )
        return created

    def recall(self, session_id: str, query: str, limit: int = 5):
        memories = self.store.list_memories(
            session_id,
            statuses=["confirmed"],
            limit=200,
        )
        ranked = []
        for memory in memories:
            activation = effective_activation(
                memory.activation,
                memory.importance,
                memory.created_at,
            )
            score = recall_score(query, memory.content, activation)
            if score > 0:
                ranked.append((score, memory, activation))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked[: max(1, min(limit, 20))]

    def context_lines(self, session_id: str, query: str, limit: int = 5):
        lines = []
        for _, memory, _ in self.recall(session_id, query, limit):
            lines.append(
                "[记忆 {} | {}] {}".format(
                    memory.memory_id,
                    memory.kind,
                    memory.content,
                )
            )
        return lines

    def correct(self, memory_id: str, replacement: str):
        replacement = (replacement or "").strip()
        if not replacement:
            raise ValueError("纠正后的记忆不能为空。")
        self.store.update_memory(
            memory_id,
            content=replacement,
            status="confirmed",
        )

    def forget(self, memory_id: str):
        self.store.update_memory(memory_id, status="forgotten")

    def remember_experience(
        self,
        session_id: str,
        title: str,
        summary: str,
        source_message_id,
        receipt_id,
    ):
        self.store.add_experience(
            session_id=session_id,
            title=title,
            summary=summary,
            source_message_id=source_message_id,
            receipt_id=receipt_id,
        )
        return self.store.add_memory(
            session_id=session_id,
            kind="shared_experience",
            content=summary,
            source_message_id=source_message_id,
            confidence=1.0,
            importance=0.8,
            status="confirmed",
        )


def relevance_tokens(text: str) -> Iterable[str]:
    return tokenize(text)
