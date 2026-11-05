"""Persistence smoke tests for the companion runtime."""

import sqlite3
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

    def test_session_summaries_capture_topic_and_last_message(self):
        self.store.add_message("chat_a", "user", "帮我整理书架", AgentMode.CHAT)
        self.store.add_message("chat_a", "assistant", "好的，先按主题分组。", AgentMode.CHAT)
        self.store.add_message("chat_b", "user", "修复登录问题", AgentMode.WORK)
        self.store.ensure_session("empty", AgentMode.CHAT)
        rows = {
            row["id"]: row for row in self.store.list_session_summaries()
        }
        self.assertEqual(set(rows), {"chat_a", "chat_b", "empty"})
        self.assertEqual(rows["chat_a"]["topic"], "帮我整理书架")
        self.assertEqual(rows["chat_a"]["message_count"], 2)
        self.assertEqual(rows["chat_a"]["last_role"], "assistant")
        self.assertEqual(rows["chat_a"]["last_content"], "好的，先按主题分组。")
        self.assertEqual(rows["chat_b"]["last_role"], "user")
        self.assertEqual(rows["empty"]["message_count"], 0)
        self.assertIsNone(rows["empty"]["topic"])
        self.assertEqual(rows["empty"]["mode"], AgentMode.CHAT.value)

    def test_session_rename_pin_and_unread_round_trip(self):
        self.store.add_message("chat_a", "user", "帮我整理书架", AgentMode.CHAT)
        self.store.add_message("chat_b", "user", "修复登录问题", AgentMode.WORK)
        self.store.add_message("chat_c", "user", "第三段对话", AgentMode.CHAT)

        self.assertTrue(self.store.rename_session("chat_a", "  书架整理  "))
        self.assertTrue(self.store.set_session_pinned("chat_b", True))
        self.assertTrue(self.store.set_session_unread("chat_a", True))

        rows = {
            row["id"]: row for row in self.store.list_session_summaries()
        }
        self.assertEqual(rows["chat_a"]["title"], "书架整理")
        self.assertEqual(rows["chat_a"]["unread"], 1)
        self.assertTrue(rows["chat_b"]["pinned_at"])
        self.assertEqual(rows["chat_c"]["title"], "")
        self.assertEqual(rows["chat_c"]["unread"], 0)
        self.assertEqual(
            [row["id"] for row in self.store.list_session_summaries()][0],
            "chat_b",
        )

        self.assertTrue(self.store.set_session_pinned("chat_b", False))
        self.assertTrue(self.store.set_session_unread("chat_a", False))
        rows = {
            row["id"]: row for row in self.store.list_session_summaries()
        }
        self.assertEqual(rows["chat_b"]["pinned_at"], "")
        self.assertEqual(rows["chat_a"]["unread"], 0)
        self.assertFalse(self.store.rename_session("missing", "无此会话"))
        self.assertFalse(self.store.set_session_unread("missing", True))

    def test_session_draft_lifecycle(self):
        self.store.ensure_session("chat_a", AgentMode.CHAT)
        self.store.ensure_session("chat_b", AgentMode.WORK)

        self.assertEqual(self.store.get_draft("chat_a"), "")
        self.assertTrue(self.store.set_draft("chat_a", "半句没发完的话"))
        self.assertEqual(self.store.get_draft("chat_a"), "半句没发完的话")
        self.assertEqual(self.store.get_draft("chat_b"), "")

        self.assertTrue(self.store.set_draft("chat_a", "替换后的草稿"))
        self.assertEqual(self.store.get_draft("chat_a"), "替换后的草稿")

        self.assertTrue(self.store.set_draft("chat_a", "   "))
        self.assertEqual(self.store.get_draft("chat_a"), "")
        self.assertFalse(self.store.set_draft("missing", "草稿"))

    def test_delete_session_removes_messages_drafts_and_memories(self):
        message_id = self.store.add_message(
            "chat_a",
            "user",
            "请记住我的项目代号是青龙。",
            AgentMode.CHAT,
        )
        self.store.add_message("chat_b", "user", "保留这个会话", AgentMode.CHAT)
        self.store.set_draft("chat_a", "未发送草稿")
        self.store.set_session_unread("chat_a", True)
        self.store.add_memory(
            "chat_a",
            "fact",
            "项目代号是青龙。",
            message_id,
            0.9,
            0.8,
            status="active",
        )
        with self.store._connection() as connection:
            memory_id = connection.execute(
                "SELECT memory_id FROM memories WHERE session_id = ?",
                ("chat_a",),
            ).fetchone()["memory_id"]

        self.assertTrue(self.store.delete_session("chat_a"))

        self.assertIsNone(self.store.get_session_mode("chat_a"))
        self.assertEqual(self.store.list_messages("chat_a"), [])
        self.assertEqual(self.store.get_draft("chat_a"), "")
        with self.store._connection() as connection:
            memory_count = connection.execute(
                "SELECT COUNT(*) FROM memories WHERE session_id = ?",
                ("chat_a",),
            ).fetchone()[0]
            indexed = connection.execute(
                "SELECT COUNT(*) FROM memory_fts WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()[0]
        self.assertEqual(memory_count, 0)
        self.assertEqual(indexed, 0)
        self.assertEqual(
            [row["id"] for row in self.store.list_session_summaries()],
            ["chat_b"],
        )
        self.assertFalse(self.store.delete_session("chat_a"))

    def test_v8_database_gains_session_management_fields(self):
        path = Path(self.temp.name) / "legacy.db"
        connection = sqlite3.connect(str(path))
        try:
            connection.executescript(
                """
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            connection.execute("PRAGMA user_version = 8")
            connection.commit()
        finally:
            connection.close()

        store = CompanionStore(path)

        with store._connection() as connection:
            version = connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(sessions)"
                )
            }
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertEqual(version, 13)
        self.assertTrue({"title", "pinned_at", "unread"} <= columns)
        self.assertIn("session_drafts", tables)
        store.ensure_session("chat_a", AgentMode.CHAT)
        self.assertTrue(store.set_draft("chat_a", "迁移后的草稿"))
        self.assertEqual(store.get_draft("chat_a"), "迁移后的草稿")


if __name__ == "__main__":
    unittest.main()
