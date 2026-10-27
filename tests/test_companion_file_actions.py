"""Safety tests for the restricted note writer."""

from datetime import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_framework.companion.file_actions import (
    FileActionError,
    NoteActionService,
    WorkspaceGuard,
    fingerprint,
    safe_note_filename,
)
from agent_framework.companion.models import AgentMode
from agent_framework.companion.store import CompanionStore


class NoteActionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.store = CompanionStore(self.root / "data" / "companion.db")
        self.store.ensure_session("session", AgentMode.WORK)
        self.store.set_workspace(str(self.workspace))
        self.service = NoteActionService(self.store, self.root / "data")

    def tearDown(self):
        self.temp.cleanup()

    def test_rejects_path_escape(self):
        guard = WorkspaceGuard(self.workspace)
        with self.assertRaises(FileActionError):
            guard.resolve("../outside.md")
        with self.assertRaises(FileActionError):
            guard.resolve(str(self.root / "absolute.md"))

    def test_proposal_does_not_write_until_execute(self):
        proposal = self.service.create_proposal(
            "session",
            "safe note",
            "# Note\n\nBody",
        )
        target = Path(proposal.target_summary)
        self.assertFalse(target.exists())
        receipt = self.service.execute(proposal)
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "# Note\n\nBody")
        self.assertEqual(receipt.status, "completed")
        self.assertTrue(receipt.undoable)

    def test_conflicting_change_stops_write(self):
        proposal = self.service.create_proposal(
            "session",
            "conflict note",
            "new",
        )
        target = Path(proposal.target_summary)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("external", encoding="utf-8")
        with self.assertRaises(FileActionError):
            self.service.execute(proposal)
        self.assertEqual(target.read_text(encoding="utf-8"), "external")

    def test_undo_restores_previous_content(self):
        fixed = datetime.now()
        with patch("agent_framework.companion.file_actions.datetime") as clock:
            clock.now.return_value = fixed
            relative = safe_note_filename("restore note")
            target = self.workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("before", encoding="utf-8")
            proposal = self.service.create_proposal(
                "session",
                "restore note",
                "after",
            )
        self.store.create_proposal(proposal)
        self.assertEqual(proposal.normalized_arguments["relative_path"], relative)
        receipt = self.service.execute(proposal)
        self.store.save_receipt(receipt)
        self.assertEqual(target.read_text(encoding="utf-8"), "after")
        self.service.undo(receipt)
        self.assertEqual(target.read_text(encoding="utf-8"), "before")
        self.assertEqual(fingerprint(target)["sha256"], receipt.result["before"]["sha256"])


if __name__ == "__main__":
    unittest.main()
