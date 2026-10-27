"""M8 tests: backups, restore, export, purge, integrity, lock and redaction."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile

from agent_framework.companion.maintenance import (
    BACKUP_DIR,
    EXPORT_DIR,
    InstanceLock,
    create_backup,
    export_data,
    integrity_report,
    list_backups,
    lock_info,
    purge_all,
    purge_memory,
    purge_session,
    restore_backup,
    verify_backup,
)
from agent_framework.companion.models import AgentMode
from agent_framework.companion.redaction import redact_text
from agent_framework.companion.store import SCHEMA_VERSION, CompanionStore
from agent_framework.companion.tool_results import ToolResultStore


ROOT = Path(__file__).resolve().parents[1]

CHILD_LOCK_SCRIPT = """
import sys
from agent_framework.companion.maintenance import InstanceLock

lock = InstanceLock(sys.argv[1])
acquired = lock.acquire()
print("locked" if acquired else "busy")
if acquired:
    lock.release()
"""


class MaintenanceTestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.store = CompanionStore(self.data / "companion.db")
        self.store.ensure_session("main", AgentMode.CHAT)

    def tearDown(self):
        self.temp.cleanup()

    def message(self, session_id="main", content="你好"):
        return self.store.add_message(
            session_id,
            "user",
            content,
            AgentMode.CHAT,
        )


class IntegrityReportTest(MaintenanceTestBase):
    def test_current_database_is_clean(self):
        report = integrity_report(self.store.database_path)

        self.assertTrue(report["exists"])
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["foreign_key_violations"], 0)
        self.assertEqual(report["user_version"], SCHEMA_VERSION)
        self.assertEqual(report["version_state"], "current")
        self.assertEqual(report["missing_indexes"], [])
        self.assertTrue(report["fts_present"])
        self.assertEqual(report["fts_rows"], report["memory_rows"])
        self.assertEqual(report["counts"]["sessions"], 1)
        self.assertTrue(report["ok"])

    def test_missing_database_reports_not_ok(self):
        report = integrity_report(self.root / "absent.db")

        self.assertFalse(report["exists"])
        self.assertFalse(report["ok"])
        self.assertEqual(report["version_state"], "missing")
        self.assertEqual(report["integrity"], "missing")

    def test_missing_index_is_reported(self):
        connection = sqlite3.connect(str(self.store.database_path))
        try:
            connection.execute("DROP INDEX idx_skill_audit_session")
            connection.commit()
        finally:
            connection.close()

        report = integrity_report(self.store.database_path)

        self.assertIn("idx_skill_audit_session", report["missing_indexes"])
        self.assertFalse(report["ok"])


class BackupTest(MaintenanceTestBase):
    def test_backup_manifest_and_listing(self):
        self.message(content="备份前的消息")

        info = create_backup(self.data, reason="unit-test")

        path = Path(info["path"])
        self.assertTrue(path.is_file())
        self.assertGreater(info["size_bytes"], 0)
        self.assertEqual(info["schema_version"], SCHEMA_VERSION)
        self.assertEqual(info["messages"], 1)
        self.assertEqual(info["sessions"], 1)
        self.assertEqual(len(info["sha256"]), 64)

        manifest = json.loads(
            path.with_name(path.stem + ".json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["reason"], "unit-test")
        self.assertEqual(manifest["schema_version"], SCHEMA_VERSION)
        self.assertEqual(manifest["messages"], 1)

        entries = list_backups(self.data)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], path.name)
        self.assertTrue(entries[0]["readable"])
        self.assertEqual(entries[0]["messages"], 1)

        verification = verify_backup(path)
        self.assertTrue(verification["valid"])
        self.assertEqual(verification["sha256"], info["sha256"])
        self.assertEqual(verification["schema_version"], SCHEMA_VERSION)

    def test_verify_rejects_missing_corrupt_and_newer(self):
        missing = verify_backup(self.root / "missing.db")
        self.assertFalse(missing["valid"])

        corrupt = self.root / "corrupt.db"
        corrupt.write_bytes(b"not a sqlite database")
        self.assertFalse(verify_backup(corrupt)["valid"])

        self.message()
        backup = create_backup(self.data)
        newer = self.data / "newer.db"
        newer.write_bytes(Path(backup["path"]).read_bytes())
        connection = sqlite3.connect(str(newer))
        try:
            connection.execute(
                "PRAGMA user_version = {}".format(SCHEMA_VERSION + 1)
            )
            connection.commit()
        finally:
            connection.close()
        result = verify_backup(newer)
        self.assertFalse(result["valid"])
        self.assertIn("更新的版本", result["reason"])

    def test_restore_recovers_snapshot_and_keeps_safety_backup(self):
        self.message(content="备份前的消息")
        backup = create_backup(self.data, reason="before-restore")
        self.message(content="备份后的消息")
        self.assertEqual(len(self.store.list_messages("main")), 2)

        result = restore_backup(self.data, backup["path"])

        self.assertTrue(Path(result["safety_backup"]).is_file())
        self.assertEqual(result["schema_version"], SCHEMA_VERSION)
        contents = [
            row["content"] for row in self.store.list_messages("main")
        ]
        self.assertEqual(contents, ["备份前的消息"])
        self.assertEqual(len(list_backups(self.data)), 2)
        self.assertTrue(verify_backup(result["safety_backup"])["valid"])

    def test_restore_rejects_invalid_backup(self):
        from agent_framework.companion.maintenance import MaintenanceError

        corrupt = self.root / "corrupt.db"
        corrupt.write_bytes(b"broken")
        with self.assertRaises(MaintenanceError):
            restore_backup(self.data, corrupt)


class ExportTest(MaintenanceTestBase):
    def test_export_zip_contains_tables_and_tool_results_but_no_keys(self):
        self.message(content="要导出的消息")
        self.store.add_memory(
            "main",
            "preference",
            "喜欢喝茶",
            None,
            0.9,
            0.7,
            status="confirmed",
        )
        tool = ToolResultStore(self.data).save("main", "read_file", "x" * 100)

        keys_dir = self.data / "config"
        keys_dir.mkdir(parents=True, exist_ok=True)
        (keys_dir / "keys.json").write_text(
            '{"api_key": "sk-should-not-export-123456"}',
            encoding="utf-8",
        )
        (self.data / "keys.json").write_text(
            '{"token": "sk-also-private-123456789"}',
            encoding="utf-8",
        )

        output = self.root / "out" / "export.zip"
        result = export_data(self.data, output_path=output)

        self.assertTrue(output.is_file())
        self.assertEqual(result["tool_results"], 1)
        with zipfile.ZipFile(str(output)) as archive:
            names = archive.namelist()
            self.assertIn("export.json", names)
            self.assertIn("manifest.json", names)
            self.assertIn("tool_results/{}.json".format(tool["result_id"]), names)
            self.assertFalse(any("keys" in name for name in names))
            text = "".join(
                archive.read(name).decode("utf-8")
                for name in names
                if name.endswith(".json")
            )
            self.assertNotIn("sk-should-not-export", text)
            self.assertNotIn("sk-also-private", text)
            payload = json.loads(archive.read("export.json").decode("utf-8"))
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        self.assertEqual(payload["format"], "companion-export")
        self.assertEqual(len(payload["tables"]["messages"]), 1)
        self.assertEqual(len(payload["tables"]["memories"]), 1)
        self.assertEqual(manifest["tool_results"], 1)
        self.assertEqual(manifest["schema_version"], SCHEMA_VERSION)

    def test_export_can_skip_tool_results(self):
        ToolResultStore(self.data).save("main", "run", "payload")
        result = export_data(self.data, include_tool_results=False)

        self.assertEqual(result["tool_results"], 0)
        with zipfile.ZipFile(result["path"]) as archive:
            names = archive.namelist()
        self.assertFalse(any(name.startswith("tool_results/") for name in names))


class PurgeTest(MaintenanceTestBase):
    def test_purge_memory_removes_tree_and_recall_references(self):
        parent = self.store.add_memory(
            "main", "preference", "喜欢茶", None, 0.9, 0.7, status="confirmed"
        )
        child = self.store.add_memory(
            "main",
            "summary",
            "喜欢喝茶",
            None,
            0.8,
            0.6,
            status="confirmed",
            derived_from=[parent],
        )
        other = self.store.add_memory(
            "main", "fact", "住在杭州", None, 0.8, 0.6, status="confirmed"
        )
        mixed = self.store.log_recall(
            "main", "hash-1", [parent, other], [0.9, 0.5], 3.0
        )
        only_child = self.store.log_recall(
            "main", "hash-2", [child], [0.8], 2.0
        )

        result = purge_memory(self.store, parent)

        self.assertEqual(result["deleted"], 2)
        self.assertEqual(sorted(result["memory_ids"]), sorted([parent, child]))
        self.assertEqual(result["recall_updated"], 1)
        self.assertEqual(result["recall_deleted"], 1)
        remaining = {
            memory.memory_id for memory in self.store.list_memories("main")
        }
        self.assertEqual(remaining, {other})
        recalls = {
            row["recall_id"]: row
            for row in self.store.list_recall_logs("main", limit=100)
        }
        self.assertNotIn(only_child, recalls)
        self.assertEqual(json.loads(recalls[mixed]["fact_ids"]), [other])
        connection = sqlite3.connect(str(self.store.database_path))
        try:
            fts_rows = connection.execute(
                "SELECT COUNT(*) FROM memory_fts"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(fts_rows, 1)

    def test_purge_session_removes_only_target_session(self):
        self.store.ensure_session("other", AgentMode.WORK)
        self.message("main", "主会话消息")
        self.message("other", "其他会话消息")
        self.store.add_memory(
            "main", "fact", "主会话记忆", None, 0.8, 0.6, status="confirmed"
        )
        self.store.add_memory(
            "other", "fact", "其他会话记忆", None, 0.8, 0.6, status="confirmed"
        )
        tools = ToolResultStore(self.data)
        main_tool = tools.save("main", "read_file", "main payload")
        other_tool = tools.save("other", "read_file", "other payload")

        result = purge_session(self.store, "main", data_dir=self.data)

        self.assertEqual(result["tool_results"]["deleted"], 1)
        self.assertEqual(self.store.list_messages("main"), [])
        self.assertEqual(len(self.store.list_messages("other")), 1)
        self.assertEqual(
            [memory.content for memory in self.store.list_memories("main")],
            [],
        )
        self.assertEqual(len(self.store.list_memories("other")), 1)
        sessions = {row["id"] for row in self.store.list_sessions()}
        self.assertEqual(sessions, {"other"})
        files = {path.name for path in (self.data / "tool_results").glob("tr_*.json")}
        self.assertEqual(files, {"{}.json".format(other_tool["result_id"])})
        self.assertNotIn(
            "{}.json".format(main_tool["result_id"]),
            files,
        )

    def test_purge_all_clears_records_and_optional_files(self):
        self.message()
        self.store.add_memory(
            "main", "fact", "一条记忆", None, 0.8, 0.6, status="confirmed"
        )
        ToolResultStore(self.data).save("main", "read_file", "payload")
        create_backup(self.data, reason="pre-purge")
        export_data(self.data)
        overwrite_backup = self.data / BACKUP_DIR / "act_demo"
        overwrite_backup.mkdir(parents=True, exist_ok=True)
        (overwrite_backup / "note.md").write_text("旧内容", encoding="utf-8")
        self.assertEqual(len(list_backups(self.data)), 1)

        result = purge_all(
            self.store,
            data_dir=self.data,
            include_backups=True,
            include_exports=True,
        )

        self.assertGreater(result["removed_files"], 0)
        self.assertEqual(result["tool_results"]["deleted"], 1)
        self.assertEqual(self.store.list_messages("main"), [])
        self.assertEqual(self.store.list_memories("main"), [])
        self.assertEqual(self.store.list_sessions(), [])
        self.assertEqual(list_backups(self.data), [])
        exports = self.data / EXPORT_DIR
        self.assertEqual(list(exports.glob("*.zip")), [])
        self.assertEqual(
            list((self.data / BACKUP_DIR).glob("companion-*.db")),
            [],
        )
        self.assertFalse(overwrite_backup.exists())


class MigrationSnapshotTest(MaintenanceTestBase):
    def test_reopening_older_schema_creates_pre_migration_backup(self):
        self.message(content="旧版本数据")
        connection = sqlite3.connect(str(self.store.database_path))
        try:
            connection.execute("PRAGMA user_version = 7")
            connection.commit()
        finally:
            connection.close()

        reopened = CompanionStore(self.store.database_path)

        self.assertEqual(reopened.migration_backup_error, "")
        self.assertTrue(reopened.migration_backup_path)
        backup_path = Path(reopened.migration_backup_path)
        self.assertTrue(backup_path.is_file())
        manifest = json.loads(
            backup_path.with_name(backup_path.stem + ".json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["reason"], "pre-migration")
        self.assertEqual(manifest["schema_version"], 7)
        self.assertEqual(manifest["messages"], 1)
        report = integrity_report(reopened.database_path)
        self.assertEqual(report["user_version"], SCHEMA_VERSION)
        self.assertTrue(report["ok"])


class InstanceLockTest(MaintenanceTestBase):
    def test_same_process_lock_is_reentrant_and_peekable(self):
        lock = InstanceLock(self.data)
        try:
            self.assertTrue(lock.acquire())
            info = lock_info(self.data)
            self.assertEqual(info["pid"], os.getpid())
            self.assertEqual(info["app"], "companion")

            inner = InstanceLock(self.data)
            self.assertTrue(inner.acquire())
            inner.release()
            self.assertIsNotNone(lock_info(self.data))
        finally:
            lock.release()
        self.assertIsNone(lock_info(self.data))

    def test_lock_is_exclusive_across_processes(self):
        lock = InstanceLock(self.data)
        self.assertTrue(lock.acquire())
        try:
            busy = self._child_attempt()
            self.assertEqual(busy, "busy")
        finally:
            lock.release()

        free = self._child_attempt()
        self.assertEqual(free, "locked")
        self.assertIsNone(lock_info(self.data))

    def _child_attempt(self):
        completed = subprocess.run(
            [sys.executable, "-c", CHILD_LOCK_SCRIPT, str(self.data)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout.strip()


class RedactionTest(MaintenanceTestBase):
    def test_redact_text_masks_known_secret_shapes(self):
        text = (
            "key sk-abcdef1234567890 and "
            "Authorization: Bearer abcdefghijklmnop "
            "password=hunter2 "
            "ghp_012345678901234567890123456789012345"
        )

        masked = redact_text(text)

        self.assertNotIn("sk-abcdef1234567890", masked)
        self.assertNotIn("abcdefghijklmnop", masked)
        self.assertNotIn("hunter2", masked)
        self.assertNotIn("ghp_012345678901234567890123456789012345", masked)
        self.assertIn("[REDACTED]", masked)

    def test_skill_audit_detail_is_persisted_redacted(self):
        self.store.log_skill_call(
            "main",
            "demo",
            "read",
            "chat",
            "denied",
            "token=sk-abcdef1234567890",
        )

        row = self.store.list_skill_audits("main")[0]

        self.assertNotIn("sk-abcdef1234567890", row["detail"])
        self.assertIn("[REDACTED]", row["detail"])


class StoreManagementQueryTest(MaintenanceTestBase):
    def test_list_queries_back_the_console(self):
        self.store.ensure_session("other", AgentMode.WORK)
        self.store.set_affect(
            "main",
            "user",
            "calm",
            0.4,
            0.2,
            0.8,
            0.9,
            None,
            "test",
        )

        sessions = {row["id"] for row in self.store.list_sessions()}
        self.assertEqual(sessions, {"main", "other"})
        self.assertEqual(self.store.list_proposals("main"), [])
        affects = self.store.list_affect_states("main")
        self.assertEqual(len(affects), 1)
        self.assertEqual(affects[0].mood_label, "calm")


if __name__ == "__main__":
    unittest.main()
