"""M1a safety tests for confirmation, idempotency and crash recovery."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_framework.companion.file_actions import UncertainExecutionError
from agent_framework.companion.models import AgentMode, ProposalStatus, SearchResult
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.llm import OfflineLLMClient


class StubSearch:
    def search(self, query, limit=5):
        self.last_query = query
        return [
            SearchResult(
                title="Example result",
                url="https://example.com/result",
                snippet="A short search summary.",
            )
        ]


class ExecutionSafetyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.other_workspace = self.root / "other"
        self.other_workspace.mkdir()
        self.data = self.root / "data"
        self.store = CompanionStore(self.data / "companion.db")
        self.runtime = CompanionRuntime(
            store=self.store,
            llm_client=OfflineLLMClient(),
            search_service=SearchService(StubSearch()),
            data_dir=self.data,
            session_id="main",
            offline=True,
        )
        self.runtime.select_workspace(str(self.workspace))

    def tearDown(self):
        self.temp.cleanup()

    def _proposal(self):
        result = self.runtime.handle_message(
            "搜索 Python Agent 并保存为笔记",
            AgentMode.WORK,
        )
        self.assertIsNotNone(result["proposal"])
        return result["proposal"]

    def test_workspace_switch_rejects_confirmation(self):
        proposal = self._proposal()
        self.runtime.select_workspace(str(self.other_workspace))

        result = self.runtime.confirm_proposal(proposal.action_id, AgentMode.WORK)

        self.assertEqual(result["receipt"].status, "not_executed")
        self.assertEqual(result["receipt"].result["code"], "workspace_changed")
        self.assertFalse(result["receipt"].undoable)
        self.assertEqual(
            self.store.get_proposal(proposal.action_id).status,
            ProposalStatus.NOT_EXECUTED,
        )
        self.assertEqual(self.runtime.pending_proposals(), [])
        self.assertEqual(list(self.root.rglob("*.md")), [])

    def test_mode_downgrade_denies_stale_proposal(self):
        proposal = self._proposal()

        result = self.runtime.confirm_proposal(proposal.action_id, AgentMode.CHAT)

        self.assertEqual(result["receipt"].status, "not_executed")
        self.assertEqual(result["receipt"].result["code"], "mode_denied")
        self.assertEqual(self.runtime.current_mode(), AgentMode.CHAT)
        self.assertEqual(list(self.workspace.rglob("*.md")), [])
        repeated = self.runtime.confirm_proposal(
            proposal.action_id,
            AgentMode.EXECUTE,
        )
        self.assertEqual(repeated["receipt"].receipt_id, result["receipt"].receipt_id)
        self.assertEqual(list(self.workspace.rglob("*.md")), [])

    def test_duplicate_confirmation_writes_once(self):
        proposal = self._proposal()

        first = self.runtime.confirm_proposal(proposal.action_id)
        second = self.runtime.confirm_proposal(proposal.action_id)

        self.assertEqual(first["receipt"].status, "completed")
        self.assertEqual(
            second["receipt"].receipt_id,
            first["receipt"].receipt_id,
        )
        self.assertEqual(len(list(self.workspace.rglob("*.md"))), 1)
        self.assertEqual(len(self.runtime.experiences()), 1)
        execution = self.store.get_execution(proposal.action_id)
        self.assertEqual(execution["status"], "completed")
        self.assertEqual(execution["receipt_id"], first["receipt"].receipt_id)

    def test_uncertain_execution_is_unknown_and_not_undoable(self):
        proposal = self._proposal()

        with patch.object(
            self.runtime.notes,
            "execute",
            side_effect=UncertainExecutionError("模拟替换后崩溃"),
        ):
            result = self.runtime.confirm_proposal(proposal.action_id)

        self.assertEqual(result["receipt"].status, "unknown")
        self.assertFalse(result["receipt"].undoable)
        self.assertEqual(
            self.store.get_proposal(proposal.action_id).status,
            ProposalStatus.UNKNOWN,
        )
        self.assertEqual(self.store.get_execution(proposal.action_id)["status"], "unknown")
        self.assertEqual(list(self.workspace.rglob("*.md")), [])

    def test_undo_uses_receipt_snapshot_after_workspace_switch(self):
        proposal = self._proposal()
        confirmed = self.runtime.confirm_proposal(proposal.action_id)
        self.assertEqual(confirmed["receipt"].status, "completed")
        written = list(self.workspace.rglob("*.md"))
        self.assertEqual(len(written), 1)

        self.runtime.select_workspace(str(self.other_workspace))
        message = self.runtime.undo_receipt(confirmed["receipt"].receipt_id)

        self.assertIn("已撤销", message)
        self.assertEqual(list(self.workspace.rglob("*.md")), [])
        receipt = self.store.get_receipt(confirmed["receipt"].receipt_id)
        self.assertEqual(receipt.undo_status, "undone")

    def test_crash_recovery_marks_unknown_without_replay(self):
        proposal = self._proposal()
        self.assertTrue(
            self.store.transition_proposal_status(
                proposal.action_id,
                ProposalStatus.AWAITING_CONFIRMATION,
                ProposalStatus.RUNNING,
            )
        )
        claimed, _ = self.store.claim_execution(proposal)
        self.assertTrue(claimed)

        restarted = CompanionRuntime(
            store=CompanionStore(self.data / "companion.db"),
            llm_client=OfflineLLMClient(),
            search_service=SearchService(StubSearch()),
            data_dir=self.data,
            session_id="main",
            offline=True,
        )

        self.assertEqual(len(restarted.recovered_receipts), 1)
        receipt = restarted.recovered_receipts[0]
        self.assertEqual(receipt.status, "unknown")
        self.assertFalse(receipt.undoable)
        self.assertEqual(
            restarted.store.get_proposal(proposal.action_id).status,
            ProposalStatus.UNKNOWN,
        )
        self.assertEqual(
            restarted.store.get_execution(proposal.action_id)["status"],
            "unknown",
        )
        self.assertEqual(list(self.workspace.rglob("*.md")), [])

    def test_mode_persists_across_restart(self):
        proposal = self._proposal()

        restarted = CompanionRuntime(
            store=CompanionStore(self.data / "companion.db"),
            llm_client=OfflineLLMClient(),
            search_service=SearchService(StubSearch()),
            data_dir=self.data,
            session_id="main",
            offline=True,
        )

        self.assertEqual(restarted.current_mode(), AgentMode.WORK)
        result = restarted.confirm_proposal(proposal.action_id)
        self.assertEqual(result["receipt"].status, "completed")

    def test_expired_proposal_is_rejected(self):
        proposal = self.runtime.notes.create_proposal(
            session_id="main",
            query="过期笔记",
            content="expired",
            mode=AgentMode.WORK,
            ttl_hours=-1,
        )
        self.store.create_proposal(proposal)

        result = self.runtime.confirm_proposal(proposal.action_id, AgentMode.WORK)

        self.assertEqual(result["receipt"].status, "not_executed")
        self.assertEqual(result["receipt"].result["code"], "proposal_expired")
        self.assertEqual(list(self.workspace.rglob("*.md")), [])
        self.assertEqual(self.store.get_execution(proposal.action_id), None)


if __name__ == "__main__":
    unittest.main()
