"""Read-only projections that adapters render without touching runtime internals."""

from __future__ import annotations

from typing import List, Optional, Tuple

from ..tokens import chars_to_tokens, tokens_to_chars
from ..work import STATE_LABELS
from .contracts import (
    ContextSegment,
    ContextView,
    ProjectionSection,
    RunStage,
    SourceView,
)


def clip(text, limit=120) -> str:
    flat = " ".join(str(text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


def stamp(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.replace("T", " ")[:16]


def stage_for_event(kind, payload=None, saw_error=False) -> str:
    """Map one runtime event to a run stage; the adapter never re-derives it."""
    payload = payload or {}
    if kind == "started":
        return RunStage.THINKING.value
    if kind == "delta":
        return RunStage.ANSWERING.value
    if kind == "reset":
        return RunStage.THINKING.value
    if kind == "tool":
        return RunStage.TOOL.value
    if kind == "error":
        return RunStage.FAILED.value
    if kind == "done":
        if saw_error:
            return RunStage.FAILED.value
        result = payload.get("result") or {}
        if result.get("cancelled"):
            return RunStage.CANCELLED.value
        return RunStage.COMPLETED.value
    return ""


_UNSET = object()


_SEGMENT_LABELS = {
    "system": "系统提示",
    "recall": "记忆召回",
    "docs": "文档检索",
    "history": "历史对话",
    "reply": "回复预留",
    "free": "空闲",
}


def _segment(key: str, chars: int) -> ContextSegment:
    rounded = max(0, int(chars or 0))
    return ContextSegment(
        key=key,
        label=_SEGMENT_LABELS[key],
        tokens=chars_to_tokens(rounded),
        chars=rounded,
    )


def _reply_reserve(runtime) -> int:
    """The ``max_tokens`` the model may answer with, in the char budget."""
    settings = getattr(runtime, "settings", None) or {}
    return tokens_to_chars(int(settings.get("max_tokens") or 0))


def build_context_view(runtime) -> Optional[ContextView]:
    """One derivation of context usage; the inspector text and the numbers share it."""
    build = getattr(runtime, "last_context", None)
    if build is None:
        return None
    # The bands are the last prompt as it was actually assembled, but the width
    # is the window in force *now* — saving a new context window in settings has
    # to move the free band immediately instead of after the next turn.
    limit = int(getattr(runtime, "max_context_chars", 0) or 0) or int(
        getattr(build, "budget_chars", 0) or 0
    )
    if limit <= 0:
        return None
    used = int(getattr(build, "used_chars", 0) or 0)
    history = min(int(getattr(build, "history_chars", 0) or 0), used)
    layers = getattr(build, "layers", None)
    recall = int(layers.get("memory") or 0) if isinstance(layers, dict) else 0
    # The document band is a named layer too: leaving it unnamed would draw the
    # library's own bytes into the system band and understate retrieval.
    docs = min(
        int(layers.get("docs") or 0) if isinstance(layers, dict) else 0,
        max(0, used - history - recall),
    )
    # Whatever the layer table did not itemise (join separators, dropped turns)
    # stays in the system band so the occupied bands add up to ``used``.
    system = max(0, used - history - recall - docs)
    segments = [
        segment
        for segment in (
            _segment("system", system),
            _segment("recall", recall),
            _segment("docs", docs),
            _segment("history", history),
        )
        if segment.chars > 0
    ]
    reserve = min(_reply_reserve(runtime), max(0, limit - used))
    if reserve > 0:
        segments.append(_segment("reply", reserve))
    segments.append(_segment("free", limit - used - reserve))
    return ContextView(
        context_used=used,
        context_limit=limit,
        compressed=bool(getattr(build, "compressed", False)),
        segments=tuple(segments),
    )


SOURCE_TITLE_CLIP = 140
SOURCE_SNIPPET_CLIP = 240


def build_sources(runtime) -> Tuple[SourceView, ...]:
    """One 来源 channel for two kinds of provenance: the local library first.

    The library is stable material the user uploaded; web hits only exist after
    a search ran, so they follow. Adapters render this tuple instead of reading
    the runtime attributes.
    """
    docs = []
    for item in getattr(runtime, "last_doc_sources", None) or ():
        if not isinstance(item, dict):
            continue
        docs.append(
            SourceView(
                source_kind="document",
                title=clip(item.get("title"), SOURCE_TITLE_CLIP),
                snippet=clip(item.get("snippet"), SOURCE_SNIPPET_CLIP),
                heading=clip(item.get("heading"), 80),
                path=str(item.get("path") or ""),
                document_id=str(item.get("document_id") or ""),
                chunk_id=str(item.get("chunk_id") or ""),
                ordinal=int(item.get("ordinal") or 0),
            )
        )
    web = []
    for item in getattr(runtime, "last_tool_sources", None) or ():
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("url") or "")
        if not (title or item.get("url")):
            continue
        web.append(
            SourceView(
                source_kind="web",
                title=clip(title, SOURCE_TITLE_CLIP),
                url=str(item.get("url") or ""),
                snippet=clip(item.get("snippet"), SOURCE_SNIPPET_CLIP),
            )
        )
    return tuple(docs + web)


def build_inspector_sections(
    runtime,
    approvals=_UNSET,
) -> Tuple[ProjectionSection, ...]:
    sections: List[ProjectionSection] = []

    status = _call(runtime, "desktop_status")
    if isinstance(status, dict):
        lines = [str(status.get("label") or "待机")]
        detail = clip(status.get("detail"), 160)
        if detail:
            lines.append(detail)
        sections.append(ProjectionSection("当前状态", tuple(lines)))

    if approvals is _UNSET:
        approvals = _call(runtime, "pending_proposals")
    if approvals is None:
        pending_lines = ["暂时读不到"]
    elif not approvals:
        pending_lines = ["没有等待确认的写入"]
    else:
        pending_lines = ["{} 条".format(len(approvals))] + [
            clip(_plan_text(item), 140) for item in approvals[:2]
        ]
    sections.append(ProjectionSection("待确认提案", tuple(pending_lines)))

    work = _call(runtime, "work_status")
    if isinstance(work, dict):
        lines = [str(work.get("state_label") or "空闲")]
        if work.get("forward_label"):
            lines.append("转发：{}".format(work["forward_label"]))
        tasks = _call(runtime, "work_tasks", 3) or []
        lines.extend(
            "{}｜{}".format(
                clip(task.card.title, 40),
                STATE_LABELS.get(task.state, task.state),
            )
            for task in tasks[:3]
        )
        sections.append(ProjectionSection("工作任务", tuple(lines)))

    receipts = _call(runtime, "latest_receipts", 3)
    if receipts is None:
        receipt_lines = ["暂时读不到"]
    elif not receipts:
        receipt_lines = ["还没有写入回执"]
    else:
        receipt_lines = [
            clip("{}：{}".format(item.status, item.summary), 120)
            for item in receipts
        ]
    sections.append(ProjectionSection("最近回执", tuple(receipt_lines)))

    workspace = _call(runtime, "workspace")
    file_lines = []
    if workspace:
        file_lines.append("工作区：{}".format(clip(workspace, 90)))
    for item in approvals or ():
        file_lines.append(
            "待写入：{}".format(clip(getattr(item, "target_summary", ""), 90))
        )
    for item in receipts or ():
        result = getattr(item, "result", None) or {}
        path = (
            result.get("relative_path")
            or result.get("target_path")
            or getattr(item, "summary", "")
        )
        file_lines.append(
            "{}｜{}".format(
                "已写入" if getattr(item, "status", "") == "completed" else "未写入",
                clip(path, 90),
            )
        )
    if file_lines:
        sections.append(ProjectionSection("文件", tuple(file_lines[:6])))

    sources = build_sources(runtime)
    if sources:
        source_lines = []
        for index, item in enumerate(sources[:6], start=1):
            source_lines.append("{}．{}".format(index, clip(item.title, 50)))
            tail = item.url or item.path
            if tail:
                source_lines.append("   {}".format(clip(tail, 100)))
        sections.append(ProjectionSection("来源", tuple(source_lines)))

    proactive = _call(runtime, "proactive_status")
    if isinstance(proactive, dict):
        lines = [
            "开关：{}".format("开启" if proactive.get("enabled") else "关闭"),
            "活动：{}".format(proactive.get("activity_label") or "未知"),
            "今日：{} / {}".format(
                proactive.get("daily_count", 0),
                proactive.get("daily_limit", 0),
            ),
            "未回复：{} / {}".format(
                proactive.get("unanswered", 0),
                proactive.get("max_unanswered", 0),
            ),
        ]
        next_due = proactive.get("next_due")
        if next_due:
            lines.append(
                "提醒：{} {}".format(
                    clip(next_due.get("display"), 30),
                    clip(next_due.get("content"), 40),
                )
            )
        last_event = proactive.get("last_event")
        if last_event:
            lines.append(
                "上次主动：{} {}".format(
                    stamp(last_event.get("created_at")),
                    clip(last_event.get("status_label"), 30),
                )
            )
        if proactive.get("unread"):
            lines.append("未读主动：{} 条".format(proactive["unread"]))
        sections.append(ProjectionSection("主动陪伴", tuple(lines)))

    skills = _call(runtime, "skills_status")
    if isinstance(skills, dict):
        available = skills.get("available") or []
        lines = ["可用技能：{} 个".format(len(available))]
        lines.extend(
            "· {}".format(clip(item.get("name") or item.get("id"), 40))
            for item in available[:4]
        )
        if skills.get("issues"):
            lines.append("校验问题：{} 个".format(len(skills["issues"])))
        sections.append(ProjectionSection("技能", tuple(lines)))

    context = build_context_view(runtime)
    if context is not None:
        sections.append(
            ProjectionSection(
                "上下文",
                (
                    "用量：{} / {} 字符".format(
                        context.context_used,
                        context.context_limit,
                    ),
                    "压缩：{}".format("已压缩" if context.compressed else "未压缩"),
                ),
            )
        )

    if not sections:
        sections.append(ProjectionSection("详情", ("暂无可展示的执行状态",)))
    return tuple(sections)


def _plan_text(item) -> str:
    return str(
        getattr(item, "plan", "") or getattr(item, "user_visible_plan", "")
    )


def _call(runtime, name, *args):
    function = getattr(runtime, name, None)
    if function is None:
        return None
    try:
        return function(*args)
    except Exception:
        return None
