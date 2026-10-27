"""Mask credential-looking values before diagnostics are persisted."""

from __future__ import annotations

import re
from typing import Any

MASK = "[REDACTED]"

PATTERNS = (
    re.compile(r"(?i)\b(sk-[A-Za-z0-9_-]{8,})"),
    re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/=-]{8,})"),
    re.compile(
        r"(?i)\b((?:api[_-]?key|access[_-]?token|secret|password|authorization)"
        r"\s*[:=]\s*)([^\s,;\"'）)]{6,})"
    ),
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})\b"),
)


def redact_text(value: Any) -> str:
    text = str(value if value is not None else "")
    for pattern in PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(
                lambda match: "{}{}".format(match.group(1), MASK),
                text,
            )
        else:
            text = pattern.sub(MASK, text)
    return text


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    return value
