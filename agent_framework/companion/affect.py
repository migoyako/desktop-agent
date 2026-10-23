"""Lightweight emotion snapshots and correction rules."""

from __future__ import annotations

import re


POSITIVE = re.compile(r"(开心|高兴|顺利|谢谢|喜欢|太好了|满意|轻松|激动)")
NEGATIVE = re.compile(r"(难过|焦虑|烦|累|疲惫|压力|生气|崩|失望|害怕|孤独)")
EXHAUSTED = re.compile(r"(很累|好累|困|疲惫|熬夜|没睡)")


DEFAULT_STATE = {
    "user": {
        "mood_label": "neutral",
        "valence": 0.0,
        "arousal": 0.35,
        "energy": None,
        "confidence": 0.0,
        "source": "default",
    },
    "agent": {
        "mood_label": "calm",
        "valence": 0.2,
        "arousal": 0.3,
        "energy": 0.8,
        "confidence": 0.5,
        "source": "default",
    },
}


class AffectService:
    def __init__(self, store):
        self.store = store

    def observe(self, session_id: str, message_id: int, text: str):
        positive = bool(POSITIVE.search(text or ""))
        negative = bool(NEGATIVE.search(text or ""))
        exhausted = bool(EXHAUSTED.search(text or ""))
        if negative:
            self.store.set_affect(
                session_id=session_id,
                target="user",
                mood_label="low" if not exhausted else "tired",
                valence=-0.55,
                arousal=0.65 if not exhausted else 0.3,
                energy=0.25 if exhausted else None,
                confidence=0.62,
                source_message_id=message_id,
                source="text-heuristic",
            )
            self.store.set_affect(
                session_id=session_id,
                target="agent",
                mood_label="concerned",
                valence=0.05,
                arousal=0.42,
                energy=0.78,
                confidence=0.55,
                source_message_id=message_id,
                source="respond-to-user",
            )
        elif positive:
            self.store.set_affect(
                session_id=session_id,
                target="user",
                mood_label="positive",
                valence=0.6,
                arousal=0.55,
                energy=None,
                confidence=0.58,
                source_message_id=message_id,
                source="text-heuristic",
            )
            self.store.set_affect(
                session_id=session_id,
                target="agent",
                mood_label="warm",
                valence=0.65,
                arousal=0.45,
                energy=0.82,
                confidence=0.58,
                source_message_id=message_id,
                source="respond-to-user",
            )

    def correct_user(self, session_id: str, mood_label: str, valence=None, source="用户纠正"):
        mapping = {
            "neutral": (0.0, 0.35),
            "positive": (0.55, 0.5),
            "low": (-0.5, 0.45),
            "tired": (-0.35, 0.2),
        }
        if mood_label not in mapping:
            raise ValueError("未知情绪标签：{}".format(mood_label))
        default_valence, arousal = mapping[mood_label]
        self.store.set_affect(
            session_id=session_id,
            target="user",
            mood_label=mood_label,
            valence=default_valence if valence is None else valence,
            arousal=arousal,
            energy=0.3 if mood_label == "tired" else None,
            confidence=1.0,
            source_message_id=None,
            source=source,
        )

    def snapshot(self, session_id: str):
        result = {}
        for target in ("user", "agent"):
            record = self.store.latest_affect(session_id, target)
            if record:
                result[target] = {
                    "mood_label": record.mood_label,
                    "valence": record.valence,
                    "arousal": record.arousal,
                    "energy": record.energy,
                    "confidence": record.confidence,
                    "source": record.source,
                    "created_at": record.created_at,
                }
            else:
                result[target] = dict(DEFAULT_STATE[target])
        return result

    def prompt_line(self, session_id: str) -> str:
        state = self.snapshot(session_id)
        user = state["user"]
        agent = state["agent"]
        return (
            "用户情绪估计：{} (置信度 {:.2f})；角色状态：{}，精力 {}。"
            "情绪只是估计，若用户纠正必须服从用户。"
        ).format(
            user["mood_label"],
            user["confidence"],
            agent["mood_label"],
            "未知" if agent["energy"] is None else "{:.2f}".format(agent["energy"]),
        )
