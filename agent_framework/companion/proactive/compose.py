"""Deterministic proactive message templates.

Generation stays local so a proactive line can always be traced back to its
source and never invents facts. The model is not called for this path.
"""

from __future__ import annotations


MAX_PROACTIVE_CHARS = 300


def _body(text) -> str:
    body = " ".join(str(text or "").split())
    if body.startswith("提醒："):
        body = body[3:].strip()
    return body


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    if text[-1] in "。！？!?…":
        return text
    return text + "。"


def compose_message(source) -> str:
    body = _body(source.content)
    if not body:
        return ""
    if source.source_type == "reminder":
        text = "提醒你一下：{}".format(_sentence(body))
    elif source.source_type == "unfinished_topic":
        text = "上次你问的「{}」还没有收尾，需要我继续吗？".format(body)
    elif source.source_type == "memory":
        text = "我想到你之前说过：{} 要聊聊吗？".format(_sentence(body))
    else:
        text = "想和你说一句：{}".format(_sentence(body))
    if len(text) > MAX_PROACTIVE_CHARS:
        text = text[: MAX_PROACTIVE_CHARS - 1] + "…"
    return text
