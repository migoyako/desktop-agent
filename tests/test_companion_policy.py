"""Permission and confirmation digest tests."""

import unittest

from agent_framework.companion.models import (
    AgentMode,
    RiskLevel,
    confirmation_digest,
)
from agent_framework.companion.policy import ASK, ALLOW, DENY, decide_tool_policy


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


if __name__ == "__main__":
    unittest.main()
