"""Safe Markdown subset shared by the Qt and Web chat bubbles.

Everything is HTML-escaped before the small formatting vocabulary is added, so
raw HTML in a model reply can never reach a rich-text layer. Links are limited
to http/https and are still opened through an explicit user confirmation.

The renderer emits semantic tags with class hooks only; each front end owns its
presentation (Qt injects ``qt_style()`` as a ``<style>`` block, Web uses
``app.css``). ``web/assets/markdown.js`` is the same grammar in JavaScript and
``tests/fixtures/markdown_samples.json`` is the sample set both are checked
against (R22).
"""

from __future__ import annotations

import html
import re
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from .theme import TYPE_TOKENS, normalize_font_scale


_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_STRIKE = re.compile(r"~~([^~\n]+)~~")
_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_BARE_URL = re.compile(r"(^|[\s(])(https?://[^\s<)]+)")
_TAG = re.compile(r"(<[^>]+>)")
_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")
_LIST_ITEM = re.compile(r"^(\s*)([-*]|[0-9]+[.)])\s+(.*)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_FENCE = re.compile(r"^\s*```\s*([\w+#.-]*)\s*$")
_DIVIDER_CELL = re.compile(r"^:?-+:?$")
_PLACEHOLDER = re.compile(r"\x00(\d+)\x00")
_URL_TAIL = ",.;:!?，。；：！？、）》」』\""

_ESCAPES = (
    ("&", "&amp;"),
    ("<", "&lt;"),
    (">", "&gt;"),
    ('"', "&quot;"),
    ("'", "&#39;"),
)


def _escape(text) -> str:
    """Same five replacements as ``escapeHtml()`` in ``markdown.js``."""
    value = str(text if text is not None else "")
    for char, entity in _ESCAPES:
        value = value.replace(char, entity)
    return value


def _heading_size(level):
    return TYPE_TOKENS["large"] if level <= 3 else TYPE_TOKENS["body"]


def safe_url(url) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return value


def inline(text) -> str:
    """Escape first, then add the inline vocabulary; code spans win over markup."""
    escaped = _escape(text)
    code: List[str] = []

    def stash(match):
        code.append(match.group(1))
        return "\x00{}\x00".format(len(code) - 1)

    escaped = _INLINE_CODE.sub(stash, escaped)
    escaped = _BOLD.sub(r"<b>\1</b>", escaped)
    escaped = _STRIKE.sub(r"<s>\1</s>", escaped)
    escaped = _LINK.sub(_link_replacement, escaped)
    escaped = _autolink(escaped)
    # Placeholders were escaped before stashing; re.sub does not rescan its
    # replacements, so a code span that itself contains \x00 is harmless.
    return _PLACEHOLDER.sub(
        lambda match: "<code>{}</code>".format(code[int(match.group(1))]),
        escaped,
    )


def _autolink(escaped: str) -> str:
    parts = _TAG.split(escaped)
    for index, part in enumerate(parts):
        if not part or part.startswith("<"):
            continue
        parts[index] = _BARE_URL.sub(_bare_replacement, part)
    return "".join(parts)


def _bare_replacement(match):
    prefix, url = match.group(1), match.group(2)
    tail = ""
    while url and url[-1] in _URL_TAIL:
        tail = url[-1] + tail
        url = url[:-1]
    if safe_url(html.unescape(url)):
        return '{}<a href="{}">{}</a>{}'.format(prefix, url, url, tail)
    return match.group(0)


def _link_replacement(match):
    label, url = match.group(1), match.group(2)
    if not safe_url(html.unescape(url)):
        return "{} ({})".format(label, url)
    return '<a href="{}">{}</a>'.format(url, label)


def render(text) -> str:
    """Return safe rich text for a complete or partially streamed reply."""
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: List[str] = []
    index = 0
    total = len(lines)
    while index < total:
        line = lines[index]
        fence = _FENCE.match(line)
        if fence:
            language = fence.group(1)
            index += 1
            body: List[str] = []
            while index < total and not _FENCE.match(lines[index]):
                body.append(lines[index])
                index += 1
            if index < total:
                index += 1
            blocks.append(_code_block(language, body))
            continue
        if not line.strip():
            index += 1
            continue
        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            blocks.append(
                '<div class="md-h md-h{}"><b>{}</b></div>'.format(
                    level, inline(heading.group(2))
                )
            )
            index += 1
            continue
        if _QUOTE.match(line):
            quoted: List[str] = []
            while index < total:
                match = _QUOTE.match(lines[index])
                if not match:
                    break
                quoted.append(inline(match.group(1)))
                index += 1
            blocks.append(
                '<blockquote class="md-quote">{}</blockquote>'.format(
                    "<br/>".join(quoted)
                )
            )
            continue
        item = _LIST_ITEM.match(line)
        if item:
            items: List[Tuple[int, bool, str]] = []
            while index < total:
                match = _LIST_ITEM.match(lines[index])
                if not match:
                    break
                depth = len(match.group(1).expandtabs(4)) // 2
                items.append((depth, match.group(2)[0].isdigit(), match.group(3)))
                index += 1
            blocks.append(_list_block(items))
            continue
        table = _table_block(lines, index)
        if table is not None:
            block, index = table
            blocks.append(block)
            continue
        paragraph: List[str] = []
        while index < total and lines[index].strip():
            if paragraph and (_starts_block(lines[index]) or _table_block(lines, index)):
                break
            paragraph.append(inline(lines[index]))
            index += 1
        blocks.append("<div>{}</div>".format("<br/>".join(paragraph)))
    return "".join(blocks) or "<div></div>"


def _starts_block(line) -> bool:
    return bool(
        _FENCE.match(line)
        or _HEADING.match(line)
        or _QUOTE.match(line)
        or _LIST_ITEM.match(line)
    )


def _list_block(items) -> str:
    """One nesting stack for both ends; a deeper level nests inside the open <li>."""
    parts: List[str] = []
    stack: List[List] = []  # [level, tag, item_open]

    def close_level():
        _, tag, item_open = stack.pop()
        if item_open:
            parts.append("</li>")
        parts.append("</{}>".format(tag))

    for level, ordered, text in items:
        tag = "ol" if ordered else "ul"
        level = max(0, min(int(level), 3))
        while stack and level < stack[-1][0]:
            close_level()
        if stack and level == stack[-1][0] and stack[-1][1] != tag:
            close_level()
        if not stack or level > stack[-1][0]:
            parts.append("<{}>".format(tag))
            stack.append([level, tag, False])
        if stack[-1][2]:
            parts.append("</li>")
        parts.append("<li>{}".format(inline(text)))
        stack[-1][2] = True
    while stack:
        close_level()
    return "".join(parts)


def _split_row(line) -> List[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    return [cell.strip() for cell in value.split("|")]


def _divider_aligns(line) -> Optional[List[str]]:
    value = line.strip()
    if "|" not in value or "-" not in value:
        return None
    if any(char not in "-:| " for char in value):
        return None
    aligns = []
    for cell in _split_row(value):
        if not _DIVIDER_CELL.match(cell):
            return None
        if cell.startswith(":") and cell.endswith(":"):
            aligns.append("center")
        elif cell.endswith(":"):
            aligns.append("right")
        else:
            aligns.append("left")
    return aligns or None


def _table_block(lines, index):
    if index + 1 >= len(lines):
        return None
    header = lines[index]
    if "|" not in header:
        return None
    aligns = _divider_aligns(lines[index + 1])
    if aligns is None:
        return None
    head = _split_row(header)
    if len(head) != len(aligns):
        return None
    rows: List[List[str]] = []
    cursor = index + 2
    while cursor < len(lines) and lines[cursor].strip() and "|" in lines[cursor]:
        row = _split_row(lines[cursor])
        rows.append((row + [""] * len(aligns))[: len(aligns)])
        cursor += 1
    parts = [
        '<table class="md-table" border="1" cellspacing="0" cellpadding="4">',
        "<tr>",
    ]
    for align, cell in zip(aligns, head):
        parts.append('<th align="{}">{}</th>'.format(align, inline(cell)))
    parts.append("</tr>")
    for row in rows:
        parts.append("<tr>")
        for align, cell in zip(aligns, row):
            parts.append('<td align="{}">{}</td>'.format(align, inline(cell)))
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts), cursor


def _code_block(language, lines) -> str:
    body = _escape("\n".join(lines))
    label = ""
    if language:
        label = '<div class="md-lang">{}</div>'.format(_escape(language))
    return '{}<pre class="md-code">{}</pre>'.format(label, body)


def qt_style(font_scale=100) -> str:
    """Presentation for Qt rich text; the Web uses ``app.css`` instead."""
    scale = normalize_font_scale(font_scale) / 100.0

    def px(token):
        return int(round(TYPE_TOKENS[token] * scale))

    return (
        "<style>"
        ".md-h1, .md-h2, .md-h3 { font-size: %(heading)dpx; margin-top: %(mt)dpx;"
        " margin-bottom: %(mb)dpx; }"
        ".md-lang { color: #8e8e93; font-size: %(meta)dpx; }"
        "pre.md-code { background-color: rgba(127, 127, 127, 0.12);"
        " border: 1px solid rgba(127, 127, 127, 0.28); padding: %(pad)dpx;"
        " font-family: Consolas, monospace; white-space: pre-wrap; }"
        "code { background-color: rgba(127, 127, 127, 0.18);"
        " font-family: Consolas, monospace; }"
        "blockquote.md-quote { color: #8e8e93; margin-left: %(indent)dpx; }"
        "th { background-color: rgba(127, 127, 127, 0.12); }"
        "table.md-table { border-color: rgba(127, 127, 127, 0.35); }"
        "</style>"
    ) % {
        "heading": int(round(_heading_size(1) * scale)),
        "meta": px("meta"),
        "mt": int(round(4 * scale)),
        "mb": int(round(2 * scale)),
        "pad": int(round(6 * scale)),
        "indent": int(round(10 * scale)),
    }


def document(text, font_scale=100) -> str:
    """Rendered reply plus the Qt presentation block, for QLabel/QTextDocument."""
    return "{}{}".format(qt_style(font_scale), render(text))
