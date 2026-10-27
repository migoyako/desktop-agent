"""M2 second-batch tests: FTS5/BM25 recall with keyword fallback."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_framework.companion.memory import MemoryService
from agent_framework.companion.models import AgentMode
from agent_framework.companion.store import SCHEMA_VERSION, CompanionStore
from agent_framework.companion.textseg import (
    fts_match_query,
    fts_segment,
    segment_tokens,
)


class TextSegmentTest(unittest.TestCase):
    def test_chinese_run_produces_two_and_three_grams(self):
        tokens = segment_tokens("我喜欢")
        self.assertEqual(tokens, ["我喜", "喜欢", "我喜欢"])

    def test_single_chinese_character_kept_as_is(self):
        self.assertEqual(segment_tokens("茶"), ["茶"])

    def test_ascii_and_mixed_text(self):
        tokens = segment_tokens("我喜欢Python编程")
        self.assertIn("python", tokens)
        self.assertIn("编程", tokens)
        self.assertIn("喜欢", tokens)

    def test_segment_is_deduplicated_and_lowercase(self):
        segment = fts_segment("Python python PYthon")
        self.assertEqual(segment, "python")

    def test_match_query_joins_tokens_with_or(self):
        match = fts_match_query("喜欢咖啡")
        self.assertIn('"喜欢" OR', match)
        self.assertIn('"咖啡"', match)

    def test_empty_or_punctuation_query_has_no_match(self):
        self.assertEqual(fts_match_query("。！？"), "")
        self.assertEqual(fts_match_query(""), "")


class MemoryFtsTest(unittest.TestCase):
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

    def raw_connection(self):
        return sqlite3.connect(str(self.store.database_path))

    def drop_fts_tables(self):
        connection = self.raw_connection()
        try:
            names = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE name LIKE 'memory_fts%'"
                ).fetchall()
            ]
            for name in names:
                connection.execute('DROP TABLE IF EXISTS "{}"'.format(name))
            connection.commit()
        finally:
            connection.close()

    def user_version(self):
        connection = self.raw_connection()
        try:
            return connection.execute("PRAGMA user_version").fetchone()[0]
        finally:
            connection.close()

    def test_new_store_reaches_current_version_with_fts(self):
        self.assertEqual(SCHEMA_VERSION, 8)
        self.assertEqual(self.user_version(), SCHEMA_VERSION)
        self.assertTrue(self.store.fts_enabled)
        connection = self.raw_connection()
        try:
            row = connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'memory_fts'"
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)

    def test_v1_database_upgrades_and_backfills_index(self):
        self.observe("请记住我喜欢喝咖啡。")
        self.drop_fts_tables()
        connection = self.raw_connection()
        try:
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
        finally:
            connection.close()

        store = CompanionStore(self.root / "companion.db")

        self.assertEqual(self.user_version(), SCHEMA_VERSION)
        self.assertTrue(store.fts_enabled)
        hits = store.search_memories_fts("main", "咖啡")
        self.assertTrue(hits)
        self.assertEqual(len(hits), 1)

    def test_chinese_recall_uses_fts_and_logs_source(self):
        self.observe("请记住我喜欢喝咖啡。")
        self.observe("请记住我不吃香菜。")
        self.observe("我的生日是5月1日。")
        memory = next(
            item for item in self.confirmed() if "咖啡" in item.content
        )

        recalled = self.memory.recall("main", "咖啡")

        self.assertEqual(
            [item.memory_id for _, item, _ in recalled],
            [memory.memory_id],
        )
        logs = self.store.list_recall_logs("main")
        self.assertEqual(logs[0]["source"], "fts")

    def test_single_character_query_falls_back_to_keyword_recall(self):
        self.observe("请记住我喜欢喝茶。")
        memory = self.confirmed()[0]

        recalled = self.memory.recall("main", "茶")

        self.assertEqual(
            [item.memory_id for _, item, _ in recalled],
            [memory.memory_id],
        )
        logs = self.store.list_recall_logs("main")
        self.assertEqual(logs[0]["source"], "keyword")

    def test_correction_reindexes_fts(self):
        self.observe("请记住我喜欢喝咖啡。")
        target = self.confirmed()[0]

        self.memory.correct(target.memory_id, "我喜欢喝茶")

        self.assertEqual(self.store.search_memories_fts("main", "咖啡"), [])
        self.assertTrue(self.store.search_memories_fts("main", "喝茶"))
        recalled = self.memory.recall("main", "喝茶")
        self.assertEqual(
            [item.memory_id for _, item, _ in recalled],
            [target.memory_id],
        )

    def test_corrupted_index_falls_back_then_self_heals(self):
        self.observe("请记住我喜欢喝咖啡。")
        self.drop_fts_tables()

        recalled = self.memory.recall("main", "咖啡")

        self.assertEqual(len(recalled), 1)
        self.assertIn("咖啡", recalled[0][1].content)
        logs = self.store.list_recall_logs("main")
        self.assertEqual(logs[0]["source"], "keyword")
        self.assertFalse(self.store.fts_enabled)

        healed = CompanionStore(self.root / "companion.db")
        self.assertTrue(healed.fts_enabled)
        self.assertTrue(healed.search_memories_fts("main", "咖啡"))

    def test_forgotten_memory_not_recalled_via_fts(self):
        self.observe("请记住我喜欢喝咖啡。")
        memory = self.confirmed()[0]

        self.memory.forget(memory.memory_id)

        self.assertEqual(self.store.search_memories_fts("main", "咖啡"), [])
        self.assertEqual(self.memory.recall("main", "咖啡"), [])

    def test_rebuild_restores_missing_index_rows(self):
        self.observe("请记住我喜欢喝咖啡。")
        connection = self.raw_connection()
        try:
            connection.execute("DELETE FROM memory_fts")
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(self.store.search_memories_fts("main", "咖啡"), [])

        self.assertTrue(self.store.rebuild_memory_fts())

        hits = self.store.search_memories_fts("main", "咖啡")
        self.assertEqual(len(hits), 1)

    def test_missing_fts5_support_degrades_to_keyword(self):
        broken = (
            "CREATE VIRTUAL TABLE memory_fts "
            "USING fts5_module_not_available(seg);"
        )
        with mock.patch(
            "agent_framework.companion.store.MEMORY_FTS_SCHEMA",
            broken,
        ):
            store = CompanionStore(self.root / "no_fts.db")
        store.ensure_session("main", AgentMode.CHAT)
        memory = MemoryService(store)
        self.assertFalse(store.fts_enabled)
        self.assertIsNone(store.search_memories_fts("main", "咖啡"))

        message_id = store.add_message(
            "main",
            "user",
            "请记住我喜欢喝咖啡。",
            AgentMode.CHAT,
        )
        memory.observe_user_message("main", message_id, "请记住我喜欢喝咖啡。")
        recalled = memory.recall("main", "咖啡")

        self.assertEqual(len(recalled), 1)
        logs = store.list_recall_logs("main")
        self.assertEqual(logs[0]["source"], "keyword")


if __name__ == "__main__":
    unittest.main()
