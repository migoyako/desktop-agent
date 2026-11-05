"""Deterministic DOCX -> Markdown conversion using only the standard library.

A ``.docx`` is an OPC zip whose text lives in ``word/document.xml``, so a
lexical document library needs no third-party parser. The conversion is a pure
function: the same bytes always produce the same Markdown, which keeps a 检索
命中 a quote of the user's file rather than a retelling of it.

Hostile input is refused, never guessed at: XML that declares entities (an
expansion bomb needs a DOCTYPE), a package with no body, macro containers, and
bodies too large to parse safely. Real Word files write none of the first two
(verified against the samples in ``course05-acp/docs``).
"""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, List

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MAX_XML_BYTES = 16 * 1024 * 1024

ENTITY_MARK = re.compile(rb"<!DOCTYPE|<!ENTITY", re.IGNORECASE)
HEADING_NAME = re.compile(r"^(?:heading|标题)\s*([1-6])\b", re.IGNORECASE)


class DocxFormatError(ValueError):
    """The .docx cannot be read as a plain WordprocessingML body."""


def _tag(name: str) -> str:
    return "{{{}}}{}".format(WORD_NS, name)


def docx_to_markdown(payload: bytes) -> str:
    """把 .docx 字节转成 Markdown；任何读不出的输入都抛 ``DocxFormatError``。"""
    data = bytes(payload or b"")
    if not data:
        raise DocxFormatError("没有读到文件内容。")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError):
        raise DocxFormatError("这个 .docx 不是有效的 Word 文档包（zip 结构已损坏）。")
    with archive:
        names = set(archive.namelist())
        if "word/document.xml" not in names:
            raise DocxFormatError("文档包里找不到正文（缺少 word/document.xml）。")
        body = _read_entry(archive, "word/document.xml")
        styles = (
            _style_levels(_read_entry(archive, "word/styles.xml"))
            if "word/styles.xml" in names
            else {}
        )
        numbering = (
            _numbering(_read_entry(archive, "word/numbering.xml"))
            if "word/numbering.xml" in names
            else {}
        )
    root = _parse(body)
    body_element = root.find(_tag("body"))
    if body_element is None:
        raise DocxFormatError("正文里没有 body 节点，无法读取内容。")
    blocks: List[str] = []
    for element in _blocks(body_element):
        if element.tag == _tag("tbl"):
            rendered = _table(element)
        else:
            rendered = _paragraph(element, styles, numbering)
        if rendered:
            blocks.append(rendered)
    markdown = "\n\n".join(blocks).strip()
    if not markdown:
        raise DocxFormatError("这份文档里没有可读取的正文文本（可能只有图片或批注）。")
    return markdown


