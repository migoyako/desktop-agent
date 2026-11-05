"""文档库：入库、切块、词法检索与删除（M16 切片 1，纯后端）。

检索走 ``textseg`` 的 2/3-gram + FTS5/BM25，与记忆链同构但**两条链的分数
不可比**（记忆是 ``recall_score`` × 衰减，文档是 BM25），所以这里只返回词法
候选，混合与预算裁剪留给切片 2 的 ``layers["docs"]``。

写入顺序永远是"先验证、再落盘、最后进库"：任何拒绝都发生在第一次写之前，
不会留下半个文件或一行没有块的文档记录。
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
import tempfile
import time
from typing import Dict, List, Optional

from . import docx as docx_convert
from .models import new_id, utc_now

DOC_DIR = "docs"
DOC_SUFFIXES = {
    ".md": "md",
    ".markdown": "md",
    ".txt": "txt",
    ".csv": "csv",
    ".json": "json",
    ".log": "log",
    ".docx": "docx",
}
# 明确拒绝而不是"看起来支持"：每一条都说清缺什么。
REFUSED_HINTS = {
    ".pdf": "PDF 需要专门的解析器，本机默认没有装；请用任意工具把内容导出为 .md 或 .txt 后再上传。",
    ".doc": "旧版 .doc（二进制格式）读不了，请先在 Word 里另存为 .docx。",
    ".rtf": "不支持 .rtf，请另存为 .txt 或 .md 后再上传。",
    ".odt": "不支持 .odt，请另存为 .docx 或 .txt 后再上传。",
    ".docm": "带宏的 .docm 不入库，请另存为不含宏的 .docx。",
    ".dotx": "Word 模板 .dotx 不是文档正文，请另存为 .docx。",
}
MAX_DOC_BYTES = 2 * 1024 * 1024
MAX_LIBRARY_BYTES = 64 * 1024 * 1024
CHUNK_CHARS = 800
CHUNK_OVERLAP = 100
MAX_CHUNKS = 2000
TOC_HEADER = "本文档目录"
FTS_CANDIDATE_LIMIT = 30

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class DocumentError(ValueError):
    """入库被拒绝；消息始终是能直接给用户看的一句中文。"""


def clean_name(filename) -> str:
    name = CONTROL_CHARS.sub("", str(filename or "")).replace("\\", "/")
    name = Path(name).name.strip().strip(".")
    if not name:
        raise DocumentError("文件名是空的，无法入库。")
    suffix = Path(name).suffix.lower()
    stem = name[: len(name) - len(suffix)] if suffix else name
    stem = stem.replace("/", "_").replace("\\", "_")[:100] or "document"
    return "{}{}".format(stem, suffix[:16])


def split_windows(body: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """定长滑窗；``overlap`` 只用于避免句子正好被切掉。"""
    text = body.strip()
    if not text:
        return []
    if size <= 0:
        return [text]
    step = max(1, size - max(0, min(overlap, size - 1)))
    windows: List[str] = []
    start = 0
    while start < len(text):
        windows.append(text[start : start + size])
        start += step
    return windows


def chunk_text(text: str, as_markdown: bool = False) -> List[Dict]:
    """切块。Markdown 按标题成节并额外产出一个目录块，其余按定长滑窗。

    目录块来自参考项目的实测做法：列举类问题（"有哪些…？"）在纯片段召回里
    必然漏条目，一个汇总所有标题的块能把这类问题兜住。
    """
    body = (text or "").strip()
    if not body:
        return []
    if not as_markdown:
        return [
            {"heading": "", "text": piece} for piece in split_windows(body)
        ][:MAX_CHUNKS]

    blocks: List[Dict] = []
    headings: List[tuple] = []
    stack: List[tuple] = []
    buffer: List[str] = []
    in_fence = False

    def flush():
        joined = "\n".join(buffer).strip()
        del buffer[:]
        if not joined:
            return
        title = " > ".join(item[1] for item in stack)
        for piece in split_windows(joined):
            blocks.append(
                {
                    "heading": title,
                    "text": "{}\n{}".format(title, piece) if title else piece,
                }
            )

    for line in body.splitlines():
        if line.lstrip().startswith("```") or line.lstrip().startswith("~~~"):
            in_fence = not in_fence
            buffer.append(line)
            continue
        match = None if in_fence else HEADING.match(line)
        if match is None:
            buffer.append(line)
            continue
        flush()
        level = len(match.group(1))
        title = match.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        headings.append((level, title))
    flush()

    if len(headings) >= 2:
        toc = "\n".join(
            "{}- {}".format("  " * max(0, level - 1), title)
            for level, title in headings
        )
        blocks.insert(
            0,
            {
                "heading": TOC_HEADER,
                "text": "{}（共 {} 个小节）\n{}".format(
                    TOC_HEADER, len(headings), toc
                )[:CHUNK_CHARS],
            },
        )
    return blocks[:MAX_CHUNKS]


SOURCE_SNIPPET_CHARS = 240
# 一个块最长 800 字，拿整块去比"有没有被回复采用"永远判不中；只比标题与块首。
USAGE_PROBE_CHARS = 60


def doc_label(hit: Dict) -> str:
    """来源与提示词共用一个称呼：文档名 + 第几块（块号从 1 开始数）。"""
    return "{} · 第 {} 块".format(
        str(hit.get("name") or ""),
        int(hit.get("ordinal") or 0) + 1,
    )


def prompt_lines(hits: List[Dict]) -> List[str]:
    """每条命中一行，行内自带出处；``trim_lines`` 只会整行丢弃，不会截半句。"""
    lines = []
    for hit in hits or ():
        text = " ".join(str(hit.get("text") or "").split())
        if not text:
            continue
        heading = str(hit.get("heading") or "").strip()
        label = doc_label(hit)
        lines.append(
            "【{}{}】{}".format(
                label,
                "｜{}".format(heading) if heading else "",
                text,
            )
        )
    return lines


def source_items(hits: List[Dict]) -> List[Dict]:
    """文档命中折成来源通道的一条记录：``source_kind`` 区分网络出处与本机文档。"""
    items = []
    for hit in hits or ():
        text = " ".join(str(hit.get("text") or "").split())
        if not text:
            continue
        items.append(
            {
                "source_kind": "document",
                "title": doc_label(hit),
                "url": "",
                "snippet": text[:SOURCE_SNIPPET_CHARS],
                "heading": str(hit.get("heading") or ""),
                "path": str(hit.get("path") or ""),
                "document_id": str(hit.get("document_id") or ""),
                "chunk_id": str(hit.get("chunk_id") or ""),
                "ordinal": int(hit.get("ordinal") or 0),
            }
        )
    return items


def usage_probes(hits: List[Dict]) -> List[str]:
    """给“有没有被回复采用”用的短句：标题 + 块首，整块太长比不中。"""
    probes = []
    for hit in hits or ():
        text = " ".join(str(hit.get("text") or "").split())
        if not text:
            continue
        probes.append(
            "{} {}".format(
                str(hit.get("heading") or "").strip(),
                text[:USAGE_PROBE_CHARS],
            ).strip()
        )
    return probes


class DocumentService:
    """文档库的门面：落盘、进库、检索、删除都只认 document_id。"""

    def __init__(self, store, data_dir):
        self.store = store
        self.root = Path(data_dir) / DOC_DIR
        # 与记忆链同形：本轮召回行的 ID 由检索自己留着，调用方不必去猜。
        self.last_recall_id = ""

    def _slot(self, document_id: str) -> Path:
        return self.root / document_id

    def ingest(self, filename, payload: bytes, persona_id: str = "") -> Dict:
        data = bytes(payload or b"")
        name = clean_name(filename)
        suffix = Path(name).suffix.lower()
        if suffix in REFUSED_HINTS:
            raise DocumentError(
                "暂不支持 {}：{}".format(suffix, REFUSED_HINTS[suffix])
            )
        kind = DOC_SUFFIXES.get(suffix)
        if kind is None:
            raise DocumentError(
                "只支持 .md、.txt、.csv、.json、.log 与 .docx；"
                "PDF 请先用任意工具导出成 .md 或 .txt。"
            )
        if not data:
            raise DocumentError("没有读到文件内容。")
        if len(data) > MAX_DOC_BYTES:
            raise DocumentError(
                "单个文档不能超过 {:.0f} MiB。".format(MAX_DOC_BYTES / 1048576)
            )
        converted = kind == "docx"
        if converted:
            try:
                text = docx_convert.docx_to_markdown(data)
            except docx_convert.DocxFormatError as error:
                raise DocumentError(str(error))
        else:
            text = _decode_text(data, name)
        blocks = chunk_text(text, as_markdown=(kind in ("md", "docx")))
        if not blocks:
            raise DocumentError("这份文档里没有可检索的正文文本。")
        used = int(self.store.document_total_bytes())
        if used + len(data) > MAX_LIBRARY_BYTES:
            raise DocumentError(
                "文档库总量不能超过 {:.0f} MiB，请先删掉一些文档。".format(
                    MAX_LIBRARY_BYTES / 1048576
                )
            )
        document_id = new_id("doc")
        created_at = utc_now()
        slot = self._slot(document_id)
        self._prepare_slot(slot)
        try:
            stored = slot / name
            _atomic_write(stored, data)
            if converted:
                _atomic_write(slot / "converted.md", text.encode("utf-8"))
            relative = str(slot.relative_to(self.store_root()))
        except OSError as error:
            self._discard(slot)
            raise DocumentError("写入文档失败：{}".format(error))
        meta = {
            "document_id": document_id,
            "persona_id": str(persona_id or ""),
            "name": name,
            "kind": kind,
            "path": "{}/{}".format(relative, name).replace("\\", "/"),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "converted": converted,
            "chars": len(text),
            "created_at": created_at,
        }
        try:
            indexed = self.store.add_document(meta, blocks)
        except Exception as error:
            self._discard(slot)
            raise DocumentError("文档入库失败：{}".format(error))
        record = self.store.get_document(document_id) or meta
        record["chunks"] = len(blocks)
        record["indexed"] = indexed
        return record

    def store_root(self) -> Path:
        return Path(self.store.database_path).parent

    def search(
        self,
        query: str,
        limit: int = 5,
        persona_id: Optional[str] = None,
        session_id: str = "",
    ) -> List[Dict]:
        """词法检索：返回带原文片段的候选块，分数只是 BM25 候选序。"""
        text = str(query or "").strip()
        self.last_recall_id = ""
        if not text or limit <= 0:
            return []
        started = time.perf_counter()
        hits = self.store.search_documents_fts(
            text,
            limit=FTS_CANDIDATE_LIMIT,
            persona_id=persona_id,
        )
        if not hits:
            return []
        records = self.store.get_doc_chunks_by_ids(
            [hit["chunk_id"] for hit in hits]
        )
        ranked: List[Dict] = []
        for hit in hits:
            chunk = records.get(hit["chunk_id"])
            if not chunk:
                continue
            ranked.append(
                {
                    "chunk_id": hit["chunk_id"],
                    "document_id": chunk["document_id"],
                    "name": chunk["name"],
                    "kind": chunk["kind"],
                    "path": chunk["path"],
                    "ordinal": int(chunk["ordinal"]),
                    "heading": chunk["heading"],
                    "text": chunk["text"],
                    "chars": int(chunk["chars"]),
                    "bm25": float(hit["bm25"]),
                }
            )
            if len(ranked) >= int(limit):
                break
        if ranked and session_id:
            self.last_recall_id = self.store.log_recall(
                session_id,
                _query_hash(text),
                [item["chunk_id"] for item in ranked],
                [round(item["bm25"], 6) for item in ranked],
                (time.perf_counter() - started) * 1000.0,
                source="fts",
                scope="docs",
            )
        return ranked

    def list_documents(self, persona_id: Optional[str] = None) -> List[Dict]:
        return self.store.list_documents(persona_id=persona_id)

    def summary(self) -> Dict:
        rows = self.store.list_documents()
        return {
            "count": len(rows),
            "bytes": int(self.store.document_total_bytes()),
            "limit_bytes": MAX_LIBRARY_BYTES,
            "fts_enabled": bool(getattr(self.store, "doc_fts_enabled", False)),
        }

    def delete(self, document_id: str) -> Dict:
        text_id = str(document_id or "")
        record = self.store.get_document(text_id) if text_id else None
        if record is None:
            return {"removed": False, "chunks": 0, "files": 0}
        slot = self._slot(text_id)
        chunks = self.store.delete_document(text_id)
        removed_files = self._discard(slot)
        return {
            "removed": True,
            "chunks": chunks,
            "files": removed_files,
            "document_id": text_id,
        }

    def original_path(self, document_id: str) -> Optional[Path]:
        record = self.store.get_document(str(document_id or ""))
        if not record:
            return None
        candidate = (self.store_root() / str(record["path"])).resolve()
        root = self.root.resolve()
        if root not in candidate.parents:
            return None
        return candidate if candidate.is_file() else None

    def _prepare_slot(self, slot: Path):
        if self.root.is_symlink():
            raise DocumentError("文档目录不能是符号链接。")
        self.root.mkdir(parents=True, exist_ok=True)
        if slot.exists() or slot.is_symlink():
            raise DocumentError("文档槽位已存在，请重试。")
        try:
            slot.mkdir(parents=True)
        except FileExistsError:
            raise DocumentError("文档槽位已存在，请重试。")
        except OSError as error:
            raise DocumentError("创建文档目录失败：{}".format(error))

    def _discard(self, slot: Path) -> int:
        if not slot.is_dir() or slot.is_symlink():
            return 0
        count = 0
        for path in list(slot.iterdir()):
            if path.is_file() or path.is_symlink():
                try:
                    path.unlink()
                    count += 1
                except OSError:
                    pass
        try:
            slot.rmdir()
        except OSError:
            pass
        return count


def _decode_text(data: bytes, name: str) -> str:
    raw = data
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise DocumentError(
            "{} 不是 UTF-8 编码的文本，请另存为 UTF-8 后再上传。".format(name)
        )


def _atomic_write(target: Path, data: bytes):
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=str(target.parent),
            prefix=".tmp-",
            suffix=target.suffix[:12] or ".doc",
            delete=False,
        ) as handle:
            temp = Path(handle.name)
            handle.write(data)
        os.replace(temp, target)
    finally:
        if temp is not None and temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def _query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
