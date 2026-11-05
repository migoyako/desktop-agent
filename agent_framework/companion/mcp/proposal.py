"""The confirmation proposal for one remote call (A8, K3).

A remote call is the one side effect we cannot take back: nothing on our disk
records what the other machine did. So the proposal carries the endpoint it was
approved against — same transport, same URL, same command — and confirming
later, after the user has edited the server, is refused instead of silently
calling a different machine with a token they meant for another one.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from ..file_actions import PROPOSAL_TTL_HOURS
from ..models import (
    ActionProposal,
    AgentMode,
    EXECUTOR_MCP,
    RiskLevel,
    confirmation_digest,
    new_id,
)

MAX_PLAN_ARGUMENT_CHARS = 1200

REMOTE_WARNING = (
    "远端服务会真的收到这次调用；本机没有备份，执行后无法撤销。"
)


def server_identity(spec) -> Dict[str, Any]:
    """The part of a server the confirmation is bound to."""
    return {
        "id": str(getattr(spec, "server_id", "") or ""),
        "label": str(getattr(spec, "display", "") or ""),
        "transport": str(getattr(spec, "transport", "") or ""),
        "url": str(getattr(spec, "url", "") or ""),
        "command": _command_text(getattr(spec, "command", "")),
        "cwd": str(getattr(spec, "cwd", "") or ""),
    }


def arguments_text(arguments: Dict[str, Any]) -> str:
    try:
        text = json.dumps(arguments or {}, ensure_ascii=False, indent=None)
    except (TypeError, ValueError):
        text = "（参数无法序列化成 JSON。）"
    text = " ".join(text.split())
    if len(text) > MAX_PLAN_ARGUMENT_CHARS:
        return text[:MAX_PLAN_ARGUMENT_CHARS] + "…（已截断）"
    return text


def build_call_proposal(
    entry: Dict[str, Any],
    spec,
    arguments: Dict[str, Any],
    session_id: str,
    mode: AgentMode = AgentMode.WORK,
    source_message_id: Optional[int] = None,
    ttl_hours: float = PROPOSAL_TTL_HOURS,
) -> ActionProposal:
    """Describe one ``tools/call`` as a proposal; touches no transport."""
    tool = str(entry.get("name") or "")
    remote = str(entry.get("remote_name") or "")
    identity = server_identity(spec)
    checked = dict(arguments or {})
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=float(ttl_hours))
    ).isoformat()
    plan = "\n".join(
        [
            "向外部 MCP 服务器发起一次调用。",
            "服务器：{}（{}）".format(
                identity["label"] or identity["id"], identity["transport"]
            ),
            "工具：{}".format(remote or tool),
            "参数：{}".format(arguments_text(checked)),
            "撤销：{}".format(REMOTE_WARNING),
        ]
    )
    return ActionProposal(
        action_id=new_id("act"),
        session_id=str(session_id),
        mode=AgentMode(mode),
        intent="调用远程工具 {}".format(remote or tool),
        tool_name=tool,
        raw_arguments=dict(checked),
        normalized_arguments=dict(checked),
        expected_state={
            "server": identity,
            "remote_tool": remote,
            "expires_at": expires_at,
            "source_message_id": source_message_id,
        },
        risk=RiskLevel.EXTERNAL,
        target_summary=tool,
        reversible=False,
        user_visible_plan=plan,
        confirmation_digest=confirmation_digest(
            tool,
            checked,
            RiskLevel.EXTERNAL.value,
            tool,
        ),
        idempotency_key=new_id("idem"),
        executor=EXECUTOR_MCP,
    )


def preview_value(proposal) -> Dict[str, Any]:
    """What the approval card shows for a queued remote call: no diff, because
    nothing on this disk changes; the endpoint and the arguments are the whole
    picture."""
    state = proposal.expected_state or {}
    return {
        "change": "remote_call",
        "workspace": "",
        "relative_path": "",
        "diff": [],
        "added": 0,
        "removed": 0,
        "truncated": False,
        "omitted_lines": 0,
        "error": "",
        "server": dict(state.get("server") or {}),
        "arguments": dict(proposal.normalized_arguments or {}),
    }


def server_identity_matches(expected: Dict[str, Any], spec) -> bool:
    """True when the live server is still the one the user approved."""
    if not isinstance(expected, dict) or spec is None:
        return False
    current = server_identity(spec)
    for key in ("id", "transport", "url", "command", "cwd"):
        if str(expected.get(key) or "") != str(current.get(key) or ""):
            return False
    return True


def _command_text(command) -> str:
    if isinstance(command, (list, tuple)):
        return " ".join(str(item) for item in command if str(item).strip())
    return str(command or "").strip()