def _read_entry(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError:
        raise DocxFormatError("文档包里缺少 {}。".format(name))
    if int(info.file_size) > MAX_XML_BYTES:
        raise DocxFormatError("文档正文过大，已拒绝解析。")
    with archive.open(name) as handle:
        data = handle.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES:
        raise DocxFormatError("文档正文过大，已拒绝解析。")
    if ENTITY_MARK.search(data):
        raise DocxFormatError("文档 XML 声明了实体，为避免解压炸弹已拒绝解析。")
    return data


def _parse(data: bytes):
    try:
        return ET.fromstring(data)
    except ET.ParseError as error:
        raise DocxFormatError("文档正文的 XML 结构不完整，无法解析：{}".format(error))


def _style_levels(styles_xml: bytes) -> Dict[str, int]:
    """styleId -> 标题层级，先看 ``outlineLvl``，再看样式名（``heading 2``）。"""
    try:
        root = _parse(styles_xml)
    except DocxFormatError:
        return {}
    levels: Dict[str, int] = {}
    for style in root.findall(_tag("style")):
        style_id = style.get(_tag("styleId")) or ""
        if not style_id:
            continue
        outline = style.find("{}/{}".format(_tag("pPr"), _tag("outlineLvl")))
        if outline is not None:
            level = _int_value(outline.get(_tag("val")))
            if level is not None:
                levels[style_id] = min(max(level + 1, 1), 6)
                continue
        name = style.find(_tag("name"))
        if name is not None:
            match = HEADING_NAME.match((name.get(_tag("val")) or "").strip())
            if match:
                levels[style_id] = int(match.group(1))
    return levels


def _numbering(numbering_xml: bytes) -> Dict[str, str]:
    """numId -> ``bullet`` 或 ``ordered``；读不出来时按无序处理。"""
    try:
        root = _parse(numbering_xml)
    except DocxFormatError:
        return {}
    abstract: Dict[str, str] = {}
    for item in root.findall(_tag("abstractNum")):
        key = item.get(_tag("abstractNumId")) or ""
        level = item.find("{}/{}".format(_tag("lvl"), _tag("numFmt")))
        value = (level.get(_tag("val")) or "").lower() if level is not None else ""
        abstract[key] = "bullet" if value == "bullet" else "ordered"
    result: Dict[str, str] = {}
    for item in root.findall(_tag("num")):
        key = item.get(_tag("numId")) or ""
        reference = item.find(_tag("abstractNumId"))
        if not key or reference is None:
            continue
        result[key] = abstract.get(reference.get(_tag("val")) or "", "bullet")
    return result


def _blocks(body_element):
    """按文档顺序给出段落与表格，并下钻进 ``w:sdt`` 的内容。"""
    for child in body_element:
        if child.tag == _tag("p") or child.tag == _tag("tbl"):
            yield child
        elif child.tag == _tag("sdt"):
            content = child.find(_tag("sdtContent"))
            if content is not None:
                for element in _blocks(content):
                    yield element


def _paragraph(element, styles: Dict[str, int], numbering: Dict[str, str]) -> str:
    text = _runs(element)
    if not text.strip():
        return ""
    level = _heading_level(element, styles)
    if level:
        return "{} {}".format("#" * level, text.strip())
    mark = _list_mark(element, numbering)
    if mark:
        return "{}{}".format(mark, text.strip())
    return text.strip()


def _heading_level(paragraph, styles: Dict[str, int]) -> int:
    props = paragraph.find(_tag("pPr"))
    if props is None:
        return 0
    direct = props.find(_tag("outlineLvl"))
    if direct is not None:
        level = _int_value(direct.get(_tag("val")))
        if level is not None:
            return min(max(level + 1, 1), 6)
    style = props.find(_tag("pStyle"))
    if style is None:
        return 0
    return int(styles.get(style.get(_tag("val")) or "", 0) or 0)


def _list_mark(paragraph, numbering: Dict[str, str]) -> str:
    props = paragraph.find(_tag("pPr"))
    if props is None:
        return ""
    # numId 和 ilvl 都嵌在 w:numPr 里，直接挂在 pPr 上的是另一种写法；两种都要认。
    holder = props.find(_tag("numPr"))
    if holder is None:
        holder = props
    reference = holder.find(_tag("numId"))
    if reference is None:
        return ""
    if (reference.get(_tag("val")) or "0") in ("0", ""):
        return ""
    depth = 0
    level = holder.find(_tag("ilvl"))
    if level is None:
        level = props.find("{}/{}".format(_tag("numPr"), _tag("ilvl")))
    if level is not None:
        depth = max(0, _int_value(level.get(_tag("val"))) or 0)
    indent = "  " * depth
    # 有序列表每条都写 "1."：Markdown 渲染器会自己续号，这里不需要复刻计数器。
    kind = numbering.get(reference.get(_tag("val")) or "", "bullet")
    return "{}{} ".format(indent, "1." if kind == "ordered" else "-")


def _runs(paragraph) -> str:
    hidden = _drawing_runs(paragraph)
    pieces: List[str] = []
    for run in paragraph.iter(_tag("r")):
        if id(run) in hidden:
            continue
        raw = _run_text(run)
        if not raw:
            continue
        props = run.find(_tag("rPr"))
        marks = ""
        if _switch_on(props, "b"):
            marks += "**"
        if _switch_on(props, "i"):
            marks += "*"
        if not marks:
            pieces.append(raw)
            continue
        head = raw[: len(raw) - len(raw.lstrip())]
        tail = raw[len(raw.rstrip()) :]
        body = raw.strip()
        if not body:
            pieces.append(raw)
            continue
        pieces.append("{}{}{}{}{}".format(head, marks, body, marks, tail))
    text = "".join(pieces)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _run_text(run) -> str:
    parts: List[str] = []
    for node in run:
        if node.tag == _tag("t"):
            parts.append(node.text or "")
        elif node.tag == _tag("tab"):
            parts.append(" ")
        elif node.tag in (_tag("br"), _tag("cr")):
            parts.append(" / ")
        elif node.tag == _tag("noBreakHyphen"):
            parts.append("-")
    return "".join(parts)


def _drawing_runs(paragraph):
    """图片/文本框里的 run 不参与正文，否则内容会被重复读一遍。"""
    hidden = set()
    for name in ("drawing", "pict", "object"):
        for host in paragraph.iter(_tag(name)):
            for run in host.iter(_tag("r")):
                hidden.add(id(run))
    return hidden


def _table(element) -> str:
    rows: List[List[str]] = []
    for row in element.findall(_tag("tr")):
        cells = []
        for cell in row.findall(_tag("tc")):
            texts = [
                _paragraph(child, {}, {})
                for child in cell.findall(_tag("p"))
            ]
            joined = " ".join(value for value in texts if value)
            cells.append(joined.replace("|", "\\|").strip())
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    lines = [
        "| {} |".format(" | ".join(padded[0])),
        "| {} |".format(" | ".join(["---"] * width)),
    ]
    for row in padded[1:]:
        lines.append("| {} |".format(" | ".join(row)))
    return "\n".join(lines)


def _switch_on(props, name: str) -> bool:
    if props is None:
        return False
    element = props.find(_tag(name))
    if element is None:
        return False
    value = (element.get(_tag("val")) or "1").strip().lower()
    return value not in ("0", "false", "off", "none")


def _int_value(text) -> int:
    try:
        return int(str(text))
    except (TypeError, ValueError):
        return None
