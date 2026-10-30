"""Time formatting shared by the Qt widgets."""

from __future__ import annotations

from datetime import datetime


def clock(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return text[:16].replace("T", " ")
    if moment.tzinfo is not None:
        moment = moment.astimezone()
    return moment.strftime("%H:%M")
