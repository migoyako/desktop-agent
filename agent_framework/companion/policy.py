"""Mode-aware tool permission policy."""

from __future__ import annotations

from .models import AgentMode, RiskLevel


ALLOW = "allow"
ASK = "ask"
DENY = "deny"


def decide_tool_policy(mode: AgentMode, risk: RiskLevel) -> str:
    """Return allow/ask/deny before a proposal is shown to the user."""
    mode = AgentMode(mode)
    risk = RiskLevel(risk)

    if mode == AgentMode.CHAT:
        return ALLOW if risk == RiskLevel.SAFE else DENY
    if mode == AgentMode.QUIET:
        return ALLOW if risk == RiskLevel.SAFE else DENY
    if mode == AgentMode.WORK:
        if risk in (RiskLevel.SAFE, RiskLevel.FS_READ, RiskLevel.NETWORK):
            return ALLOW
        if risk == RiskLevel.FS_WRITE:
            return ASK
        return DENY
    if mode == AgentMode.EXECUTE:
        if risk in (RiskLevel.SAFE, RiskLevel.FS_READ, RiskLevel.NETWORK):
            return ALLOW
        if risk in (
            RiskLevel.FS_WRITE,
            RiskLevel.SHELL,
            RiskLevel.INPUT_CONTROL,
        ):
            return ASK
    return DENY


def requires_confirmation(mode: AgentMode, risk: RiskLevel) -> bool:
    return decide_tool_policy(mode, risk) == ASK


def mode_label(mode: AgentMode) -> str:
    return {
        AgentMode.CHAT: "陪伴",
        AgentMode.WORK: "工作",
        AgentMode.EXECUTE: "执行",
        AgentMode.QUIET: "安静",
    }[AgentMode(mode)]
