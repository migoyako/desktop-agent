"""Dependency-free web search providers for the first companion version."""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import re
from typing import List
from urllib.parse import quote_plus, urlparse, parse_qs
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from .models import SearchResult


class SearchError(RuntimeError):
    pass


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def text(self):
        return " ".join(" ".join(self.parts).split())


def strip_html(value: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(value or "")
        return parser.text()
    except Exception:
        return re.sub(r"<[^>]+>", " ", unescape(value or "")).strip()


def _safe_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        return ""
    return value


def parse_bing_rss(payload: bytes, limit: int = 5) -> List[SearchResult]:
    root = ElementTree.fromstring(payload)
    results = []
    for item in root.findall(".//item"):
        title = strip_html(item.findtext("title") or "")
        link = _safe_url((item.findtext("link") or "").strip())
        description = strip_html(item.findtext("description") or "")
        if title and link:
            results.append(
                SearchResult(title=title, url=link, snippet=description)
            )
        if len(results) >= limit:
            break
    return results


class _DuckDuckGoParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self.current = None
        self.capture = ""
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        classes = values.get("class", "").split()
        if tag == "a" and "result__a" in classes:
            self.current = SearchResult(
                title="",
                url=_safe_url(values.get("href", "")),
                snippet="",
            )
            self.capture = "title"
            self.depth = 1
        elif (
            self.current
            and tag == "a"
            and "result__snippet" in classes
            and not self.current.snippet
        ):
            self.capture = "snippet"
            self.depth = 1
        elif self.capture:
            self.depth += 1

    def handle_data(self, data):
        if self.current and self.capture:
            self.current.title += data if self.capture == "title" else ""
            self.current.snippet += data if self.capture == "snippet" else ""

    def handle_endtag(self, tag):
        if not self.capture:
            return
        self.depth -= 1
        if self.depth <= 0:
            if self.capture == "title":
                self.current.title = strip_html(self.current.title)
                if self.current.title and self.current.url:
                    self.results.append(self.current)
            else:
                self.current.snippet = strip_html(self.current.snippet)
            self.capture = ""


def parse_duckduckgo_html(payload: bytes, limit: int = 5) -> List[SearchResult]:
    parser = _DuckDuckGoParser()
    parser.feed(payload.decode("utf-8", errors="replace"))
    results = []
    for item in parser.results:
        url = item.url
        if "duckduckgo.com/l/" in url:
            query = parse_qs(urlparse(url).query)
            url = (query.get("uddg") or [""])[0]
        url = _safe_url(url)
        if url:
            results.append(
                SearchResult(
                    title=item.title,
                    url=url,
                    snippet=item.snippet,
                )
            )
        if len(results) >= limit:
            break
    return results


class WebSearchService:
    def __init__(self, timeout=12):
        self.timeout = float(timeout)

    def _fetch(self, url):
        request = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 CompanionAgent/0.1"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            },
        )
        with urlopen(request, timeout=self.timeout) as response:
            return response.read()

    def search(self, query: str, limit: int = 5) -> List[SearchResult]:
        query = (query or "").strip()
        if not query:
            raise SearchError("搜索关键词不能为空。")
        errors = []
        urls = [
            (
                "Bing RSS",
                "https://www.bing.com/search?q={}&format=rss&count={}".format(
                    quote_plus(query),
                    max(1, min(limit, 10)),
                ),
            ),
            (
                "DuckDuckGo",
                "https://html.duckduckgo.com/html/?q={}".format(
                    quote_plus(query)
                ),
            ),
        ]
        for name, url in urls:
            try:
                payload = self._fetch(url)
                if name == "Bing RSS":
                    results = parse_bing_rss(payload, limit)
                else:
                    results = parse_duckduckgo_html(payload, limit)
                if results:
                    return results
            except Exception as error:
                errors.append("{}: {}".format(name, error))
        raise SearchError("网页搜索失败。{}".format("；".join(errors)))


@dataclass
class SearchService:
    backend: object = None

    def __post_init__(self):
        if self.backend is None:
            self.backend = WebSearchService()

    def search(self, query: str, limit: int = 5):
        return self.backend.search(query, limit)


def compose_search_note(query, results, generated_at=None) -> str:
    from datetime import datetime

    stamp = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# 搜索笔记：{}".format(query),
        "",
        "- 生成时间：{}".format(stamp),
        "- 来源数量：{}".format(len(results)),
        "",
        "## 搜索结果",
        "",
    ]
    for index, result in enumerate(results, start=1):
        lines.append("### {}. {}".format(index, result.title))
        lines.append("")
        lines.append("- 来源：{}".format(result.url))
        if result.snippet:
            lines.append("- 摘要：{}".format(result.snippet))
        lines.append("")
    lines.extend(
        [
            "## 使用说明",
            "",
            "以上内容来自公开搜索结果，链接和摘要请在使用前自行核验。",
            "",
        ]
    )
    return "\n".join(lines)


def compose_search_answer(query, results) -> str:
    lines = [
        "我搜索了“{}”，找到了 {} 条结果：".format(query, len(results)),
        "",
    ]
    for index, result in enumerate(results, start=1):
        lines.append("{}. {}".format(index, result.title))
        lines.append("   {}".format(result.url))
        if result.snippet:
            lines.append("   {}".format(result.snippet[:180]))
    return "\n".join(lines).strip()
