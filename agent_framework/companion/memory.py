"""Evidence-oriented long-term memory for the first companion version."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
import re
import time
from typing import Iterable, List


CHINESE = re.compile(r"[\u4e00-\u9fff]+")
TOKENS = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+")
PREFERENCE = re.compile(
    r"((?:我|本人)(?:现在|最近|其实)?\s*"
    r"(?:不喜欢|更喜欢|喜欢|偏好|习惯|希望)\s*"
    r"[^。！？\n]{1,40})"
)
PREFERENCE_OBJECT = re.compile(
    r"(?:不喜欢|更喜欢|喜欢|偏好|习惯|希望)\s*(.+)"
)
IDENTITY_PATTERNS = (
    (
        "identity:name",
        re.compile(r"((?:我|本人)(?:叫|的名字是)\s*[^。！？\n]{1,20})"),
    ),
    (
        "identity:birthday",
        re.compile(r"((?:我|本人)的生日是\s*[^。！？\n]{1,20})"),
    ),
    (
        "identity:job",
        re.compile(r"((?:我|本人)的(?:职业|工作)是\s*[^。！？\n]{1,30})"),
    ),
)
EXPLICIT = re.compile(r"(?:请)?记住[：:，,\s]*(.+)")
CONFIRM_SCORE = 0.70
CANDIDATE_SCORE = 0.45


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
                values.extend(
                    part[index : index + 2]
                    for index in range(len(part) - 1)
                )
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
        return max(
            0.0,
            min(1.0, activation * (2 ** (-elapsed_days / half_life))),
        )
    except (TypeError, ValueError):
        return activation


def recall_score(query: str, content: str, activation: float) -> float:
    query_tokens = set(tokenize(query))
    content_tokens = set(tokenize(content))
    if not query_tokens or not content_tokens:
        return 0.0
    overlap = len(query_tokens & content_tokens) / float(len(query_tokens))
    return overlap * (0.75 + 0.25 * max(0.0, min(activation, 1.0)))


def _clean(value: str) -> str:
    return (value or "").strip(" \t\r\n。！？")


def _normalize(value: str) -> str:
    return re.sub(r"[\s，。！？、,.!?：:；;\"'“”‘’]", "", value or "").casefold()


def _content_hash(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def _query_hash(query: str) -> str:
    return hashlib.sha256(
        (query or "").strip().casefold().encode("utf-8")
    ).hexdigest()


def _tombstone_blocks(memory, tombstones) -> bool:
    content_hash = _content_hash(memory.content)
    source_id = int(memory.source_message_id or 0)
    for tombstone in tombstones:
        if tombstone["content_hash"] == content_hash:
            return True
        if (
            tombstone["canonical_key"]
            and tombstone["canonical_key"] == memory.canonical_key
            and tombstone["through_seq"] >= source_id
        ):
            return True
    return False


def score_memory(text: str, kind: str, explicit: bool) -> float:
    score = 0.45
    if explicit:
        score += 0.15
    if kind == "preference":
        score += 0.10
    elif kind == "identity":
        score += 0.05
    score += 0.10
    score += 0.05
    return round(min(score, 1.0), 4)


def _memory_reason(kind: str, explicit: bool) -> str:
    parts = ["用户消息原文"]
    if explicit:
        parts.append("显式记忆")
    if kind == "preference":
        parts.append("偏好模式")
    elif kind == "identity":
        parts.append("身份模式")
    else:
        parts.append("事实模式")
    return "；".join(parts)


def _canonical_key(kind: str, content: str) -> str:
    if kind == "preference":
        match = PREFERENCE_OBJECT.search(content)
        target = match.group(1) if match else content
        return "preference:" + _normalize(target)
    if kind.startswith("identity:"):
        return kind
    return "fact:user:" + _normalize(content)


class MemoryService:
    def __init__(self, store):
        self.store = store
        self.last_recall_source = "keyword"

    def observe_user_message(
        self,
        session_id: str,
        message_id: int,
        text: str,
    ):
        text = (text or "").strip()
        if not text:
            return []
        if self.store.get_message_role(message_id) != "user":
            return []
        created = []
        seen_keys = set()
        for item in self._extract(text):
            if item["canonical_key"] in seen_keys:
                continue
            seen_keys.add(item["canonical_key"])
            if self.store.content_tombstoned(
                session_id,
                _content_hash(item["content"]),
            ):
                continue
            evidence = {
                "message_id": message_id,
                "quote": item["quote"],
            }
            existing = self.store.find_memory_by_key(
                session_id,
                item["canonical_key"],
            )
            if existing is not None and existing.content == item["content"]:
                reinforced = self.store.reinforce_memory(
                    existing.memory_id,
                    evidence,
                )
                if (
                    reinforced
                    and existing.status == "candidate"
                    and item["score"] >= CONFIRM_SCORE
                ):
                    self.store.promote_memory(
                        existing.memory_id,
                        item["score"],
                        item["reason"],
                    )
                continue
            if existing is not None:
                if item["score"] >= CONFIRM_SCORE:
                    self.store.archive_memory(
                        existing.memory_id,
                        "被新的用户记忆取代：{}".format(item["content"]),
                    )
                else:
                    self.store.add_memory_candidate(
                        session_id=session_id,
                        memory_id=existing.memory_id,
                        canonical_key=item["canonical_key"],
                        content=item["content"],
                        score=item["score"],
                        reason=item["reason"] + "；待确认的冲突候选",
                    )
                    continue
            memory_id = self.store.add_memory(
                session_id=session_id,
                kind=item["kind"],
                content=item["content"],
                source_message_id=message_id,
                confidence=item["score"],
                importance=0.7 if item["kind"] == "preference" else 0.6,
                status=item["status"],
                canonical_key=item["canonical_key"],
                evidence=evidence,
                candidate_score=item["score"],
                candidate_reason=item["reason"],
            )
            created.append(memory_id)
        return created

    def _extract(self, text: str):
        items = []
        for match in PREFERENCE.finditer(text):
            content = _clean(match.group(1))
            if not content:
                continue
            items.append(
                self._item(
                    kind="preference",
                    content=content,
                    quote=match.group(1),
                    explicit=False,
                )
            )
        for key, pattern in IDENTITY_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            content = _clean(match.group(1))
            if not content:
                continue
            items.append(
                self._item(
                    kind="identity",
                    content=content,
                    quote=match.group(1),
                    explicit=False,
                    canonical_key=key,
                )
            )
        explicit = EXPLICIT.search(text)
        if explicit and not items:
            content = _clean(explicit.group(1))
            if content:
                items.append(
                    self._item(
                        kind="fact",
                        content=content,
                        quote=explicit.group(1),
                        explicit=True,
                    )
                )
        return items

    @staticmethod
    def _item(
        kind,
        content,
        quote,
        explicit,
        canonical_key=None,
    ):
        score = score_memory(content, kind, explicit)
        return {
            "kind": kind,
            "content": content,
            "quote": quote.strip(),
            "canonical_key": canonical_key or _canonical_key(kind, content),
            "score": score,
            "status": (
                "confirmed" if score >= CONFIRM_SCORE else "candidate"
            ),
            "reason": _memory_reason(kind, explicit),
        }

    FTS_CANDIDATE_LIMIT = 30

    def recall(self, session_id: str, query: str, limit: int = 5):
        started = time.perf_counter()
        tombstones = self.store.list_memory_tombstones(session_id)
        ranked = self._recall_via_fts(session_id, query, limit, tombstones)
        source = "fts"
        if ranked is None:
            ranked = self._recall_via_keywords(
                session_id,
                query,
                limit,
                tombstones,
            )
            source = "keyword"
        self.last_recall_source = source
        self.store.log_recall(
            session_id,
            _query_hash(query),
            [memory.memory_id for _, memory, _ in ranked],
            [round(score, 6) for score, _, _ in ranked],
            (time.perf_counter() - started) * 1000.0,
            source=source,
        )
        return ranked

    def _recall_via_fts(self, session_id, query, limit, tombstones):
        """FTS5/BM25 检索候选，再用单一衰减公式重排。

        返回 ``None`` 表示 FTS 不可用、查询失败或没有候选，
        调用方降级为关键词召回；聊天主流程永不因索引异常阻塞。
        """
        try:
            hits = self.store.search_memories_fts(
                session_id,
                query,
                limit=self.FTS_CANDIDATE_LIMIT,
            )
        except Exception:
            return None
        if not hits:
            return None
        records = self.store.get_memories_by_ids(
            [hit["memory_id"] for hit in hits]
        )
        ranked = []
        for hit in hits:
            memory = records.get(hit["memory_id"])
            if memory is None or memory.status != "confirmed":
                continue
            if _tombstone_blocks(memory, tombstones):
                continue
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

    def _recall_via_keywords(self, session_id, query, limit, tombstones):
        """原关键词重叠召回，作为 FTS 不可用时的降级路径。"""
        memories = self.store.list_memories(
            session_id,
            statuses=["confirmed"],
            limit=200,
        )
        ranked = []
        for memory in memories:
            if _tombstone_blocks(memory, tombstones):
                continue
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
            source = (
                "#{}".format(memory.source_message_id)
                if memory.source_message_id is not None
                else "-"
            )
            if self.last_recall_source == "fts":
                reason = (
                    "结构化记忆+BM25召回"
                    if memory.canonical_key
                    else "BM25召回"
                )
            else:
                reason = (
                    "结构化记忆+关键词"
                    if memory.canonical_key
                    else "关键词匹配"
                )
            lines.append(
                "[记忆 {} | {} | 来源消息 {} | 证据 {} 条 | 原因 {}] {}".format(
                    memory.memory_id,
                    memory.kind,
                    source,
                    memory.evidence_count,
                    reason,
                    memory.content,
                )
            )
        return lines

    def correct(self, memory_id: str, replacement: str):
        replacement = (replacement or "").strip()
        if not replacement:
            raise ValueError("纠正后的记忆不能为空。")
        memory = self.store.get_memory(memory_id)
        if memory is None:
            raise ValueError("找不到这条记忆。")
        self.store.update_memory(
            memory_id,
            content=replacement,
            status="confirmed",
        )
        self.store.add_memory_event(
            memory_id,
            memory.session_id,
            "corrected",
            {"old": memory.content, "new": replacement},
        )

    def forget(self, memory_id: str):
        memory = self.store.get_memory(memory_id)
        if memory is None:
            raise ValueError("找不到这条记忆。")
        epoch = int(datetime.now(timezone.utc).timestamp() * 1000)
        through_seq = self.store.max_message_id(memory.session_id)
        self.store.update_memory(memory_id, status="forgotten")
        self.store.add_memory_event(
            memory_id,
            memory.session_id,
            "forgotten",
            {"epoch": epoch},
        )
        if memory.canonical_key:
            for other in self.store.list_memories(
                memory.session_id,
                limit=500,
            ):
                if (
                    other.memory_id != memory_id
                    and other.canonical_key == memory.canonical_key
                    and other.status in ("candidate", "confirmed")
                ):
                    self.store.update_memory(
                        other.memory_id,
                        status="forgotten",
                    )
                    self.store.add_memory_event(
                        other.memory_id,
                        other.session_id,
                        "forgotten_cascade",
                        {"source_memory_id": memory_id},
                    )
        self.store.add_memory_tombstone(
            memory.session_id,
            memory.canonical_key,
            _content_hash(memory.content),
            epoch,
            through_seq,
        )

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
