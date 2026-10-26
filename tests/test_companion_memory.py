"""M2 tests for evidence-backed memory lifecycle."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_framework.companion.memory import MemoryService
from agent_framework.companion.models import AgentMode
from agent_framework.companion.store import SCHEMA_VERSION, CompanionStore


class MemoryLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = CompanionStore(self.root / "companion.db")
        self.store.ensure_session("main", AgentMode.CHAT)
        self.memory = MemoryService(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def observe(self, text):
        message_id = self.store.add_message(
            "main",
            "user",
            text,
            AgentMode.CHAT,
        )
        self.memory.observe_user_message("main", message_id, text)
        return message_id

    def confirmed(self):
        return self.store.list_memories("main", statuses=["confirmed"])

    def test_legacy_database_migrates_to_current_version(self):
        path = self.root / "legacy.db"
        connection = sqlite3.connect(str(path))
        connection.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE memories (
                memory_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                source_message_id INTEGER,
                confidence REAL NOT NULL,
                importance REAL NOT NULL,
                activation REAL NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO sessions VALUES (
                'main', 'chat',
                '2026-10-24T00:00:00+00:00',
                '2026-10-24T00:00:00+00:00'
            );
            INSERT INTO memories VALUES (
                'mem_legacy', 'main', 'preference', '旧记忆', NULL,
                0.8, 0.7, 0.7, 'confirmed',
                '2026-10-24T00:00:00+00:00',
                '2026-10-24T00:00:00+00:00'
            );
            """
        )
        connection.commit()
        connection.close()

        store = CompanionStore(path)

        check = sqlite3.connect(str(path))
        try:
            version = check.execute("PRAGMA user_version").fetchone()[0]
            columns = {
                row[1]
                for row in check.execute("PRAGMA table_info(memories)")
            }
        finally:
            check.close()
        self.assertEqual(version, SCHEMA_VERSION)
        self.assertIn("canonical_key", columns)
        self.assertIn("evidence_count", columns)
        memories = store.list_memories("main")
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0].content, "旧记忆")
        self.assertEqual(memories[0].canonical_key, "")

    def test_user_message_creates_memory_with_exact_evidence(self):
        text = "请记住我喜欢简洁回答。"
        self.observe(text)

        memories = self.confirmed()
        self.assertEqual(len(memories), 1)
        memory = memories[0]
        self.assertEqual(memory.kind, "preference")
        self.assertEqual(memory.canonical_key, "preference:简洁回答")
        self.assertEqual(memory.evidence_count, 1)
        self.assertEqual(memory.status, "confirmed")

        evidence = self.store.list_memory_evidence(memory.memory_id)
        self.assertEqual(len(evidence), 1)
        item = evidence[0]
        self.assertEqual(item["source_role"], "user")
        self.assertEqual(item["quote"], "我喜欢简洁回答")
        self.assertEqual(
            text[item["quote_start"] : item["quote_end"]],
            item["quote"],
        )
        events = self.store.list_memory_events(memory.memory_id)
        self.assertIn("created", [event["event_type"] for event in events])

    def test_assistant_message_cannot_become_evidence(self):
        message_id = self.store.add_message(
            "main",
            "assistant",
            "我喜欢咖啡。",
            AgentMode.CHAT,
        )

        with self.assertRaises(ValueError):
            self.store.add_memory(
                session_id="main",
                kind="preference",
                content="我喜欢咖啡",
                source_message_id=message_id,
                confidence=0.9,
                importance=0.7,
                status="confirmed",
                canonical_key="preference:咖啡",
                evidence={
                    "message_id": message_id,
                    "quote": "我喜欢咖啡",
                },
            )

        self.assertEqual(self.store.list_memories("main"), [])

    def test_observe_ignores_assistant_messages(self):
        message_id = self.store.add_message(
            "main",
            "assistant",
            "请记住我喜欢咖啡。",
            AgentMode.CHAT,
        )

        created = self.memory.observe_user_message(
            "main",
            message_id,
            "请记住我喜欢咖啡。",
        )

        self.assertEqual(created, [])
        self.assertEqual(self.store.list_memories("main"), [])

    def test_explicit_fact_is_confirmed_and_identity_stays_candidate(self):
        self.observe("请记住我不吃香菜。")
        self.observe("我的生日是5月1日。")

        memories = self.store.list_memories("main")
        fact = next(item for item in memories if item.kind == "fact")
        identity = next(item for item in memories if item.kind == "identity")
        self.assertEqual(fact.status, "confirmed")
        self.assertTrue(fact.canonical_key.startswith("fact:user:"))
        self.assertEqual(identity.status, "candidate")
        self.assertEqual(identity.canonical_key, "identity:birthday")

        candidates = self.store.list_memory_candidates(
            "main",
            statuses=["pending"],
        )
        self.assertTrue(
            any(
                item["canonical_key"] == "identity:birthday"
                for item in candidates
            )
        )

    def test_preference_correction_supersedes_old_value(self):
        self.observe("请记住我喜欢咖啡。")
        first = self.confirmed()[0]
        self.assertIn("喜欢咖啡", first.content)

        self.observe("请记住我不喜欢咖啡。")

        old = self.store.get_memory(first.memory_id)
        self.assertEqual(old.status, "archived")
        self.assertEqual(old.canonical_key, "preference:咖啡")
        events = self.store.list_memory_events(first.memory_id)
        self.assertIn("superseded", [event["event_type"] for event in events])

        current = self.confirmed()
        self.assertEqual(len(current), 1)
        self.assertIn("不喜欢咖啡", current[0].content)
        self.assertEqual(current[0].canonical_key, "preference:咖啡")

        recalled = self.memory.recall("main", "咖啡")
        self.assertEqual(
            [memory.memory_id for _, memory, _ in recalled],
            [current[0].memory_id],
        )

    def test_forget_creates_tombstone_and_blocks_resurrection(self):
        self.observe("请记住我喜欢咖啡。")
        memory = self.confirmed()[0]

        self.memory.forget(memory.memory_id)

        self.assertEqual(
            self.store.get_memory(memory.memory_id).status,
            "forgotten",
        )
        connection = sqlite3.connect(str(self.store.database_path))
        try:
            tombstones = connection.execute(
                "SELECT * FROM memory_tombstones WHERE session_id = 'main'"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(len(tombstones), 1)
        self.assertEqual(self.memory.recall("main", "咖啡"), [])

        self.observe("请记住我喜欢咖啡。")

        active = [
            item
            for item in self.store.list_memories("main")
            if item.status != "forgotten"
        ]
        self.assertEqual(active, [])

    def test_same_content_reinforces_evidence_once_per_message(self):
        text = "请记住我喜欢咖啡。"
        first_message = self.observe(text)
        self.observe(text)

        memories = self.confirmed()
        self.assertEqual(len(memories), 1)
        memory = memories[0]
        self.assertEqual(memory.evidence_count, 2)
        self.assertEqual(
            len(self.store.list_memory_evidence(memory.memory_id)),
            2,
        )
        events = self.store.list_memory_events(memory.memory_id)
        self.assertIn("reinforced", [event["event_type"] for event in events])

        self.memory.observe_user_message("main", first_message, text)
        self.assertEqual(
            len(self.store.list_memory_evidence(memory.memory_id)),
            2,
        )

    def test_recall_log_does_not_store_raw_query(self):
        self.observe("请记住我喜欢咖啡。")
        memory = self.confirmed()[0]

        self.memory.recall("main", "咖啡", limit=5)

        logs = self.store.list_recall_logs("main")
        self.assertEqual(len(logs), 1)
        entry = logs[0]
        self.assertEqual(len(entry["query_hash"]), 64)
        self.assertNotIn("咖啡", entry["query_hash"])
        self.assertEqual(json.loads(entry["fact_ids"]), [memory.memory_id])
        self.assertEqual(len(json.loads(entry["scores"])), 1)
        self.assertEqual(entry["used_in_reply"], 0)


if __name__ == "__main__":
    unittest.main()
