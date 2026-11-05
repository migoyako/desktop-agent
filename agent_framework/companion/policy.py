"""Mode-aware tool permission policy."""

from __future__ import annotations

from .models import AgentMode, RiskLevel


ALLOW = "allow"
ASK = "ask"
DENY = "deny"

PERMISSION_STRICT = "strict"
PERMISSION_BALANCED = "balanced"
PERMISSION_RELAXED = "relaxed"
PERMISSION_TIERS = (PERMISSION_STRICT, PERMISSION_BALANCED, PERMISSION_RELAXED)

PERMISSION_LABELS = {
    PERMISSION_STRICT: "保守",
    PERMISSION_BALANCED: "平衡",
    PERMISSION_RELAXED: "信任",
}

PERMISSION_HINTS = {
    PERMISSION_STRICT: "文件写入仍要确认；shell、界面控制与远程工具直接禁用。",
    PERMISSION_BALANCED: "按模式的出厂判断：写入、shell、界面控制与远程工具都要确认。",
    PERMISSION_RELAXED: (
        "文件写入不再逐次确认（回执可撤销）；"
        "shell、界面控制与远程工具仍要确认。"
    ),
}

# Risks that build a proposal, so they are the only ones a confirmation tier can
# meaningfully move. Read and network tools answer directly and cannot pause for
# a confirmation card: asking one would only produce "工具没有生成提案".
_PROPOSAL_RISKS = (
    RiskLevel.FS_WRITE,
    RiskLevel.SHELL,
    RiskLevel.INPUT_CONTROL,
    RiskLevel.EXTERNAL,
)


def resolve_permission_tier(value) -> str:
    """Unknown or missing tiers fall back to the shipped matrix."""
    text = str(value or "").strip().lower()
    return text if text in PERMISSION_TIERS else PERMISSION_BALANCED


def decide_tool_policy(mode: AgentMode, risk: RiskLevel, tier=None) -> str:
    """Return allow/ask/deny before a proposal is shown to the user."""
    mode = AgentMode(mode)
    risk = RiskLevel(risk)

    if mode == AgentMode.CHAT:
        decision = ALLOW if risk == RiskLevel.SAFE else DENY
    elif mode == AgentMode.QUIET:
        decision = ALLOW if risk == RiskLevel.SAFE else DENY
    elif mode == AgentMode.WORK:
        if risk in (RiskLevel.SAFE, RiskLevel.FS_READ, RiskLevel.NETWORK):
            decision = ALLOW
        elif risk in (RiskLevel.FS_WRITE, RiskLevel.EXTERNAL):
            decision = ASK
        else:
            decision = DENY
    elif mode == AgentMode.EXECUTE:
        if risk in (RiskLevel.SAFE, RiskLevel.FS_READ, RiskLevel.NETWORK):
            decision = ALLOW
        elif risk in _PROPOSAL_RISKS:
            decision = ASK
        else:
            decision = DENY
    else:
        decision = DENY
    return _apply_tier(decision, risk, resolve_permission_tier(tier))


def _apply_tier(decision: str, risk: RiskLevel, tier: str) -> str:
    """Nudge the shipped matrix without ever turning a refusal into a permission."""
    if decision == DENY:
        return DENY
    if decision == ASK:
        if tier == PERMISSION_STRICT and risk in (
            RiskLevel.SHELL,
            RiskLevel.INPUT_CONTROL,
            RiskLevel.EXTERNAL,
        ):
            # 保守：shell、输入控制与远程工具连确认的机会都不给，直接从清单里消失。
            return DENY
        if tier == PERMISSION_RELAXED and risk == RiskLevel.FS_WRITE:
            # 信任：本机文件写入自行执行，回执与撤销仍然照记；远程调用不在"信任"的含义里，
            # 副作用落在别人的机器上，撤销不了，所以照旧要确认。
            return ALLOW
    return decision


def requires_confirmation(mode: AgentMode, risk: RiskLevel, tier=None) -> bool:
    return decide_tool_policy(mode, risk, tier) == ASK


def mode_label(mode: AgentMode) -> str:
    return {
        AgentMode.CHAT: "陪伴",
        AgentMode.WORK: "工作",
        AgentMode.EXECUTE: "执行",
        AgentMode.QUIET: "安静",
    }[AgentMode(mode)]
