"""M2 batch 2: derivation cascades, chat import and the evidence console."""

import json
import tempfile
import unittest
from pathlib import Path

from agent_framework.companion.importer import (
    ChatImportService,
    ImportConfirmationRequired,
)
from agent_framework.companion.memory import MemoryService
from agent_framework.companion.models import AgentMode
from agent_framework.companion.store import SCHEMA_VERSION, CompanionStore


class MemoryBatch2Test(unittest.TestCase):
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

    def write_json(self, name, payload):
        path = self.root / name
        path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_database_reaches_current_version_with_graph_and_import_tables(self):
        self.assertEqual(SCHEMA_VERSION, 8)
        with self.store._connection() as connection:
            version = connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            experience_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(experiences)"
                )
            }
        self.assertEqual(version, SCHEMA_VERSION)
        self.assertIn("memory_relations", tables)
        self.assertIn("import_jobs", tables)
        self.assertIn("import_messages", tables)
        self.assertIn("memory_id", experience_columns)
        self.assertIn("status", experience_columns)

    def test_v2_database_upgrades_to_graph_and_import_schema(self):
        with self.store._connection() as connection:
            connection.execute("DROP TABLE import_jobs")
            connection.execute("DROP TABLE import_messages")
            connection.execute("DROP TABLE memory_relations")
            connection.execute("PRAGMA user_version = 2")

        store = CompanionStore(self.store.database_path)

        with store._connection() as connection:
            version = connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertEqual(version, SCHEMA_VERSION)
        self.assertIn("memory_relations", tables)
        self.assertIn("import_jobs", tables)
        self.assertIn("import_messages", tables)

    def test_forgetting_fact_propagates_to_derived_memories_and_experiences(self):
        base_id = self.observe("请记住我的项目代号是青龙。")
        base = [
            item for item in self.confirmed() if item.content.find("青龙") >= 0
        ][0]
        summary_id = self.store.add_memory(
            session_id="main",
            kind="reflection",
            content="一起推进了青龙项目部署",
            source_message_id=None,
            confidence=1.0,
            importance=0.8,
            status="confirmed",
            derived_from=[base.memory_id],
            relation="reflection",
        )
        experience_id = self.memory.remember_experience(
            session_id="main",
            title="部署青龙",
            summary="今天一起完成了青龙项目部署",
            source_message_id=None,
            receipt_id=None,
            derived_from=[summary_id],
        )
        self.store.add_memory(
            session_id="main",
            kind="shared_experience",
            content="青龙项目已经顺利上线",
            source_message_id=None,
            confidence=1.0,
            importance=0.8,
            status="confirmed",
            derived_from=[base.memory_id],
            relation="summary",
        )
        self.assertEqual(base_id, base.source_message_id)

        self.memory.forget(base.memory_id)

        for memory_id in (base.memory_id, summary_id, experience_id):
            self.assertEqual(
                self.store.get_memory(memory_id).status,
                "forgotten",
            )
        self.assertEqual(self.store.list_experiences("main"), [])
        self.assertEqual(self.memory.recall("main", "青龙"), [])
        events = self.store.list_memory_events(summary_id)
        self.assertIn(
            "forgotten_derived",
            [event["event_type"] for event in events],
        )

    def test_forgotten_summary_and_derivation_do_not_resurrect(self):
        self.observe("请记住我的项目代号是青龙。")
        base = [
            item for item in self.confirmed() if "青龙" in item.content
        ][0]
        summary = "一起完成了青龙项目部署"
        memory_id = self.memory.remember_experience(
            session_id="main",
            title="部署",
            summary=summary,
            source_message_id=None,
            receipt_id=None,
            derived_from=[base.memory_id],
        )
        self.assertTrue(memory_id)

        self.memory.forget(base.memory_id)

        self.assertEqual(
            self.memory.remember_experience(
                session_id="main",
                title="部署",
                summary=summary,
                source_message_id=None,
                receipt_id=None,
            ),
            "",
        )
        with self.assertRaises(ValueError):
            self.store.add_memory(
                session_id="main",
                kind="reflection",
                content="另一条反思",
                source_message_id=None,
                confidence=1.0,
                importance=0.8,
                status="confirmed",
                derived_from=[base.memory_id],
            )
        self.assertEqual(self.store.list_experiences("main"), [])
        self.assertFalse(
            any(
                item.content == summary and item.status != "forgotten"
                for item in self.store.list_memories("main")
            )
        )

    def test_protected_memory_is_not_archived_by_conflicting_observation(self):
        self.observe("请记住我喜欢咖啡。")
        old = self.confirmed()[0]
        self.memory.set_protected(old.memory_id, True)
        self.assertFalse(self.store.archive_memory(old.memory_id, "测试"))

        self.observe("请记住我不喜欢咖啡。")

        self.assertEqual(
            self.store.get_memory(old.memory_id).status,
            "confirmed",
        )
        candidates = self.store.list_memory_candidates(
            "main",
            memory_id=old.memory_id,
        )
        self.assertTrue(candidates)
        self.assertIn("受保护", candidates[0]["reason"])
        current = self.confirmed()
        self.assertEqual(len(current), 1)
        self.assertIn("喜欢咖啡", current[0].content)

    def test_persona_write_entry_marks_memory_protected(self):
        memory_id = self.memory.remember_persona(
            "main",
            "用户喜欢简洁、直接的回答",
        )

        memory = self.store.get_memory(memory_id)
        self.assertEqual(memory.kind, "persona")
        self.assertTrue(memory.protected)
        self.assertEqual(memory.status, "confirmed")
        self.assertEqual(memory.importance, 1.0)
        self.assertEqual(memory.canonical_key, "persona:用户喜欢简洁直接的回答")

        updated_id = self.memory.remember_persona(
            "main",
            "用户喜欢简洁、直接的回答",
        )
        self.assertEqual(updated_id, memory_id)
        self.assertEqual(len(self.store.list_memories("main")), 1)

    def test_evidence_console_exposes_source_candidate_and_recalls(self):
        text = "请记住我喜欢咖啡。"
        self.observe(text)
        memory = self.confirmed()[0]
        self.memory.recall("main", "咖啡")

        details = self.memory.details("main", memory.memory_id)

        self.assertEqual(details["memory"].memory_id, memory.memory_id)
        self.assertEqual(len(details["evidence"]), 1)
        self.assertEqual(details["evidence"][0]["quote"], "我喜欢咖啡")
        self.assertIn(text, details["evidence"][0]["message_content"])
        self.assertIn(
            "created",
            [event["event_type"] for event in details["events"]],
        )
        self.assertEqual(len(details["recalls"]), 1)
        self.assertIsNotNone(details["recalls"][0]["score"])
        self.assertEqual(details["derived"], [])

    def test_import_requires_cost_confirmation(self):
        path = self.write_json(
            "chat.json",
            [
                {"role": "user", "content": "请记住我喜欢咖啡。"},
                {"role": "assistant", "content": "好的。"},
            ],
        )
        service = ChatImportService(self.store, self.memory)

        with self.assertRaises(ImportConfirmationRequired) as caught:
            service.run(path, target_session_id="main")

        estimate = caught.exception.estimate
        self.assertEqual(estimate["messages"], 2)
        self.assertEqual(estimate["user_messages"], 1)
        self.assertGreaterEqual(estimate["estimated_memories"], 1)
        self.assertEqual(self.store.list_messages("main"), [])

    def test_import_pauses_and_resumes_without_duplicate_messages(self):
        path = self.write_json(
            "chat.json",
            [
                {"role": "user", "content": "请记住我喜欢咖啡。"},
                {"role": "assistant", "content": "好的。"},
                {"role": "user", "content": "请记住我的生日是5月1日。"},
                {"role": "user", "content": "请记住我喜欢安静。"},
            ],
        )
        service = ChatImportService(self.store, self.memory)

        first = service.run(
            path,
            target_session_id="main",
            max_messages=2,
            approved=True,
        )
        self.assertEqual(first["status"], "paused")
        self.assertEqual(first["cursor"], 2)
        self.assertEqual(first["imported_messages"], 2)

        reopened = CompanionStore(self.store.database_path)
        service = ChatImportService(reopened, MemoryService(reopened))
        second = service.run(path, target_session_id="main", approved=True)
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["cursor"], 4)
        self.assertEqual(second["imported_messages"], 4)
        self.assertEqual(second["skipped_duplicates"], 0)

        third = service.run(path, target_session_id="main", approved=True)
        self.assertEqual(third["status"], "completed")
        self.assertEqual(third["imported_messages"], 4)

    def test_import_deduplicates_overlapping_copies(self):
        first = self.write_json(
            "first.json",
            [
                {"role": "user", "content": "请记住我喜欢咖啡。"},
                {"role": "user", "content": "请记住我喜欢安静。"},
            ],
        )
        second = self.write_json(
            "second.json",
            [
                {"role": "user", "content": "请记住我喜欢安静。"},
                {"role": "user", "content": "请记住我习惯早起。"},
            ],
        )
        service = ChatImportService(self.store, self.memory)

        first_result = service.run(first, target_session_id="main", approved=True)
        self.assertEqual(first_result["imported_messages"], 2)

        second_result = service.run(
            second,
            target_session_id="main",
            approved=True,
        )
        self.assertEqual(second_result["imported_messages"], 1)
        self.assertEqual(second_result["skipped_duplicates"], 1)

    def test_import_skips_roleplay_conversation_memories(self):
        path = self.write_json(
            "roleplay.json",
            [
                {
                    "title": "角色扮演练习",
                    "messages": [
                        {
                            "role": "user",
                            "content": "我叫林夜，我最喜欢咖啡。",
                        },
                        {"role": "assistant", "content": "（点头）"},
                        {
                            "role": "user",
                            "content": "请记住我的生日是5月1日。",
                        },
                    ],
                }
            ],
        )
        service = ChatImportService(self.store, self.memory)

        estimate = service.estimate(path, "main")
        result = service.run(path, target_session_id="main", approved=True)

        self.assertEqual(estimate["estimated_memories"], 0)
        self.assertEqual(result["imported_messages"], 3)
        self.assertEqual(result["imported_memories"], 0)
        self.assertEqual(self.store.list_memories("main"), [])

    def test_import_roleplay_state_stops_at_end_marker(self):
        path = self.write_json(
            "roleplay.json",
            [
                {"role": "user", "content": "我们来角色扮演吧。"},
                {"role": "assistant", "content": "好，你扮演谁？"},
                {"role": "user", "content": "我叫林夜，我喜欢咖啡。"},
                {"role": "user", "content": "结束扮演。"},
                {"role": "user", "content": "请记住我喜欢安静。"},
            ],
        )
        service = ChatImportService(self.store, self.memory)

        estimate = service.estimate(path, "main")
        result = service.run(path, target_session_id="main", approved=True)

        self.assertEqual(estimate["estimated_memories"], 1)
        self.assertEqual(result["imported_memories"], 1)
        contents = [item.content for item in self.confirmed()]
        self.assertTrue(any("安静" in content for content in contents))
        self.assertFalse(any("咖啡" in content for content in contents))

    def test_import_does_not_resurrect_forgotten_content(self):
        self.observe("请记住我喜欢咖啡。")
        memory = self.confirmed()[0]
        self.memory.forget(memory.memory_id)

        path = self.write_json(
            "forgotten.json",
            [
                {"role": "user", "content": "请记住我喜欢咖啡。"},
                {"role": "user", "content": "请记住我喜欢安静。"},
            ],
        )
        service = ChatImportService(self.store, self.memory)
        result = service.run(path, target_session_id="main", approved=True)

        self.assertEqual(result["imported_memories"], 1)
        contents = [item.content for item in self.confirmed()]
        self.assertFalse(any("咖啡" in content for content in contents))
        self.assertEqual(self.memory.recall("main", "咖啡"), [])

    def test_jsonl_and_conversation_exports_are_supported(self):
        jsonl = self.root / "chat.jsonl"
        jsonl.write_text(
            "\n".join(
                [
                    json.dumps(
                        {"role": "user", "content": "请记住我喜欢咖啡。"},
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {"role": "assistant", "content": "好的。"},
                        ensure_ascii=False,
                    ),
                ]
            ),
            encoding="utf-8",
        )
        conversations = self.write_json(
            "conversations.json",
            [
                {
                    "title": "旧会话",
                    "messages": [
                        {
                            "role": "user",
                            "content": "请记住我习惯早起。",
                        }
                    ],
                }
            ],
        )
        service = ChatImportService(self.store, self.memory)

        jsonl_result = service.run(
            jsonl,
            target_session_id="main",
            approved=True,
        )
        conversation_result = service.run(
            conversations,
            target_session_id="main",
            approved=True,
        )

        self.assertEqual(jsonl_result["imported_messages"], 2)
        self.assertEqual(conversation_result["imported_messages"], 1)
        self.assertEqual(len(self.confirmed()), 2)


if __name__ == "__main__":
    unittest.main()
