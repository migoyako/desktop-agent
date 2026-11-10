"""M16 切片 1: the lexical document library (DOCX conversion, chunking, storage, HTTP).

Everything here is backend-only by design: the RAG/MCP panels stay greyed until
切片 4, so the contract these tests pin is the storage + HTTP surface, not a page.
"""

import base64
import io
import json
import socket
import sqlite3
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

from agent_framework.companion import docx as docx_tools
from agent_framework.companion import documents as doc_tools
from agent_framework.companion.app import create_application_service
from agent_framework.companion.documents import (
    TOC_HEADER,
    DocumentError,
    DocumentService,
    chunk_text,
    clean_name,
    split_windows,
)
from agent_framework.companion.maintenance import (
    EXPORT_TABLES,
    EXPECTED_INDEXES,
    create_backup,
    export_data,
    integrity_report,
    purge_all,
    purge_memories,
    purge_session,
)
from agent_framework.companion.models import AgentMode
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import SCHEMA_VERSION, CompanionStore
from agent_framework.companion.assets import MAX_AVATAR_BYTES
from agent_framework.companion.web import WebAdapter
from agent_framework.companion.web.server import MAX_REQUEST_BYTES
from agent_framework.llm import OfflineLLMClient

W = docx_tools.WORD_NS
XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


class StubSearch:
    def search(self, query, limit=5):
        return []


def document_xml(body):
    return "{}<w:document xmlns:w=\"{}\"><w:body>{}</w:body></w:document>".format(
        XML_HEAD, W, body
    )


def styles_xml(*definitions):
    return "{}<w:styles xmlns:w=\"{}\">{}</w:styles>".format(
        XML_HEAD, W, "".join(definitions)
    )


def style(style_id, inner):
    return '<w:style w:type="paragraph" w:styleId="{}">{}</w:style>'.format(
        style_id, inner
    )


def numbered_xml(num_id, fmt="bullet"):
    return (
        "{}<w:numbering xmlns:w=\"{}\">"
        '<w:abstractNum w:abstractNumId="0">'
        '<w:lvl w:ilvl="0"><w:numFmt w:val="{}"/></w:lvl>'
        "</w:abstractNum>"
        '<w:num w:numId="{}"><w:abstractNumId w:val="0"/></w:num>'
        "</w:numbering>"
    ).format(XML_HEAD, W, fmt, num_id)


def run(text, bold=False, italic=False):
    props = ""
    if bold or italic:
        marks = ("<w:b/>" if bold else "") + ("<w:i/>" if italic else "")
        props = "<w:rPr>{}</w:rPr>".format(marks)
    return "<w:r>{}<w:t>{}</w:t></w:r>".format(props, text)


def paragraph(*runs_, **kwargs):
    props = ""
    inner = ""
    if kwargs.get("style"):
        inner += '<w:pStyle w:val="{}"/>'.format(kwargs["style"])
    if kwargs.get("outline") is not None:
        inner += '<w:outlineLvl w:val="{}"/>'.format(kwargs["outline"])
    if kwargs.get("num") is not None:
        level = kwargs.get("ilvl", 0)
        inner += (
            "<w:numPr>"
            '<w:ilvl w:val="{}"/><w:numId w:val="{}"/>'
            "</w:numPr>".format(level, kwargs["num"])
        )
    if inner:
        props = "<w:pPr>{}</w:pPr>".format(inner)
    return "<w:p>{}{}</w:p>".format(props, "".join(runs_))


def table(rows):
    body = ""
    for row in rows:
        body += "<w:tr>{}</w:tr>".format(
            "".join("<w:tc><w:p>{}</w:p></w:tc>".format(run(cell)) for cell in row)
        )
    return "<w:tbl>{}</w:tbl>".format(body)


