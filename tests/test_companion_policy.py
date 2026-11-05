"""Permission and confirmation digest tests."""

import unittest

from agent_framework.companion.models import (
    AgentMode,
    RiskLevel,
    confirmation_digest,
)
from agent_framework.companion.policy import (
    ASK,
    ALLOW,
    DENY,
    PERMISSION_BALANCED,
    PERMISSION_HINTS,
    PERMISSION_LABELS,
    PERMISSION_RELAXED,
    PERMISSION_STRICT,
    PERMISSION_TIERS,
    decide_tool_policy,
    requires_confirmation,
    resolve_permission_tier,
)


class CompanionPolicyTest(unittest.TestCase):
    def test_chat_has_no_execution_tools(self):
        self.assertEqual(
            decide_tool_policy(AgentMode.CHAT, RiskLevel.SAFE),
            ALLOW,
        )
        for risk in (
            RiskLevel.FS_READ,
            RiskLevel.FS_WRITE,
            RiskLevel.NETWORK,
            RiskLevel.SHELL,
            RiskLevel.INPUT_CONTROL,
            RiskLevel.EXTERNAL,
        ):
            self.assertEqual(decide_tool_policy(AgentMode.CHAT, risk), DENY)

    def test_work_requires_write_confirmation(self):
        self.assertEqual(
            decide_tool_policy(AgentMode.WORK, RiskLevel.NETWORK),
            ALLOW,
        )
        self.assertEqual(
            decide_tool_policy(AgentMode.WORK, RiskLevel.FS_WRITE),
            ASK,
        )
        self.assertEqual(
            decide_tool_policy(AgentMode.WORK, RiskLevel.SHELL),
            DENY,
        )

    def test_digest_changes_with_arguments(self):
        first = confirmation_digest(
            "write_note",
            {"content": "a"},
            "fs-write",
            "target",
        )
        second = confirmation_digest(
            "write_note",
            {"content": "b"},
            "fs-write",
            "target",
        )
        self.assertNotEqual(first, second)


class PermissionTierTest(unittest.TestCase):
    """The 高级 panel's three tiers may only move a confirmation, never a refusal."""

    def test_strict_drops_shell_and_input_control_entirely(self):
        for mode in (AgentMode.WORK, AgentMode.EXECUTE):
            with self.subTest(mode=mode):
                self.assertEqual(
                    decide_tool_policy(mode, RiskLevel.SHELL, PERMISSION_STRICT), DENY
                )
                self.assertEqual(
                    decide_tool_policy(mode, RiskLevel.INPUT_CONTROL, PERMISSION_STRICT),
                    DENY,
                )
                # 写入仍然要确认 —— 它本来就要弹卡片，保守档不该抢走这一步。
                self.assertEqual(
                    decide_tool_policy(mode, RiskLevel.FS_WRITE, PERMISSION_STRICT), ASK
                )

    def test_strict_leaves_read_and_network_alone(self):
        # 只读与联网工具直接返回结果，永远不会生成确认卡：把它们改成 ASK 只会让
        # 调用以「工具没有生成提案」失败，所以档位对它们没有影响。
        for risk in (RiskLevel.FS_READ, RiskLevel.NETWORK):
            self.assertEqual(
                decide_tool_policy(AgentMode.WORK, risk, PERMISSION_STRICT), ALLOW
            )
        self.assertEqual(
            decide_tool_policy(AgentMode.CHAT, RiskLevel.FS_READ, PERMISSION_STRICT), DENY
        )

    def test_relaxed_only_trusts_file_writes(self):
        self.assertEqual(
            decide_tool_policy(AgentMode.WORK, RiskLevel.FS_WRITE, PERMISSION_RELAXED),
            ALLOW,
        )
        self.assertEqual(
            decide_tool_policy(AgentMode.EXECUTE, RiskLevel.SHELL, PERMISSION_RELAXED), ASK
        )
        self.assertEqual(
            decide_tool_policy(
                AgentMode.EXECUTE, RiskLevel.INPUT_CONTROL, PERMISSION_RELAXED
            ),
            ASK,
        )

    def test_balanced_is_the_shipped_matrix(self):
        for mode in AgentMode:
            for risk in RiskLevel:
                with self.subTest(mode=mode, risk=risk):
                    self.assertEqual(
                        decide_tool_policy(mode, risk, PERMISSION_BALANCED),
                        decide_tool_policy(mode, risk),
                    )
                    self.assertEqual(
                        decide_tool_policy(mode, risk, None),
                        decide_tool_policy(mode, risk),
                    )

    def test_a_tier_never_turns_a_refusal_into_a_permission(self):
        for mode in AgentMode:
            for risk in RiskLevel:
                baseline = decide_tool_policy(mode, risk)
                if baseline != DENY:
                    continue
                for tier in PERMISSION_TIERS:
                    with self.subTest(mode=mode, risk=risk, tier=tier):
                        self.assertEqual(decide_tool_policy(mode, risk, tier), DENY)

    def test_confirmation_follows_the_tier(self):
        self.assertTrue(
            requires_confirmation(AgentMode.WORK, RiskLevel.FS_WRITE, PERMISSION_STRICT)
        )
        self.assertFalse(
            requires_confirmation(AgentMode.WORK, RiskLevel.FS_WRITE, PERMISSION_RELAXED)
        )
        self.assertFalse(
            requires_confirmation(AgentMode.EXECUTE, RiskLevel.SHELL, PERMISSION_STRICT)
        )

    def test_unknown_tiers_fall_back_to_balanced(self):
        self.assertEqual(resolve_permission_tier("信任"), PERMISSION_BALANCED)
        self.assertEqual(resolve_permission_tier(""), PERMISSION_BALANCED)
        self.assertEqual(resolve_permission_tier(None), PERMISSION_BALANCED)
        self.assertEqual(resolve_permission_tier("STRICT"), PERMISSION_STRICT)
        self.assertEqual(set(PERMISSION_LABELS), set(PERMISSION_TIERS))
        self.assertEqual(set(PERMISSION_HINTS), set(PERMISSION_TIERS))


if __name__ == "__main__":
    unittest.main()
