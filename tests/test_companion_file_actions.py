"""Safety tests for the restricted note writer."""

from datetime import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_framework.companion.file_actions import (
    PREVIEW_DIFF_LINES,
    PREVIEW_LINE_CHARS,
    FileActionError,
    NoteActionService,
    diff_lines,
    fingerprint,
    safe_note_filename,
)
from agent_framework.companion.models import AgentMode
from agent_framework.companion.path_safety import WorkspaceGuard
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

    def test_preview_of_new_file_is_read_only(self):
        proposal = self.service.create_proposal("session", "new preview", "line one")
        target = Path(proposal.target_summary)

        preview = self.service.preview(proposal)

        self.assertEqual(preview["change"], "create")
        self.assertFalse(preview["error"])
        self.assertEqual(preview["relative_path"], proposal.normalized_arguments["relative_path"])
        self.assertTrue(str(preview["workspace"]).endswith("workspace"))
        self.assertGreater(preview["added"], 0)
        self.assertEqual(preview["removed"], 0)
        self.assertFalse(preview["truncated"])
        self.assertIn(("add", "line one"), preview["diff"])
        self.assertFalse(target.exists())

    def test_preview_of_existing_file_shows_before_and_after(self):
        relative = safe_note_filename("diff preview")
        target = self.workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("old line\nkeep me", encoding="utf-8")
        with patch("agent_framework.companion.file_actions.safe_note_filename", return_value=relative):
            proposal = self.service.create_proposal(
                "session",
                "diff preview",
                "new line\nkeep me",
            )

        preview = self.service.preview(proposal)

        self.assertEqual(preview["change"], "update")
        tags = [tag for tag, _ in preview["diff"]]
        self.assertIn("del", tags)
        self.assertIn("add", tags)
        self.assertIn("ctx", tags)
        self.assertEqual(preview["added"], 1)
        self.assertEqual(preview["removed"], 1)
        self.assertEqual(target.read_text(encoding="utf-8"), "old line\nkeep me")

    def test_preview_truncates_long_or_sensitive_content(self):
        content = "\n".join("line {} {}".format(index, "x" * 400) for index in range(120))

        rows, added, removed, omitted = diff_lines([], content.splitlines())

        self.assertEqual(len(rows), PREVIEW_DIFF_LINES)
        self.assertEqual(added, 120)
        self.assertEqual(removed, 0)
        self.assertEqual(omitted, 120 - PREVIEW_DIFF_LINES)
        self.assertTrue(all(len(line) <= PREVIEW_LINE_CHARS for _, line in rows))

    def test_preview_reports_changed_workspace(self):
        proposal = self.service.create_proposal("session", "stale workspace", "body")
        proposal.expected_state["workspace"]["file_id"] = "stale"

        preview = self.service.preview(proposal)

        self.assertEqual(preview["change"], "unavailable")
        self.assertTrue(preview["error"])
        self.assertEqual(preview["diff"], [])


if __name__ == "__main__":
    unittest.main()