def build_docx(document, styles=None, numbering=None, extra=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", XML_HEAD + "<Types/>")
        archive.writestr("_rels/.rels", XML_HEAD + "<Relationships/>")
        archive.writestr("word/document.xml", document)
        if styles is not None:
            archive.writestr("word/styles.xml", styles)
        if numbering is not None:
            archive.writestr("word/numbering.xml", numbering)
        for name, payload in (extra or {}).items():
            archive.writestr(name, payload)
    return buffer.getvalue()


class DocxConversionTests(unittest.TestCase):
    def heading_package(self):
        styles = styles_xml(
            style("2", '<w:name w:val="heading 1"/>'),
            style("3", '<w:pPr><w:outlineLvl w:val="1"/></w:pPr>'),
            style("4", '<w:name w:val="标题 3"/>'),
        )
        body = "".join(
            [
                paragraph(run("员工名录"), style="2"),
                paragraph(run("张伟"), style="3"),
                paragraph(run("联系方式"), style="4"),
                paragraph(run("正文段落")),
            ]
        )
        return build_docx(document_xml(body), styles=styles)

    def test_chinese_word_style_ids_resolve_to_heading_levels(self):
        markdown = docx_tools.docx_to_markdown(self.heading_package())
        self.assertIn("# 员工名录", markdown)
        self.assertIn("## 张伟", markdown)
        self.assertIn("### 联系方式", markdown)
        self.assertNotIn("# 正文段落", markdown)

    def test_direct_outline_level_beats_the_style_id(self):
        styles = styles_xml(style("2", '<w:name w:val="heading 1"/>'))
        document = document_xml(
            paragraph(run("小节"), style="2", outline=2)
        )
        markdown = docx_tools.docx_to_markdown(
            build_docx(document, styles=styles)
        )
        self.assertEqual(markdown, "### 小节")

    def test_bold_italic_and_line_breaks_survive(self):
        document = document_xml(
            "<w:p>"
            + run("张伟", bold=True)
            + run("：后端")
            + run("在岗", italic=True)
            + run("兼岗", bold=True, italic=True)
            + '<w:r><w:br/><w:t>电话</w:t></w:r>'
            + "</w:p>"
        )
        markdown = docx_tools.docx_to_markdown(build_docx(document))
        self.assertIn("**张伟**", markdown)
        self.assertIn("*在岗*", markdown)
        self.assertIn(" / 电话", markdown)

    def test_list_marks_come_from_numbering_and_ilvl(self):
        document = document_xml(
            paragraph(run("工具"), num=1)
            + paragraph(run("子项"), num=1, ilvl=1)
            + paragraph(run("第一步"), num=2)
        )
        numbering = (
            "{}<w:numbering xmlns:w=\"{}\">"
            '<w:abstractNum w:abstractNumId="0">'
            '<w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl>'
            "</w:abstractNum>"
            '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
            '<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>'
            "</w:numbering>"
        ).format(XML_HEAD, W)
        # abstractNumId 1 未定义：按无序处理，但绝不能丢整行。
        markdown = docx_tools.docx_to_markdown(
            build_docx(document, numbering=numbering)
        )
        self.assertIn("- 工具", markdown)
        self.assertIn("  - 子项", markdown)
        self.assertIn("- 第一步", markdown)

    def test_ordered_lists_render_as_markdown_list_items(self):
        document = document_xml(
            paragraph(run("第一步"), num=1) + paragraph(run("第二步"), num=1)
        )
        markdown = docx_tools.docx_to_markdown(
            build_docx(document, numbering=numbered_xml("1", "decimal"))
        )
        self.assertEqual(
            [line for line in markdown.splitlines() if line.strip()],
            ["1. 第一步", "1. 第二步"],
        )

    def test_tables_become_github_markdown_with_escaped_pipes(self):
        document = document_xml(
            table([["姓名", "备注"], ["张伟", "a|b"], ["李娜"]])
        )
        markdown = docx_tools.docx_to_markdown(build_docx(document))
        lines = [line for line in markdown.splitlines() if line.startswith("|")]
        self.assertEqual(lines[0], "| 姓名 | 备注 |")
        self.assertEqual(lines[1], "| --- | --- |")
        self.assertEqual(lines[2], "| 张伟 | a\\|b |")
        self.assertEqual(lines[3], "| 李娜 |  |")

    def test_content_controls_are_descended(self):
        body = (
            '<w:sdt><w:sdtContent>'
            + paragraph(run("目录内小节"), style="2")
            + "</w:sdtContent></w:sdt>"
        )
        styles = styles_xml(style("2", '<w:name w:val="heading 1"/>'))
        markdown = docx_tools.docx_to_markdown(
            build_docx(document_xml(body), styles=styles)
        )
        self.assertEqual(markdown, "# 目录内小节")

    def test_runs_inside_a_drawing_are_not_double_counted(self):
        document = document_xml(
            "<w:p>"
            + run("图")
            + '<w:r><w:drawing><wp:inline xmlns:wp="x">'
            "<w:r><w:t>图题重复</w:t></w:r></wp:inline></w:drawing></w:r>"
            "</w:p>"
        )
        self.assertEqual(
            docx_tools.docx_to_markdown(build_docx(document)), "图"
        )

    def test_identical_bytes_produce_identical_markdown(self):
        package = self.heading_package()
        first = docx_tools.docx_to_markdown(package)
        for _ in range(3):
            self.assertEqual(docx_tools.docx_to_markdown(package), first)

    def test_a_non_zip_is_refused_with_a_readable_reason(self):
        with self.assertRaises(docx_tools.DocxFormatError) as caught:
            docx_tools.docx_to_markdown(b"PK\x03\x04 not really a package")
        self.assertIn("zip", str(caught.exception))

    def test_a_package_without_a_body_is_refused(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", XML_HEAD + "<Types/>")
        with self.assertRaises(docx_tools.DocxFormatError) as caught:
            docx_tools.docx_to_markdown(buffer.getvalue())
        self.assertIn("word/document.xml", str(caught.exception))

    def test_a_document_without_a_wbody_is_refused(self):
        with self.assertRaises(docx_tools.DocxFormatError) as caught:
            docx_tools.docx_to_markdown(
                build_docx(XML_HEAD + '<w:document xmlns:w="{}"/>'.format(W))
            )
        self.assertIn("body", str(caught.exception))

    def test_entity_declarations_are_refused_before_parsing(self):
        bomb = '<!DOCTYPE d [<!ENTITY x "aaaa">]>&x;'
        with self.assertRaises(docx_tools.DocxFormatError) as caught:
            docx_tools.docx_to_markdown(build_docx(document_xml(bomb)))
        self.assertIn("实体", str(caught.exception))

    def test_truncated_xml_is_refused_rather_than_half_read(self):
        with self.assertRaises(docx_tools.DocxFormatError) as caught:
            docx_tools.docx_to_markdown(
                build_docx('<?xml version="1.0"?><w:document xmlns:w="{}"><w:body>'.format(W))
            )
        self.assertIn("不完整", str(caught.exception))

    def test_a_document_with_only_empty_paragraphs_is_refused(self):
        with self.assertRaises(docx_tools.DocxFormatError) as caught:
            docx_tools.docx_to_markdown(build_docx(document_xml(paragraph(run("   ")))))
        self.assertIn("正文文本", str(caught.exception))


class ChunkingTests(unittest.TestCase):
    def test_plain_text_uses_overlapping_windows_within_the_size_cap(self):
        body = "".join("句{}。".format(index) for index in range(400))
        blocks = chunk_text(body)
        self.assertGreater(len(blocks), 1)
        self.assertTrue(all(len(item["text"]) <= doc_tools.CHUNK_CHARS for item in blocks))
        self.assertEqual(blocks[0]["heading"], "")

    def test_markdown_sections_carry_their_heading_path(self):
        text = "# 员工名录\n\n## 张伟\n\n负责后端与检索。\n\n## 李娜\n\n负责界面。\n"
        blocks = chunk_text(text, as_markdown=True)
        headings = [item["heading"] for item in blocks]
        self.assertIn("员工名录 > 张伟", headings)
        hit = [item for item in blocks if item["heading"] == "员工名录 > 张伟"][0]
        self.assertTrue(hit["text"].startswith("员工名录 > 张伟\n"))

    def test_two_or_more_headings_add_a_table_of_contents_block(self):
        blocks = chunk_text("# 甲\n\n内容一\n\n# 乙\n\n内容二\n", as_markdown=True)
        self.assertEqual(blocks[0]["heading"], TOC_HEADER)
        self.assertIn("甲", blocks[0]["text"])
        self.assertIn("乙", blocks[0]["text"])

    def test_a_single_heading_is_not_worth_a_toc_block(self):
        blocks = chunk_text("# 甲\n\n只有一节\n", as_markdown=True)
        self.assertEqual([item["heading"] for item in blocks], ["甲"])

    def test_a_heading_inside_a_code_fence_stays_content(self):
        text = "# 真实标题\n\n```python\n# 这不是标题\nx = 1\n```\n\n# 另一个标题\n\n正文\n"
        blocks = chunk_text(text, as_markdown=True)
        titles = [item["heading"] for item in blocks]
        self.assertNotIn("这不是标题", " ".join(titles))
        self.assertIn("真实标题", titles)
        joined = "\n".join(item["text"] for item in blocks)
        self.assertIn("# 这不是标题", joined)

    def test_chunk_count_is_capped(self):
        body = "甲" * (doc_tools.CHUNK_CHARS * doc_tools.MAX_CHUNKS)
        self.assertGreater(len(split_windows(body)), doc_tools.MAX_CHUNKS)
        self.assertEqual(len(chunk_text(body)), doc_tools.MAX_CHUNKS)

    def test_blank_content_yields_no_blocks(self):
        self.assertEqual(chunk_text("   \n\n  \n"), [])


class NameCleaningTests(unittest.TestCase):
    def test_paths_and_control_characters_never_reach_the_filesystem(self):
        self.assertEqual(clean_name("..\\..\\secret\\名录.md"), "名录.md")
        self.assertEqual(clean_name("a/b/doc.txt"), "doc.txt")
        self.assertEqual(clean_name("  报告\x00.docx  "), "报告.docx")
        self.assertEqual(len(clean_name("{}{}".format("x" * 300, ".md"))), 103)

    def test_an_empty_name_is_refused(self):
        for value in ("", "   ", "...", "/"):
            with self.assertRaises(DocumentError):
                clean_name(value)


class DocumentServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.store = CompanionStore(self.root / "companion.db")
        self.store.ensure_session("main", AgentMode.CHAT)
        self.service = DocumentService(self.store, self.root)

    def docs_dir(self):
        return self.root / "docs"

    def slot_dirs(self):
        return sorted(p.name for p in self.docs_dir().iterdir()) if self.docs_dir().is_dir() else []

    def test_ingest_lands_the_original_and_indexes_it(self):
        record = self.service.ingest("名录.md", "# 员工名录\n\n张伟负责后端检索。\n".encode("utf-8"))
        self.assertEqual(record["kind"], "md")
        self.assertEqual(record["converted"], False)
        self.assertGreaterEqual(record["indexed"], 1)
        stored = self.root / "docs" / Path(record["path"]).parent.name / "名录.md"
        self.assertTrue(stored.is_file())
        hits = self.service.search("张伟", session_id="main")
        self.assertEqual([item["name"] for item in hits], ["名录.md"])
        self.assertIn("张伟负责后端检索", hits[0]["text"])

    def test_docx_ingest_keeps_both_the_source_and_the_converted_markdown(self):
        styles = styles_xml(style("2", '<w:name w:val="heading 1"/>'))
        package = build_docx(
            document_xml(paragraph(run("产品说明"), style="2") + paragraph(run("屏幕 6.7 寸"))),
            styles=styles,
        )
        record = self.service.ingest("说明.docx", package)
        self.assertTrue(record["converted"])
        self.assertEqual(record["kind"], "docx")
        slot = self.docs_dir() / record["document_id"]
        self.assertTrue((slot / "说明.docx").is_file())
        self.assertIn("屏幕 6.7 寸", (slot / "converted.md").read_text(encoding="utf-8"))
        hits = self.service.search("产品说明", persona_id="")
        self.assertTrue(hits)
        self.assertIn("# ", (slot / "converted.md").read_text(encoding="utf-8"))

    def test_every_refusal_happens_before_the_first_write(self):
        payloads = [
            ("招标.pdf", b"%PDF-1.4 fake"),
            ("宏.docm", b"PK\x03\x04junk"),
            ("未知.xyz", b"hello"),
            ("空.txt", b""),
            ("非utf8.txt", b"\xff\xfe\x00bad"),
            ("空正文.txt", "   \n  \n".encode("utf-8")),
        ]
        messages = []
        for name, payload in payloads:
            with self.assertRaises(DocumentError) as caught:
                self.service.ingest(name, payload)
            messages.append(str(caught.exception))
        joined = " ".join(messages)
        self.assertIn("PDF", joined)
        self.assertIn("宏", joined)
        self.assertIn("UTF-8", joined)
        self.assertEqual(self.store.list_documents(), [])
        self.assertEqual(self.slot_dirs(), [])

    def test_pdf_refusal_tells_the_user_how_to_proceed(self):
        with self.assertRaises(DocumentError) as caught:
            self.service.ingest("手册.pdf", b"%PDF-1.4")
        self.assertIn(".md", str(caught.exception))

    def test_unsupported_suffix_names_the_supported_set(self):
        with self.assertRaises(DocumentError) as caught:
            self.service.ingest("表格.xlsx", b"PK\x03\x04junk")
        self.assertIn(".docx", str(caught.exception))

    def test_a_broken_docx_is_refused_without_leaving_a_slot(self):
        with self.assertRaises(DocumentError) as caught:
            self.service.ingest("坏包.docx", b"PK\x03\x04not-a-package")
        self.assertIn("zip", str(caught.exception))
        self.assertEqual(self.slot_dirs(), [])

    def test_oversized_single_files_are_refused(self):
        payload = ("字" * (doc_tools.MAX_DOC_BYTES // 3 + 10)).encode("utf-8")
        self.assertGreater(len(payload), doc_tools.MAX_DOC_BYTES)
        with self.assertRaises(DocumentError) as caught:
            self.service.ingest("大文件.txt", payload)
        self.assertIn("MiB", str(caught.exception))
        self.assertEqual(self.slot_dirs(), [])

    def test_the_library_total_is_capped(self):
        payload = "内容甲内容甲内容甲".encode("utf-8")
        with mock.patch.object(doc_tools, "MAX_LIBRARY_BYTES", 40):
            self.service.ingest("甲.txt", payload)
            with self.assertRaises(DocumentError) as caught:
                self.service.ingest("乙.txt", payload)
        self.assertIn("文档库总量", str(caught.exception))
        self.assertEqual(len(self.store.list_documents()), 1)

    def test_search_is_limited_and_carries_the_source_columns(self):
        body = "# 名录\n\n" + "\n\n".join(
            "## 员工{}\n\n负责模块{}。".format(index, index) for index in range(30)
        )
        self.service.ingest("多节.md", body.encode("utf-8"))
        hits = self.service.search("负责模块", limit=3)
        self.assertLessEqual(len(hits), 3)
        for hit in hits:
            self.assertEqual(
                set(hit),
                {
                    "chunk_id",
                    "document_id",
                    "name",
                    "kind",
                    "path",
                    "ordinal",
                    "heading",
                    "text",
                    "chars",
                    "bm25",
                },
            )
            self.assertTrue((self.root / hit["path"]).is_file())

    def test_a_search_with_a_session_id_logs_a_docs_recall(self):
        self.service.ingest("名录.txt", "张伟负责后端检索。\n".encode("utf-8"))
        self.service.search("张伟", session_id="main")
        rows = self.store.list_recall_logs("main", limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["scope"], "docs")
        self.assertEqual(rows[0]["source"], "fts")
        self.assertEqual(json.loads(rows[0]["fact_ids"])[0][:4], "dck_")

    def test_a_search_without_a_session_id_writes_no_recall_row(self):
        self.service.ingest("名录.txt", "张伟负责后端检索。\n".encode("utf-8"))
        self.service.search("张伟")
        self.assertEqual(self.store.list_recall_logs("main", limit=5), [])

    def test_empty_or_non_positive_queries_short_circuit(self):
        self.service.ingest("名录.txt", "张伟。".encode("utf-8"))
        self.assertEqual(self.service.search(""), [])
        self.assertEqual(self.service.search("张伟", limit=0), [])

    def test_documents_are_scoped_per_persona_with_shared_rows_visible(self):
        self.service.ingest("甲.txt", "弗拉德负责检索。".encode("utf-8"), persona_id="hjl")
        self.service.ingest("乙.txt", "兹维格负责界面。".encode("utf-8"), persona_id="other")
        self.service.ingest("丙.txt", "卡普兰负责测试。".encode("utf-8"))
        names = {row["name"] for row in self.store.list_documents(persona_id="hjl")}
        self.assertEqual(names, {"甲.txt", "丙.txt"})
        self.assertEqual(
            {row["name"] for row in self.store.list_documents()},
            {"甲.txt", "乙.txt", "丙.txt"},
        )
        self.assertEqual(
            [hit["name"] for hit in self.service.search("弗拉德", persona_id="hjl")],
            ["甲.txt"],
        )
        self.assertEqual(self.service.search("兹维格", persona_id="hjl"), [])
        self.assertEqual(
            [hit["name"] for hit in self.service.search("兹维格")], ["乙.txt"]
        )

    def test_delete_removes_rows_chunks_and_files(self):
        record = self.service.ingest("名录.txt", "张伟负责后端检索。\n".encode("utf-8"))
        result = self.service.delete(record["document_id"])
        self.assertTrue(result["removed"])
        self.assertGreaterEqual(result["chunks"], 1)
        self.assertGreaterEqual(result["files"], 1)
        self.assertEqual(self.store.list_documents(), [])
        self.assertFalse((self.docs_dir() / record["document_id"]).exists())
        self.assertEqual(self.service.search("张伟"), [])

    def test_deleting_an_unknown_document_is_idempotent(self):
        self.assertEqual(
            self.service.delete("doc_missing"),
            {"removed": False, "chunks": 0, "files": 0},
        )

    def test_summary_reports_size_and_fts_state(self):
        self.service.ingest("名录.txt", "张伟。".encode("utf-8"))
        summary = self.service.summary()
        self.assertEqual(summary["count"], 1)
        self.assertEqual(summary["bytes"], len("张伟。".encode("utf-8")))
        self.assertEqual(summary["limit_bytes"], doc_tools.MAX_LIBRARY_BYTES)
        self.assertTrue(summary["fts_enabled"])

    def test_original_path_stays_inside_the_library(self):
        record = self.service.ingest("名录.txt", "张伟。".encode("utf-8"))
        self.assertTrue(self.service.original_path(record["document_id"]).is_file())
        with self.store._connection() as connection:
            connection.execute(
                "UPDATE documents SET path = ? WHERE document_id = ?",
                ("../../secrets.md", record["document_id"]),
            )
        self.assertIsNone(self.service.original_path(record["document_id"]))

    def test_a_missing_fts_index_degrades_to_no_hits_without_raising(self):
        record = self.service.ingest("名录.txt", "张伟。".encode("utf-8"))
        with self.store._connection() as connection:
            connection.execute("DROP TABLE doc_fts")
        self.store.doc_fts_enabled = False
        self.assertEqual(self.service.search("张伟"), [])
        self.assertTrue(record["document_id"])


class StoreMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.path = self.root / "companion.db"

    def tables(self, store):
        with store._connection() as connection:
            return {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }

    def user_version(self, store):
        with store._connection() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def test_a_fresh_database_carries_the_document_library(self):
        store = CompanionStore(self.path)
        self.assertEqual(SCHEMA_VERSION, 17)
        self.assertEqual(self.user_version(store), SCHEMA_VERSION)
        self.assertTrue({"documents", "doc_chunks", "doc_fts"} <= self.tables(store))
        self.assertTrue(store.doc_fts_enabled)

    def test_v10_databases_gain_the_tables_and_scope_column(self):
        store = CompanionStore(self.path)
        store.ensure_session("main", AgentMode.CHAT)
        store.add_message("main", "user", "旧数据", AgentMode.CHAT)
        with store._connection() as connection:
            connection.executescript(
                "DROP TABLE doc_fts; DROP TABLE doc_chunks; DROP TABLE documents;"
            )
            connection.execute("PRAGMA user_version = 10")
        reopened = CompanionStore(self.path)
        self.assertEqual(self.user_version(reopened), SCHEMA_VERSION)
        self.assertTrue({"documents", "doc_chunks", "doc_fts"} <= self.tables(reopened))
        with reopened._connection() as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(recall_log)").fetchall()
            }
            rows = connection.execute(
                "SELECT scope, source FROM recall_log LIMIT 1"
            ).fetchall()
        self.assertIn("scope", columns)
        self.assertEqual(rows, [])
        self.assertEqual(len(reopened.list_messages("main", 10)), 1)

    def test_the_pre_migration_backup_covers_a_v10_database(self):
        store = CompanionStore(self.path)
        store.ensure_session("main", AgentMode.CHAT)
        store.add_message("main", "user", "旧数据", AgentMode.CHAT)
        connection = sqlite3.connect(str(self.path))
        try:
            connection.execute("DROP TABLE doc_fts")
            connection.execute("DROP TABLE doc_chunks")
            connection.execute("DROP TABLE documents")
            connection.execute("PRAGMA user_version = 10")
            connection.commit()
        finally:
            connection.close()
        store = CompanionStore(self.path)
        self.assertTrue(Path(store.migration_backup_path).is_file())

    def test_a_drifted_doc_index_is_rebuilt_on_launch(self):
        store = CompanionStore(self.path)
        meta = {
            "document_id": "doc_fixed",
            "name": "名录.txt",
            "kind": "txt",
            "path": "docs/doc_fixed/名录.txt",
            "bytes": 6,
            "sha256": "0" * 64,
            "chars": 6,
        }
        store.add_document(meta, [{"chunk_id": "dck_a", "text": "张伟"}, {"chunk_id": "dck_b", "text": "李娜"}])
        with store._connection() as connection:
            connection.execute("DELETE FROM doc_fts WHERE chunk_id = 'dck_a'")
        drifted = CompanionStore(self.path)
        with drifted._connection() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM doc_fts").fetchone()[0], 2
            )
        self.assertEqual(
            [hit["chunk_id"] for hit in drifted.search_documents_fts("张伟")],
            ["dck_a"],
        )

    def test_search_returns_none_when_the_index_is_unusable(self):
        store = CompanionStore(self.path)
        store.doc_fts_enabled = False
        self.assertIsNone(store.search_documents_fts("张伟"))

    def test_search_returns_empty_for_unmatchable_queries(self):
        store = CompanionStore(self.path)
        self.assertEqual(store.search_documents_fts("。。。"), [])

    def test_limit_bounds_are_clamped(self):
        store = CompanionStore(self.path)
        self.assertEqual(store.search_documents_fts("张伟", limit=0), [])
        self.assertEqual(store.search_documents_fts("张伟", limit=-5), [])

    def test_rebuild_doc_fts_restores_parity(self):
        store = CompanionStore(self.path)
        store.add_document(
            {
                "document_id": "doc_r",
                "name": "a.txt",
                "kind": "txt",
                "path": "docs/doc_r/a.txt",
                "bytes": 2,
                "sha256": "x",
                "chars": 2,
            },
            [{"chunk_id": "dck_r1", "text": "甲"}, {"chunk_id": "dck_r2", "text": "乙"}],
        )
        with store._connection() as connection:
            connection.execute("DELETE FROM doc_fts")
        self.assertTrue(store.rebuild_doc_fts())
        self.assertEqual(
            [hit["chunk_id"] for hit in store.search_documents_fts("乙")], ["dck_r2"]
        )

    def test_delete_document_reports_the_chunk_count(self):
        store = CompanionStore(self.path)
        store.add_document(
            {
                "document_id": "doc_d",
                "name": "a.txt",
                "kind": "txt",
                "path": "docs/doc_d/a.txt",
                "bytes": 2,
                "sha256": "x",
                "chars": 2,
            },
            [{"chunk_id": "dck_d1", "text": "甲"}],
        )
        self.assertEqual(store.delete_document("doc_d"), 1)
        self.assertEqual(store.delete_document("doc_d"), 0)
        with store._connection() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM doc_fts").fetchone()[0], 0
            )

    def test_recall_log_defaults_older_rows_to_memory_scope(self):
        store = CompanionStore(self.path)
        recall_id = store.log_recall("main", "hash", ["mem_1"], [0.5], 1.0, source="hybrid")
        rows = {row["recall_id"]: row for row in store.list_recall_logs("main")}
        self.assertEqual(rows[recall_id]["scope"], "memory")


class MaintenanceCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.addCleanup(self.temp.cleanup)
        self.store = CompanionStore(self.data / "companion.db")
        self.store.ensure_session("main", AgentMode.CHAT)
        self.service = DocumentService(self.store, self.data)
        self.record = self.service.ingest("名录.txt", "张伟负责后端检索。\n".encode("utf-8"))

    def mutate(self, sql):
        connection = sqlite3.connect(str(self.store.database_path))
        try:
            connection.execute(sql)
            connection.commit()
        finally:
            connection.close()

    def test_document_tables_are_exported_and_indexed(self):
        self.assertIn("documents", EXPORT_TABLES)
        self.assertIn("doc_chunks", EXPORT_TABLES)
        self.assertIn("idx_documents_persona", EXPECTED_INDEXES)
        self.assertIn("idx_doc_chunks_document", EXPECTED_INDEXES)

    def test_integrity_report_is_clean_with_a_document_library(self):
        report = integrity_report(self.store.database_path)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["counts"]["documents"], 1)
        self.assertEqual(report["counts"]["doc_chunks"], self.record["chunks"])
        self.assertTrue(report["doc_fts_present"])
        self.assertEqual(report["doc_fts_rows"], report["doc_chunk_rows"])

    def test_a_drifted_doc_index_fails_the_integrity_check(self):
        self.mutate(
            "DELETE FROM doc_fts WHERE chunk_id = ("
            "SELECT chunk_id FROM doc_chunks LIMIT 1)"
        )
        report = integrity_report(self.store.database_path)
        self.assertNotEqual(report["doc_fts_rows"], report["doc_chunk_rows"])
        self.assertFalse(report["ok"])

    def test_missing_document_indexes_are_reported(self):
        self.mutate("DROP INDEX idx_doc_chunks_document")
        report = integrity_report(self.store.database_path)
        self.assertIn("idx_doc_chunks_document", report["missing_indexes"])
        self.assertFalse(report["ok"])

    def test_backup_contains_the_document_tables(self):
        info = create_backup(self.data, self.store.database_path, reason="manual")
        connection = sqlite3.connect(info["path"])
        try:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            rows = connection.execute("SELECT name FROM documents").fetchall()
        finally:
            connection.close()
        self.assertTrue({"documents", "doc_chunks"} <= names)
        self.assertEqual([row[0] for row in rows], ["名录.txt"])

    def test_export_zip_carries_the_document_rows(self):
        result = export_data(self.data, self.store.database_path)
        with zipfile.ZipFile(result["path"]) as archive:
            payload = json.loads(archive.read("export.json").decode("utf-8"))
        self.assertEqual([row["name"] for row in payload["tables"]["documents"]], ["名录.txt"])
        self.assertEqual(
            len(payload["tables"]["doc_chunks"]), self.record["chunks"]
        )
        self.assertIn("documents", result["counts"])

    def test_clearing_memories_never_touches_the_document_library(self):
        self.store.add_memory(
            "main", "fact", "张伟的偏好", None, 0.8, 0.6, status="confirmed"
        )
        result = purge_memories(self.store)
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(len(self.store.list_documents()), 1)
        self.assertTrue(self.store.search_documents_fts("张伟"))
        self.assertTrue((self.data / "docs" / self.record["document_id"] / "名录.txt").is_file())

    def test_purging_a_session_leaves_the_library_alone(self):
        self.service.search("张伟", session_id="main")
        purge_session(self.store, "main", data_dir=self.data)
        self.assertEqual(len(self.store.list_documents()), 1)
        self.assertTrue((self.data / "docs" / self.record["document_id"]).is_dir())

    def test_purge_all_clears_document_rows_and_files(self):
        docs_root = self.data / "docs"
        stray = docs_root / "orphan-dir.bin"
        stray.write_bytes(b"x")
        result = purge_all(self.store, data_dir=self.data)
        self.assertEqual(result["counts"]["documents"], 1)
        self.assertEqual(result["counts"]["doc_chunks"], self.record["chunks"])
        self.assertEqual(result["counts"]["doc_fts"], self.record["chunks"])
        self.assertEqual(self.store.list_documents(), [])
        self.assertFalse((docs_root / self.record["document_id"]).exists())
        self.assertTrue(docs_root.is_dir())
        self.assertEqual(result["docs"]["deleted"], 2)
        self.assertFalse(stray.exists())
        with self.store._connection() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM doc_fts").fetchone()[0], 0
            )

    def test_purge_all_on_a_library_without_files_is_not_an_error(self):
        self.service.delete(self.record["document_id"])
        result = purge_all(self.store, data_dir=self.data)
        self.assertEqual(result["counts"]["documents"], 0)
        self.assertEqual(result["docs"], {"deleted": 0, "skipped": 0})


class WebDocumentApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        store = CompanionStore(self.root / "companion.db")
        self.runtime = CompanionRuntime(
            store=store,
            llm_client=OfflineLLMClient(),
            search_service=SearchService(StubSearch()),
            data_dir=self.root,
            session_id="main",
            offline=True,
        )
        self.service = create_application_service(self.runtime)
        self.adapter = WebAdapter(self.service, data_dir=self.root).start()
        self.addCleanup(self.adapter.stop)
        self.base = "http://" + self.adapter.address

    def request(self, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={
                "X-Companion-Token": self.adapter.token,
                "Content-Type": "application/json",
            },
            method="POST" if body is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8") or "{}")

    def upload(self, name, payload: bytes):
        return self.request(
            "/api/docs/import",
            {"filename": name, "data": base64.b64encode(payload).decode("ascii")},
        )

    def test_get_reports_an_empty_not_connected_library(self):
        status, payload = self.request("/api/docs")
        self.assertEqual(status, 200)
        self.assertEqual(payload["rows"], [])
        self.assertFalse(payload["connected"])
        self.assertEqual(payload["max_file_bytes"], doc_tools.MAX_DOC_BYTES)
        self.assertEqual(payload["max_library_bytes"], doc_tools.MAX_LIBRARY_BYTES)
        self.assertEqual(payload["summary"]["count"], 0)

    def test_import_returns_the_row_and_the_new_summary(self):
        status, payload = self.upload("名录.md", "# 员工名录\n\n张伟负责后端。\n".encode("utf-8"))
        self.assertEqual(status, 200)
        self.assertEqual(payload["document"]["name"], "名录.md")
        self.assertEqual(payload["document"]["kind"], "md")
        self.assertGreaterEqual(payload["document"]["chunks"], 1)
        self.assertFalse(payload["document"]["converted"])
        self.assertEqual(payload["summary"]["count"], 1)
        rows = self.request("/api/docs")[1]["rows"]
        self.assertEqual([row["name"] for row in rows], ["名录.md"])

    def test_a_data_url_payload_is_accepted(self):
        status, payload = self.request(
            "/api/docs/import",
            {
                "filename": "说明.txt",
                "data": "data:text/plain;base64,{}".format(
                    base64.b64encode("张伟在岗。".encode("utf-8")).decode("ascii")
                ),
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["document"]["name"], "说明.txt")

    def test_a_docx_upload_reports_conversion_and_indexes_text(self):
        styles = styles_xml(style("2", '<w:name w:val="heading 1"/>'))
        package = build_docx(
            document_xml(paragraph(run("手机产品说明"), style="2") + paragraph(run("电池 5000 毫安"))),
            styles=styles,
        )
        status, payload = self.upload("产品.docx", package)
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["document"]["converted"])
        hits = self.runtime.documents.search("电池", persona_id=self.runtime.persona_id)
        self.assertEqual([hit["name"] for hit in hits], ["产品.docx"])

    def test_refused_uploads_are_a_400_with_the_reason(self):
        status, payload = self.upload("手册.pdf", b"%PDF-1.4")
        self.assertEqual(status, 400)
        self.assertIn("PDF", payload["error"])
        self.assertEqual(self.request("/api/docs")[1]["rows"], [])

    def test_missing_or_malformed_data_is_a_400(self):
        self.assertEqual(self.request("/api/docs/import", {"filename": "a.txt"})[0], 400)
        self.assertEqual(
            self.request("/api/docs/import", {"filename": "a.txt", "data": "!!!!"})[0],
            400,
        )

    def test_an_over_limit_upload_is_a_400_before_decoding(self):
        status, payload = self.request(
            "/api/docs/import",
            {"filename": "大.txt", "data": "A" * (doc_tools.MAX_DOC_BYTES * 2 + 10)},
        )
        self.assertEqual(status, 400)
        self.assertIn("2 MiB", payload["error"])

    def test_delete_reports_what_it_removed(self):
        document_id = self.upload("名录.txt", "张伟。".encode("utf-8"))[1]["document"]["document_id"]
        status, payload = self.request("/api/docs/delete", {"document_id": document_id})
        self.assertEqual(status, 200)
        self.assertTrue(payload["removed"])
        self.assertGreaterEqual(payload["files"], 1)
        self.assertEqual(payload["summary"]["count"], 0)
        self.assertFalse((self.root / "docs" / document_id).exists())
        self.assertEqual(
            self.request("/api/docs/delete", {"document_id": ""})[0], 400
        )

    def test_the_transport_cap_stays_above_the_avatar_refusal(self):
        # 头像处理器会自己拒掉 16 MiB 的 base64 并给出"不能超过 8 MiB"；传输层
        # 若先断连，用户看到的就只剩网络错误，所以上限必须留在它之上。
        self.assertGreater(MAX_REQUEST_BYTES, MAX_AVATAR_BYTES * 2 + 64)

    def test_an_oversized_body_is_refused_without_being_read(self):
        declared = MAX_REQUEST_BYTES + 1
        request = (
            "POST /api/docs/import HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "X-Companion-Token: {}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: {}\r\n"
            "\r\n"
        ).format(self.adapter.token, declared)
        with socket.create_connection(("127.0.0.1", self.adapter.port), timeout=10) as sock:
            sock.sendall(request.encode("ascii"))
            sock.settimeout(10)
            chunks = []
            while True:
                try:
                    piece = sock.recv(4096)
                except socket.timeout:
                    break
                if not piece:
                    break
                chunks.append(piece)
                if len(b"".join(chunks)) > 4096:
                    break
        head = b"".join(chunks)
        self.assertIn(b"413", head.split(b"\r\n", 1)[0])
        self.assertIn("拒绝".encode("utf-8"), head)

    def test_a_negative_content_length_is_refused(self):
        with socket.create_connection(("127.0.0.1", self.adapter.port), timeout=10) as sock:
            sock.sendall(
                (
                    "POST /api/docs/import HTTP/1.1\r\n"
                    "Host: 127.0.0.1\r\n"
                    "X-Companion-Token: {}\r\n"
                    "Content-Length: -5\r\n"
                    "\r\n"
                ).format(self.adapter.token).encode("ascii")
            )
            sock.settimeout(10)
            data = b"".join(iter(lambda: sock.recv(4096), b""))
        self.assertIn(b"413", data.split(b"\r\n", 1)[0])


if __name__ == "__main__":
    unittest.main()
