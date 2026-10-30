"""Safe Markdown subset shared by the Qt and Web chat bubbles."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from agent_framework.companion.ui import markdown

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "markdown_samples.json"
MARKDOWN_JS = ROOT / "agent_framework" / "companion" / "web" / "assets" / "markdown.js"

NODE_SCRIPT = (
    "const fs=require('fs');"
    "const md=require(process.argv[1]);"
    "const s=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));"
    "process.stdout.write(JSON.stringify(s.samples.map((x)=>md.render(x))));"
)


class SafeUrlTest(unittest.TestCase):
    def test_only_http_and_https_survive(self):
        self.assertEqual(
            markdown.safe_url("https://example.com/a?b=1"),
            "https://example.com/a?b=1",
        )
        self.assertEqual(markdown.safe_url("http://example.com"), "http://example.com")
        for unsafe in (
            "javascript:alert(1)",
            "data:text/html,<b>x</b>",
            "file:///C:/Windows",
            "https://",
            "example.com",
            "",
            None,
        ):
            self.assertEqual(markdown.safe_url(unsafe), "")


class RenderTest(unittest.TestCase):
    def test_raw_html_never_reaches_the_rich_text_layer(self):
        rendered = markdown.render(
            "<img src=x onerror=alert(1)>\n<script>alert(2)</script>"
        )
        self.assertNotIn("<img", rendered)
        self.assertNotIn("<script", rendered)
        self.assertIn("&lt;img", rendered)
        self.assertIn("&lt;script", rendered)

    def test_inline_code_bold_and_strike_are_the_whole_inline_vocabulary(self):
        rendered = markdown.render("用 `print(1)` 验证 **重点** 与 ~~旧结论~~")
        self.assertIn("<code>print(1)</code>", rendered)
        self.assertIn("<b>重点</b>", rendered)
        self.assertIn("<s>旧结论</s>", rendered)
        self.assertNotIn("<b>print", rendered)
        self.assertNotIn("<em>", rendered)

    def test_code_spans_win_over_bold_and_links(self):
        rendered = markdown.render("`**不粗**` 与 `[链接](https://example.com)`")
        self.assertIn("<code>**不粗**</code>", rendered)
        self.assertIn("<code>[链接](https://example.com)</code>", rendered)
        self.assertNotIn("<a ", rendered)

    def test_safe_links_become_anchors_and_unsafe_links_stay_plain(self):
        rendered = markdown.render(
            "[文档](https://example.com/doc) 和 [危险](javascript:alert(1))"
        )
        self.assertIn('<a href="https://example.com/doc">文档</a>', rendered)
        self.assertEqual(rendered.count("<a "), 1)
        self.assertIn("危险 (javascript:alert(1))", rendered)

    def test_quotes_in_links_are_escaped(self):
        rendered = markdown.render('[x](https://example.com/"onmouseover=alert(1))')
        self.assertNotIn('"onmouseover', rendered)
        self.assertIn("&quot;", rendered)

    def test_bare_urls_are_linked_without_trailing_punctuation(self):
        rendered = markdown.render("看 https://example.com/x。")
        self.assertIn('<a href="https://example.com/x">https://example.com/x</a>', rendered)
        self.assertIn("</a>。", rendered)
        self.assertNotIn("example.com/x。", rendered)

    def test_fenced_code_keeps_the_language_label_and_tolerates_streaming(self):
        done = markdown.render("```python\nprint('<b>')\n```")
        self.assertIn('<div class="md-lang">python</div>', done)
        self.assertIn("<pre", done)
        self.assertIn("&lt;b&gt;", done)
        self.assertNotIn("<b>", done)

        streaming = markdown.render("```python\nprint(1)")
        self.assertIn('<div class="md-lang">python</div>', streaming)
        self.assertIn("<pre", streaming)
        self.assertIn("print(1)", streaming)

    def test_heading_levels_keep_their_class(self):
        rendered = markdown.render("# 一\n## 二\n### 三\n#### 四")
        self.assertIn('<div class="md-h md-h1"><b>一</b></div>', rendered)
        self.assertIn('<div class="md-h md-h2"><b>二</b></div>', rendered)
        self.assertIn('<div class="md-h md-h3"><b>三</b></div>', rendered)
        self.assertIn("<div>#### 四</div>", rendered)

    def test_lists_nest_inside_the_open_item(self):
        rendered = markdown.render("- 一级\n  - 二级\n    1. 三级\n- 再一级")
        self.assertIn("<ul><li>一级<ul><li>二级<ol><li>三级</li></ol></li></ul></li>", rendered)
        self.assertTrue(rendered.endswith("<li>再一级</li></ul>"))

    def test_quotes_merge_and_render_as_one_block(self):
        rendered = markdown.render("> 第一行\n> 第二行\n\n正文")
        self.assertIn(
            '<blockquote class="md-quote">第一行<br/>第二行</blockquote>', rendered
        )
        self.assertIn("<div>正文</div>", rendered)

    def test_tables_carry_alignments_and_pad_short_rows(self):
        rendered = markdown.render(
            "| 项目 | 状态 | 备注 |\n|:---|---:|:---:|\n| 周报 | 草稿 | 等确认 |\n| 待办 | 完成 |"
        )
        self.assertIn('<table class="md-table" border="1" cellspacing="0" cellpadding="4">', rendered)
        self.assertIn('<th align="left">项目</th>', rendered)
        self.assertIn('<th align="right">状态</th>', rendered)
        self.assertIn('<th align="center">备注</th>', rendered)
        self.assertIn('<td align="center"></td>', rendered)

    def test_text_that_only_looks_tabular_stays_a_paragraph(self):
        rendered = markdown.render("| 不是表格 | 两列 |\n| 缺少分隔行 |")
        self.assertNotIn("<table", rendered)
        self.assertIn("<div>", rendered)

    def test_long_reply_mixes_table_code_list_and_quote(self):
        rendered = markdown.render(
            "## 总结\n\n| 事项 | 截止 |\n|:--|--:|\n| 周报 | 周五 |\n\n"
            "1. 生成初稿\n2. 你确认\n\n```python\nprint(1)\n```\n\n> 待确认后写入。"
        )
        for fragment in ("md-h md-h2", "md-table", "md-lang", "md-code", "<ol>", "md-quote"):
            self.assertIn(fragment, rendered)

    def test_blank_input_still_returns_a_block(self):
        self.assertEqual(markdown.render(""), "<div></div>")
        self.assertEqual(markdown.render(None), "<div></div>")


class QtPresentationTest(unittest.TestCase):
    def test_style_block_scales_with_the_font_scale(self):
        base = markdown.qt_style(100)
        large = markdown.qt_style(150)
        huge = markdown.qt_style(200)
        self.assertIn("font-size: 16px", base)
        self.assertIn("font-size: 24px", large)
        self.assertIn("font-size: 32px", huge)
        for css in (base, large, huge):
            for selector in (".md-h1", "pre.md-code", "blockquote.md-quote", "th"):
                self.assertIn(selector, css)

    def test_document_prefixes_the_renderer_output(self):
        rendered = markdown.document("**重点**", 150)
        self.assertTrue(rendered.startswith("<style>"))
        self.assertIn("<b>重点</b>", rendered)


class WebParityTest(unittest.TestCase):
    """Same sample set, same bytes: the JS twin must match the Python renderer."""

    def test_renderers_agree_on_every_fixture(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available")
        result = subprocess.run(
            [node, "-e", NODE_SCRIPT, str(MARKDOWN_JS), str(FIXTURES)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        samples = json.loads(FIXTURES.read_text(encoding="utf-8"))["samples"]
        rendered = json.loads(result.stdout)
        self.assertEqual(len(rendered), len(samples))
        for sample, web in zip(samples, rendered):
            self.assertEqual(markdown.render(sample), web, "sample: {!r}".format(sample))


if __name__ == "__main__":
    unittest.main()
