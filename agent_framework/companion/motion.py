"""Content-driven gestures: the model vocabulary and the [[action:]] markers.

The vocabulary is read from ``live2d/model3.json`` only (D58: no fallback
figure, no invented ids). Unknown ids are dropped instead of degrading into a
random motion, so a hallucinated marker is a silent no-op.
"""

from __future__ import annotations

from threading import Lock
from typing import Dict, Iterable, List, Sequence

# The standby group is the stage's own loop; naming it in a marker would fight
# the idle rotator, so it never enters the content vocabulary.
NON_GESTURE_GROUPS = frozenset({"idle"})

# A model may declare this Motion group in model3.json to say which of its
# clips are worth offering the model. Clips outside it still exist, they just
# do something the stage already holds (this pack's hand_to_chest only moves
# the hands from "hidden" to "visible", which is the rest pose).
ACTION_POOL_GROUP = "action_pool"

MAX_ACTIONS_PER_TURN = 3


def gesture_prompt(names: Sequence[str]) -> str:
    """The system-prompt block that teaches the model its own gesture ids."""
    return (
        "## 桌宠动作（只有列出的 id 会被执行，其它 id 会被丢弃）\n"
        "在回复正文里插入 [[action:动作id]] 可让桌宠做出对应动作；"
        "一次回复最多 {} 个，不要向用户解释这个标记，也不要编造列表外的 id。\n"
        "可用动作：{}".format(
            MAX_ACTIONS_PER_TURN, "、".join(str(name) for name in names)
        )
    )


def vocabulary(live2d: Dict) -> List[str]:
    """Motion groups plus expression names, as the model declares them."""
    if not isinstance(live2d, dict):
        return []
    names: List[str] = []
    groups = live2d.get("motion_groups") or {}
    pool = groups.get(ACTION_POOL_GROUP) if isinstance(groups, dict) else None
    declared = [str(name) for name in pool if str(name).strip()] if isinstance(pool, (list, tuple)) else []
    motions = live2d.get("motions") or []
    expressions = live2d.get("expressions") or []
    for group in declared or (motions if isinstance(motions, (list, tuple)) else []):
        if str(group).strip().lower() in NON_GESTURE_GROUPS:
            continue
        _append(names, group)
    for expression in expressions if isinstance(expressions, (list, tuple)) else []:
        _append(names, expression)
    return names


def _append(names: List[str], value) -> None:
    text = str(value or "").strip()
    if text and text not in names:
        names.append(text)


def plan_actions(markers: Iterable[Dict[str, str]], known: Sequence[str]) -> List[str]:
    """Validated gesture ids for one turn, in order, capped and de-duplicated."""
    lookup = {name.lower(): name for name in known}
    chosen: List[str] = []
    for marker in markers or []:
        if not isinstance(marker, dict):
            continue
        if str(marker.get("kind") or "").lower() != "action":
            continue
        value = str(marker.get("value") or "").strip()
        if not value:
            continue
        name = lookup.get(value.lower())
        if name is None or name in chosen:
            continue
        chosen.append(name)
        if len(chosen) >= MAX_ACTIONS_PER_TURN:
            break
    return chosen


class MotionQueue:
    """Pending gestures waiting for the overlay to pick them up (one-shot)."""

    def __init__(self, limit: int = 8):
        self._lock = Lock()
        self._rows: List[Dict[str, object]] = []
        self._next_id = 0
        self._limit = max(1, int(limit))

    def push(self, names: Sequence[str]) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        with self._lock:
            for name in names or []:
                self._next_id += 1
                row = {"id": self._next_id, "name": str(name)}
                self._rows.append(row)
                rows.append(dict(row))
            overflow = len(self._rows) - self._limit
            if overflow > 0:
                del self._rows[:overflow]
        return rows

    def pending(self, limit: int = 3) -> List[Dict[str, object]]:
        with self._lock:
            return [dict(row) for row in self._rows[: max(0, int(limit))]]

    def mark_read(self, event_id) -> int:
        try:
            wanted = int(event_id)
        except (TypeError, ValueError):
            return 0
        with self._lock:
            keep = [row for row in self._rows if int(row["id"]) != wanted]
            removed = len(self._rows) - len(keep)
            self._rows = keep
        return removed

    def clear(self) -> None:
        with self._lock:
            self._rows = []
