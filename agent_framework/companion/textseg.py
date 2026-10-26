"""Deterministic text segmentation for FTS5 indexing and matching.

中文没有空格分隔，SQLite FTS5 的 unicode61 分词器会把连续汉字当成一个
词元，导致召回质量差。这里统一做“2/3-gram 预分词”：

- 索引侧：``fts_segment`` 把内容切成空格分隔的词元串写入 FTS5；
- 查询侧：``fts_match_query`` 用同一套切分生成 OR 组合的 MATCH 表达式。

两侧共用同一规则，保证“能召回的都能解释”；BM25 只负责候选检索，
最终排序仍由 memory.recall_score 的单一衰减公式完成（决策 D2）。
"""

from __future__ import annotations

import re
from typing import List


CHINESE_RUN = re.compile(r"[\u4e00-\u9fff]+")
TOKENS = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+")


def segment_tokens(text: str) -> List[str]:
    """把文本切成有序去重的索引词元（中文 2/3-gram + 英数整词）。"""
    values: List[str] = []
    seen = set()
    lowered = (text or "").casefold()
    for part in TOKENS.findall(lowered):
        if CHINESE_RUN.fullmatch(part):
            grams = _chinese_ngrams(part)
        else:
            grams = [part]
        for gram in grams:
            if gram not in seen:
                seen.add(gram)
                values.append(gram)
    return values


def _chinese_ngrams(run: str) -> List[str]:
    if len(run) == 1:
        return [run]
    grams = [run[index : index + 2] for index in range(len(run) - 1)]
    grams.extend(run[index : index + 3] for index in range(len(run) - 2))
    return grams


def fts_segment(text: str) -> str:
    """生成写入 FTS5 的预分词文本。"""
    return " ".join(segment_tokens(text))


def fts_match_query(text: str) -> str:
    """生成 FTS5 MATCH 表达式；没有可用词元时返回空串。"""
    tokens = segment_tokens(text)
    if not tokens:
        return ""
    return " OR ".join(
        '"{}"'.format(token.replace('"', "")) for token in tokens if token
    )
