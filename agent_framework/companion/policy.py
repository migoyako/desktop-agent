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
    PERMISSION_STRICT: (
        "文件写入、删除、shell、启动应用、启动链、界面控制与远程工具直接禁用。"
    ),
    PERMISSION_BALANCED: (
        "文件写入在授权目录内直接执行并保留回执；删除、shell、启动链、界面控制与远程工具仍要确认；"
        "启动已登记的应用直接执行。"
    ),
    PERMISSION_RELAXED: (
        "文件写入不再逐次确认（回执可撤销）；"
        "删除、shell、启动链、界面控制与远程工具仍要确认；"
        "启动已登记的应用仍直接执行。"
    ),
}

# Risks that build a proposal, so they are the only ones a confirmation tier can
# meaningfully move. Read and network tools answer directly and cannot pause for
# a confirmation card: asking one would only produce "工具没有生成提案".
_PROPOSAL_RISKS = (
    RiskLevel.FS_WRITE,
    RiskLevel.FS_DELETE,
    RiskLevel.SHELL,
    RiskLevel.INPUT_CONTROL,
    RiskLevel.EXTERNAL,
    RiskLevel.LAUNCH_CHAIN,
)

# Risks the shipped matrix lets run without a card, and which the conservative
# tier therefore removes instead of downgrading to a confirmation.
_TIER_DENIED_ALLOW = (RiskLevel.APP_LAUNCH, RiskLevel.FS_WRITE)


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
        elif risk == RiskLevel.APP_LAUNCH:
            # 登记即授权：可执行文件与参数只从 app_catalog 读，模型选不了命令行，所以
            # 工作模式不必再逐次确认；能不能启动由授权表和身份复核决定。
            decision = ALLOW
        elif risk == RiskLevel.FS_WRITE:
            # A directory capability is the authorization. Once the user granted
            # fs-write, a second per-file yes/no card adds friction without adding
            # a different security decision.
            decision = ALLOW
        elif risk in (
            RiskLevel.FS_DELETE,
            RiskLevel.EXTERNAL,
            RiskLevel.LAUNCH_CHAIN,
        ):
            decision = ASK
        else:
            decision = DENY
    elif mode == AgentMode.EXECUTE:
        if risk in (RiskLevel.SAFE, RiskLevel.FS_READ, RiskLevel.NETWORK):
            decision = ALLOW
        elif risk == RiskLevel.APP_LAUNCH:
            decision = ALLOW
        elif risk == RiskLevel.FS_WRITE:
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
    if decision == ALLOW:
        if tier == PERMISSION_STRICT and risk in _TIER_DENIED_ALLOW:
            # 保守：出厂把启动已登记应用放行了，这一档不放宽——连确认卡片也不给，
            # 因为放行理由（登记即授权）本身就是一种放宽。
            return DENY
        return decision
    if decision == ASK:
        if tier == PERMISSION_STRICT and risk in (
            RiskLevel.FS_DELETE,
            RiskLevel.SHELL,
            RiskLevel.INPUT_CONTROL,
            RiskLevel.EXTERNAL,
            RiskLevel.LAUNCH_CHAIN,
        ):
            # 保守：删除、shell、输入控制与远程工具连确认的机会都不给，直接从清单里消失。
            return DENY
        if tier == PERMISSION_RELAXED and risk == RiskLevel.FS_WRITE:
            # 信任：本机文件写入自行执行，回执与撤销仍然照记；删除不在放行范围内——回收站
            # 也是用户看不见的目录搬家，而远程调用同样照旧要确认。
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
