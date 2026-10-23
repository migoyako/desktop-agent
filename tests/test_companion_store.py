"""Persistence smoke tests for the companion runtime."""

import tempfile
import unittest
from pathlib import Path

from agent_framework.companion.models import (
    ActionProposal,
    AgentMode,
    ProposalStatus,
    RiskLevel,
    confirmation_digest,
    new_id,
)
from agent_framework.companion.store import CompanionStore


class CompanionStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = CompanionStore(Path(self.temp.name) / "companion.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_messages_persist(self):
        message_id = self.store.add_message(
            "session",
            "user",
            "请记住我喜欢简洁回答。",
            AgentMode.CHAT,
        )
        self.assertGreater(message_id, 0)
        rows = self.store.list_messages("session")
        self.assertEqual(rows[-1]["content"], "请记住我喜欢简洁回答。")

    def test_proposal_and_workspace_persist(self):
        self.store.set_workspace(self.temp.name)
        arguments = {"relative_path": "notes/a.md", "content": "hello"}
        digest = confirmation_digest(
            "write_note",
            arguments,
            RiskLevel.FS_WRITE.value,
            "target",
        )
        proposal = ActionProposal(
            action_id=new_id("act"),
            session_id="session",
            mode=AgentMode.WORK,
            intent="test",
            tool_name="write_note",
            raw_arguments=arguments,
            normalized_arguments=arguments,
            expected_state={"target": None},
            risk=RiskLevel.FS_WRITE,
            target_summary="target",
            reversible=True,
            user_visible_plan="plan",
            confirmation_digest=digest,
            idempotency_key=new_id("idem"),
        )
        self.store.ensure_session("session", AgentMode.WORK)
        self.store.create_proposal(proposal)
        loaded = self.store.get_proposal(proposal.action_id)
        self.assertEqual(loaded.status, ProposalStatus.AWAITING_CONFIRMATION)
        self.assertEqual(loaded.normalized_arguments, arguments)
        self.assertEqual(self.store.get_workspace(), str(Path(self.temp.name)))


if __name__ == "__main__":
    unittest.main()
