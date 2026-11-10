"""S2 — file metadata index, search candidates and the leased read-only tools."""

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from agent_framework.companion.access_control import (
    CAPABILITY_FS_DELETE,
    CAPABILITY_FS_READ,
    CAPABILITY_FS_SEARCH,
    CAPABILITY_FS_WRITE,
    LEASE_ACTIVE,
    AccessControlError,
    AccessControlService,
)
from agent_framework.companion.file_index import (
    CANDIDATE_SCAN_LIMIT,
    MAX_CANDIDATES,
    READ_LEASE_OPERATIONS,
    FileIndexError,
    FileIndexService,
    TurnReadBroker,
    index_state_report,
    scan_directory,
)
from agent_framework.companion.maintenance import (
    EXPECTED_INDEXES,
    EXPORT_TABLES,
    PURGE_ALL_TABLES,
    integrity_report,
    purge_all,
)
from agent_framework.companion.models import AgentMode, RiskLevel
from agent_framework.companion.path_safety import (
    MAX_PATH_DEPTH,
    PathSafetyError,
    WorkspaceGuard,
)
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import (
    INDEX_FAILED,
    INDEX_MAX_DEPTH,
    INDEX_READY,
    INDEX_RUNNING,
    INDEX_STALE,
    SCHEMA_VERSION,
    CompanionStore,
)
from agent_framework.llm import OfflineLLMClient


class IndexFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.store = CompanionStore(self.data / "companion.db")
        self.access = AccessControlService(self.store)
        self.projects = self.root / "projects"
        self.projects.mkdir()
        self.index = FileIndexService(self.store, self.access)

    def tearDown(self):
        self.index.shutdown()
        self.temp.cleanup()

    def raw(self, sql, params=()):
        with self.store._connection() as connection:
            return connection.execute(sql, params).fetchall()

    def write(self, relative, text="内容", encoding="utf-8"):
        target = self.projects / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            target.write_bytes(text)
        else:
            target.write_text(text, encoding=encoding)
        return target

    def grant(self, capabilities=(CAPABILITY_FS_READ, CAPABILITY_FS_SEARCH), path=None):
        return self.access.grant_directory(
            path or self.projects, list(capabilities)
        )

    def indexed_grant(self, capabilities=(CAPABILITY_FS_READ, CAPABILITY_FS_SEARCH)):
        grant = self.grant(capabilities)
        self.index.index_grant(grant.grant_id)
        return grant

    def ref_for(self, name, grant_ids=None):
        grant_ids = grant_ids if grant_ids is not None else [
            grant.grant_id for grant in self.access.list_grants()
        ]
        rows = self.store.search_file_catalog(grant_ids, term=name)
        return rows[0]["file_ref"]

    def broker(self, task_id="msg_1", **kwargs):
        return TurnReadBroker(self.index, task_id, access=self.access, **kwargs)


class SchemaMigrationTest(IndexFixture):
    def test_v14_creates_catalog_and_index_state(self):
        self.assertEqual(SCHEMA_VERSION, 17)
        tables = {
            row[0]
            for row in self.raw(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertTrue({"file_catalog", "file_index_state"} <= tables)
        indexes = {
            row[0]
            for row in self.raw(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        self.assertTrue(
            {
                "idx_file_catalog_path",
                "idx_file_catalog_name",
                "idx_file_catalog_parent",
                "idx_file_catalog_grant",
            }
            <= indexes
        )

    def test_v13_database_gains_catalog_and_keeps_grants(self):
        self.write("keep.md", "旧数据")
        grant = self.grant()
        self.raw("DROP TABLE file_catalog;")
        self.raw("DROP TABLE file_index_state;")
        self.raw("PRAGMA user_version = 13")
        reopened = CompanionStore(self.data / "companion.db")
        self.assertEqual(
            int(self.raw("PRAGMA user_version")[0][0]), SCHEMA_VERSION
        )
        self.assertIsNotNone(reopened.get_access_grant(grant.grant_id))
        tables = {
            row[0]
            for row in self.raw(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertTrue({"file_catalog", "file_index_state"} <= tables)
        self.assertTrue(Path(reopened.migration_backup_path).is_file())

    def test_maintenance_lists_carry_the_new_tables(self):
        self.assertIn("file_catalog", EXPORT_TABLES)
        self.assertIn("file_index_state", EXPORT_TABLES)
        self.assertIn("file_catalog", PURGE_ALL_TABLES)
        self.assertIn("file_index_state", PURGE_ALL_TABLES)
        self.assertLess(
            PURGE_ALL_TABLES.index("file_catalog"),
            PURGE_ALL_TABLES.index("access_grants"),
        )
        for name in (
            "idx_file_catalog_path",
            "idx_file_catalog_name",
            "idx_file_catalog_parent",
            "idx_file_catalog_grant",
        ):
            self.assertIn(name, EXPECTED_INDEXES)

    def test_integrity_report_accepts_a_v14_database(self):
        report = integrity_report(self.data / "companion.db")
        self.assertEqual(report["missing_indexes"], [])
        self.assertTrue(report["ok"], report)

    def test_purge_all_clears_the_catalog(self):
        self.write("gone.md")
        self.indexed_grant()
        self.assertGreater(self.store.count_file_catalog(), 0)
        purge_all(self.store, self.data)
        self.assertEqual(self.store.count_file_catalog(), 0)
        self.assertEqual(self.store.list_file_index_states(), [])


class ScanTest(IndexFixture):
    def test_the_agent_data_directory_never_enters_the_index(self):
        # 用户把工作区的父目录（或数据目录本身）授权进来时，库文件、备份和工具
        # 结果不能变成"可搜到的文件"——那是代理自己的内部数据，不是用户的项目。
        self.write("keep.md")
        (self.data / "tool_results").mkdir(parents=True, exist_ok=True)
        (self.data / "tool_results" / "payload.txt").write_text("内部", encoding="utf-8")
        grant = self.grant(path=self.root)
        self.index.index_grant(grant.grant_id)
        rows = self.store.search_file_catalog([grant.grant_id])
        self.assertEqual([row["rel_path"] for row in rows], ["projects/keep.md"])

    def test_granting_the_data_directory_starts_no_background_scan(self):
        grant = self.grant(path=self.data)
        self.assertFalse(self.index.schedule_index(grant.grant_id))
        self.assertFalse(self.index.is_indexing(grant.grant_id))
        self.assertEqual(self.index.indexable_grants(), [])

    def test_scan_walks_files_and_reports_counts(self):
        self.write("readme.md", "顶层")
        self.write("Notes/todo.txt", "子层")
        entries, stats = scan_directory(self.projects)
        self.assertEqual(stats["files"], 2)
        self.assertFalse(stats["truncated"])
        names = sorted(item["rel_path"] for item in entries)
        self.assertEqual(names, ["Notes/todo.txt", "readme.md"])

    def test_scan_skips_heavy_and_blocked_directories(self):
        self.write("keep.md")
        self.write("node_modules/pkg/index.js")
        self.write(".venv/lib/site.py")
        self.write("__pycache__/mod.pyc")
        self.write(".git/config")
        entries, stats = scan_directory(self.projects)
        self.assertEqual([item["rel_path"] for item in entries], ["keep.md"])
        self.assertEqual(stats["skipped_directories"], 4)

    def test_indexed_paths_stay_resolvable_by_the_guard(self):
        # 索引里出现一个 resolve() 会拒绝的路径，等于搜到一个读不了的文件。
        self.write("a/b/c/d/e/f/g/h/deep.md")
        self.write("ok.md")
        grant = self.indexed_grant()
        guard = WorkspaceGuard(grant.canonical_root)
        rows = self.store.search_file_catalog([grant.grant_id])
        self.assertIn("ok.md", [row["rel_path"] for row in rows])
        for row in rows:
            self.assertTrue(
                (self.projects / row["rel_path"]).is_file(), row["rel_path"]
            )
            guard.resolve(row["rel_path"])

    def test_file_cap_marks_the_walk_truncated(self):
        for index in range(5):
            self.write("f{}.md".format(index))
        capped = FileIndexService(self.store, self.access, max_files=2)
        grant = self.grant()
        state = capped.index_grant(grant.grant_id)
        self.assertTrue(state["truncated"])
        self.assertEqual(state["files_indexed"], 2)

    def test_truncated_walk_does_not_reconcile(self):
        # 半截扫描说"这个文件不在了"是假证词：它可能只是在没扫到的那一段里。
        self.write("ghost.md")
        grant = self.grant()
        self.store.upsert_file_catalog(
            grant.grant_id,
            [
                {
                    "name": "ghost.md",
                    "rel_path": "ghost.md",
                    "parent_key": "",
                    "ext": ".md",
                    "size_bytes": 4,
                    "mtime": "2026-11-01T00:00:00+00:00",
                }
            ],
        )
        self.write("a.md")
        self.write("b.md")
        capped = FileIndexService(self.store, self.access, max_files=1)
        state = capped.index_grant(grant.grant_id)
        self.assertTrue(state["truncated"])
        self.assertFalse(state["reconciled"])
        self.assertEqual(state["removed"], 0)
        self.assertIn("ghost.md", self.store.list_file_catalog_keys(grant.grant_id))

    def test_reconciliation_removes_vanished_files_and_revives_recreated_ones(self):
        self.write("keep.md")
        self.write("drop.md")
        grant = self.indexed_grant()
        self.assertIn("drop.md", self.store.list_file_catalog_keys(grant.grant_id))
        (self.projects / "drop.md").unlink()
        state = self.index.index_grant(grant.grant_id)
        self.assertEqual(state["removed"], 1)
        self.assertNotIn("drop.md", self.store.list_file_catalog_keys(grant.grant_id))
        self.assertEqual(
            len(self.store.list_file_catalog_keys(grant.grant_id, status="deleted")),
            1,
        )
        self.write("drop.md", "又回来了")
        self.index.index_grant(grant.grant_id)
        self.assertIn("drop.md", self.store.list_file_catalog_keys(grant.grant_id))

    def test_search_survives_rename_by_keying_on_path(self):
        self.write("old.md", "x")
        grant = self.indexed_grant()
        (self.projects / "old.md").rename(self.projects / "new.md")
        self.index.index_grant(grant.grant_id)
        keys = self.store.list_file_catalog_keys(grant.grant_id)
        self.assertEqual(keys, ["new.md"])

    def test_index_state_is_ready_with_counts(self):
        self.write("one.md")
        grant = self.indexed_grant()
        state = self.store.get_file_index_state(grant.grant_id)
        self.assertEqual(state["status"], INDEX_READY)
        self.assertEqual(state["files_indexed"], 1)
        self.assertTrue(state["finished_at"])
        self.assertEqual(state["last_error"], "")

    def test_revoked_grant_refuses_indexing(self):
        grant = self.grant()
        self.access.revoke_grant(grant.grant_id)
        with self.assertRaises(FileIndexError) as caught:
            self.index.index_grant(grant.grant_id)
        self.assertEqual(caught.exception.code, "grant_revoked")

    def test_missing_directory_fails_loudly(self):
        workspace = self.root / "volume"
        workspace.mkdir()
        grant = self.grant(path=workspace)
        (workspace / "a.md").write_text("x", encoding="utf-8")
        shutil.rmtree(workspace)
        with self.assertRaises(FileIndexError) as caught:
            self.index.index_grant(grant.grant_id)
        self.assertEqual(caught.exception.code, "grant_unusable")
        state = self.store.get_file_index_state(grant.grant_id)
        self.assertEqual(state["status"], INDEX_FAILED)
        self.assertTrue(state["last_error"])


class BackgroundIndexTest(IndexFixture):
    def test_schedule_indexes_off_the_calling_thread(self):
        self.write("bg.md")
        grant = self.grant()
        self.assertTrue(self.index.schedule_index(grant.grant_id))
        self.assertEqual(self.index.wait_for_index(15), 0)
        self.assertEqual(
            self.store.get_file_index_state(grant.grant_id)["status"], INDEX_READY
        )

    def test_duplicate_schedule_is_ignored_while_running(self):
        grant = self.grant()
        started = threading.Event()
        release = threading.Event()

        def slow_index(grant_id):
            started.set()
            release.wait(5)

        self.index.index_grant = slow_index
        self.assertTrue(self.index.schedule_index(grant.grant_id))
        self.assertTrue(started.wait(5))
        self.assertFalse(self.index.schedule_index(grant.grant_id))
        release.set()
        self.assertEqual(self.index.wait_for_index(5), 0)
        self.assertFalse(self.index.is_indexing(grant.grant_id))


class SearchTest(IndexFixture):
    def test_returns_at_most_five_candidates(self):
        for index in range(9):
            self.write("note{}.md".format(index))
        self.indexed_grant()
        report = self.index.search("note")
        self.assertEqual(len(report["candidates"]), MAX_CANDIDATES)
        self.assertEqual(report["matched"], 9)
        self.assertEqual(report["limit"], MAX_CANDIDATES)

    def test_matched_is_a_floor_when_the_fetch_cap_is_hit(self):
        # 取候选有每目录上限，撞限时 matched 是下限：把它当总数，模型就会说"目录里
        # 只有这 400 个文件"，而真相是我们只读了 400 行就停了。
        for index in range(CANDIDATE_SCAN_LIMIT + 7):
            self.write("bulk/report_{:04d}.md".format(index))
        self.indexed_grant()
        report = self.index.search("report")
        self.assertTrue(report["matched_capped"])
        self.assertEqual(report["matched"], CANDIDATE_SCAN_LIMIT)
        self.assertLess(len(report["candidates"]), report["matched"])

    def test_matched_is_exact_below_the_fetch_cap(self):
        self.write("one/report.md")
        self.indexed_grant()
        report = self.index.search("report")
        self.assertFalse(report["matched_capped"])
        self.assertEqual(report["matched"], 1)

    def test_reasons_and_ranking(self):
        self.write("meeting-notes.md", "1")
        self.write("notes.md", "2")
        self.write("archive/notes-old.md", "3")
        self.indexed_grant()
        exact = self.index.search("notes.md")["candidates"][0]
        self.assertEqual(exact["name"], "notes.md")
        self.assertEqual(exact["reason"], "文件名完全匹配")
        broad = self.index.search("notes")["candidates"]
        self.assertTrue(all(item["reason"] for item in broad))
        self.assertEqual(broad[0]["reason"], "文件名匹配")

    def test_candidate_shape_has_no_absolute_path(self):
        self.write("shape.md")
        grant = self.indexed_grant()
        candidate = self.index.search("shape")["candidates"][0]
        self.assertEqual(
            set(candidate),
            {
                "file_ref",
                "name",
                "grant_id",
                "directory",
                "rel_path",
                "ext",
                "size_bytes",
                "mtime",
                "reason",
                "from_index",
            },
        )
        self.assertNotIn(str(self.projects), json.dumps(candidate, default=str))

    def test_ext_and_modified_after_filters(self):
        self.write("a.md")
        self.write("b.txt")
        self.indexed_grant()
        self.assertEqual(
            [item["name"] for item in self.index.search("", ext="md")["candidates"]],
            ["a.md"],
        )
        future = self.index.search("", modified_after="2030-01-01T00:00:00+00:00")
        self.assertEqual(future["candidates"], [])

    def test_parent_key_lists_one_level(self):
        self.write("root.md")
        self.write("Notes/inner.md")
        self.indexed_grant()
        top = self.index.search("", parent_key="")["candidates"]
        self.assertEqual([item["name"] for item in top], ["root.md"])
        inner = self.index.search("", parent_key="Notes")["candidates"]
        self.assertEqual([item["name"] for item in inner], ["inner.md"])

    def test_revoked_grant_rows_never_surface(self):
        self.write("secret.md")
        grant = self.indexed_grant()
        self.access.revoke_grant(grant.grant_id)
        report = self.index.search("secret")
        self.assertEqual(report["candidates"], [])
        self.assertEqual(report["matched"], 0)

    def test_cold_directory_falls_back_to_bounded_live_scan(self):
        self.write("cold/note.md")
        grant = self.grant()
        report = self.index.search("note")
        self.assertEqual(len(report["live_scan_grants"]), 1)
        self.assertEqual(report["indexed_grants"], [])
        candidate = report["candidates"][0]
        self.assertFalse(candidate["from_index"])
        self.assertIn("实时扫描", candidate["reason"])
        self.assertTrue(candidate["file_ref"].startswith("live:"))

    def test_live_scan_finds_files_that_the_index_never_saw(self):
        self.write("cold.md")
        self.grant()
        (self.projects / "later.md").write_text("新增", encoding="utf-8")
        report = self.index.search("later")
        self.assertEqual([item["name"] for item in report["candidates"]], ["later.md"])


class ReadTest(IndexFixture):
    def test_read_returns_slice_and_next_offset(self):
        self.write("long.md", "甲" * 120)
        self.indexed_grant()
        broker = self.broker()
        first = broker.read(self.ref_for("long"), limit=50)
        self.assertEqual(first["chars_returned"], 50)
        self.assertEqual(first["next_offset"], 50)
        self.assertTrue(first["truncated"])
        self.assertEqual(first["total_chars"], 120)
        second = broker.read(
            first["file_ref"], offset=first["next_offset"], limit=200
        )
        self.assertEqual(second["offset"], 50)
        self.assertFalse(second["truncated"])

    def test_read_decodes_gbk_text(self):
        self.write("gbk.txt", "中文GBK".encode("gbk"))
        self.indexed_grant()
        self.assertEqual(
            self.broker().read(self.ref_for("gbk"))["text"], "中文GBK"
        )

    def test_read_rejects_absolute_path_as_reference(self):
        self.write("plain.md")
        self.indexed_grant()
        with self.assertRaises(FileIndexError) as caught:
            self.broker().read(str(self.projects / "plain.md"))
        self.assertEqual(caught.exception.code, "file_ref_unresolved")

    def test_read_rejects_binary_and_unsupported_types(self):
        self.write("blob.txt", b"\x00\x01\x02abc")
        self.write("doc.docx", b"not really a docx")
        self.indexed_grant()
        with self.assertRaises(FileIndexError) as caught:
            self.broker().read(self.ref_for("doc"))
        self.assertEqual(caught.exception.code, "unsupported_type")
        # .txt 在文本类型名单里，但内容是二进制 → 另一条拒绝理由。
        (self.projects / "blob.txt").write_bytes(b"\x00\x01\x02")
        with self.assertRaises(FileIndexError) as caught:
            self.broker().read(self.ref_for("blob"))
        self.assertEqual(caught.exception.code, "binary")

    def test_read_marks_stale_when_file_disappears(self):
        self.write("temp.md")
        grant = self.indexed_grant()
        ref = self.ref_for("temp")
        (self.projects / "temp.md").unlink()
        with self.assertRaises(FileIndexError) as caught:
            self.broker().read(ref)
        self.assertEqual(caught.exception.code, "file_missing")
        self.assertEqual(
            self.store.get_file_index_state(grant.grant_id)["status"], INDEX_STALE
        )

    def test_read_refuses_when_identity_no_longer_matches(self):
        self.write("swap.md", "原文件")
        grant = self.indexed_grant()
        ref = self.ref_for("swap")
        self.raw(
            "UPDATE file_catalog SET file_id = ? WHERE file_ref = ?",
            ("999999999", ref),
        )
        with self.assertRaises(FileIndexError) as caught:
            self.broker().read(ref)
        self.assertEqual(caught.exception.code, "identity_changed")
        self.assertEqual(
            self.store.get_file_index_state(grant.grant_id)["status"], INDEX_STALE
        )

    def test_read_refuses_a_traversal_row_in_the_catalog(self):
        # 扫描器不会写出这种行，但索引是可以被别的路径填的：读取那一步必须自己挡住。
        self.write("inside.md")
        grant = self.grant()
        (self.root / "outside.txt").write_text("越界", encoding="utf-8")
        self.index.index_grant(grant.grant_id)
        self.store.upsert_file_catalog(
            grant.grant_id,
            [
                {
                    "name": "outside.txt",
                    "rel_path": "../outside.txt",
                    "parent_key": "",
                    "ext": ".txt",
                    "size_bytes": 6,
                    "mtime": "2026-11-01T00:00:00+00:00",
                }
            ],
        )
        ref = self.store.search_file_catalog(
            [grant.grant_id], term="outside"
        )[0]["file_ref"]
        with self.assertRaises(FileIndexError) as caught:
            self.broker().read(ref)
        self.assertEqual(caught.exception.code, "path_rejected")

    def test_read_needs_fs_read_capability(self):
        self.write("searchable.md")
        grant = self.indexed_grant((CAPABILITY_FS_SEARCH,))
        self.store.upsert_file_catalog(
            grant.grant_id,
            [
                {
                    "name": "searchable.md",
                    "rel_path": "searchable.md",
                    "parent_key": "",
                    "ext": ".md",
                    "size_bytes": 3,
                    "mtime": "2026-11-01T00:00:00+00:00",
                }
            ],
        )
        ref = self.store.search_file_catalog([grant.grant_id], term="searchable")[0][
            "file_ref"
        ]
        with self.assertRaises(FileIndexError) as caught:
            self.broker().read(ref)
        self.assertEqual(caught.exception.code, "file_ref_unresolved")
        self.assertIn("fs-read", str(caught.exception))

    def test_live_reference_resolves_through_the_guard(self):
        self.write("cold/deep note.md", "实时可读")
        self.grant()
        candidate = self.index.search("deep")["candidates"][0]
        read = self.broker("msg_live").read(candidate["file_ref"])
        self.assertEqual(read["text"], "实时可读")
        self.assertEqual(read["rel_path"], "cold/deep note.md")

    def test_live_reference_cannot_escape_the_grant(self):
        self.grant()
        (self.root / "outside.txt").write_text("越界", encoding="utf-8")
        with self.assertRaises(FileIndexError) as caught:
            self.broker().read("live:../outside.txt")
        self.assertEqual(caught.exception.code, "file_ref_unresolved")


class LeaseTest(IndexFixture):
    def test_one_lease_per_grant_and_capability(self):
        self.write("a.md")
        self.indexed_grant()
        broker = self.broker("msg_lease")
        ref = self.ref_for("a")
        broker.search("a")
        broker.read(ref)
        broker.search("a")
        broker.read(ref)
        leases = self.access.list_leases(task_id="msg_lease")
        self.assertEqual(len(leases), 2)
        self.assertEqual(
            sorted(item.capability for item in leases),
            [CAPABILITY_FS_READ, CAPABILITY_FS_SEARCH],
        )
        read_lease = [
            item for item in leases if item.capability == CAPABILITY_FS_READ
        ][0]
        self.assertEqual(read_lease.operations_used, 2)

    def test_budget_exhaustion_blocks_the_next_read(self):
        self.write("a.md")
        self.indexed_grant()
        ref = self.ref_for("a")
        broker = self.broker("msg_budget", max_operations=2)
        broker.read(ref)
        broker.read(ref)
        with self.assertRaises(AccessControlError):
            broker.read(ref)

    def test_search_skips_grants_out_of_budget(self):
        # 一个目录搜不了不等于全都搜不了：另一枚租约还得继续工作。
        self.write("a.md")
        grant = self.grant()
        other = self.root / "other"
        other.mkdir()
        (other / "b.md").write_text("y", encoding="utf-8")
        second = self.access.grant_directory(
            other, [CAPABILITY_FS_READ, CAPABILITY_FS_SEARCH]
        )
        self.index.index_grant(grant.grant_id)
        self.index.index_grant(second.grant_id)
        broker = self.broker("msg_multi", max_operations=1)
        broker.spend(grant.grant_id, CAPABILITY_FS_SEARCH)
        report = broker.search("")
        self.assertEqual([item["name"] for item in report["candidates"]], ["b.md"])
        self.assertTrue(report["refused"])

    def test_revocation_bites_in_the_middle_of_a_turn(self):
        self.write("a.md")
        grant = self.indexed_grant()
        ref = self.ref_for("a")
        broker = self.broker("msg_revoke")
        broker.read(ref)
        self.access.revoke_grant(grant.grant_id)
        with self.assertRaises(FileIndexError):
            broker.read(ref)

    def test_lease_budget_blocks_search_too(self):
        self.write("a.md")
        self.indexed_grant()
        broker = self.broker("msg_search_budget", max_operations=1)
        broker.search("a")
        with self.assertRaises(AccessControlError):
            broker.search("a")

    def test_close_releases_this_turns_leases(self):
        self.write("a.md")
        self.indexed_grant()
        broker = self.broker("msg_close")
        broker.search("a")
        broker.read(self.ref_for("a"))
        self.assertEqual(len(self.access.active_leases()), 2)
        self.assertEqual(broker.close(), 2)
        self.assertEqual(self.access.active_leases(), [])

    def test_broker_without_a_task_refuses(self):
        self.write("a.md")
        self.indexed_grant()
        broker = self.broker("")
        with self.assertRaises(AccessControlError):
            broker.search("a")


class RuntimeToolTest(IndexFixture):
    def runtime(self):
        return CompanionRuntime(
            self.store,
            OfflineLLMClient(),
            SearchService(),
            self.data,
            offline=True,
        )

    def test_file_tools_are_hidden_in_chat_and_quiet_modes(self):
        runtime = self.runtime()
        for mode in (AgentMode.CHAT, AgentMode.QUIET):
            registry = runtime._build_tool_registry(mode, "msg_x")
            names = {tool.name for tool in registry.available(mode)}
            self.assertNotIn("find_files", names)
            self.assertNotIn("read_file", names)
            result = registry.execute(
                "find_files", {"query": "a"}, mode
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.code, "mode_denied")

    def test_work_mode_offers_and_runs_the_file_tools(self):
        self.write("notes/meeting.md", "会议内容")
        runtime = self.runtime()
        runtime.select_workspace(self.projects)
        self.assertEqual(runtime.file_index.wait_for_index(15), 0)
        registry = runtime._build_tool_registry(AgentMode.WORK, "msg_work")
        names = {tool.name for tool in registry.available(AgentMode.WORK)}
        self.assertTrue({"find_files", "read_file"} <= names)
        found = registry.execute("find_files", {"query": "meeting"}, AgentMode.WORK)
        self.assertTrue(found.ok, found.content)
        self.assertIn("命中原因", found.content)
        refs = found.data["file_refs"]
        self.assertEqual(len(refs), 1)
        # The model receives content, not ToolResult.data.
        self.assertIn("file_ref=" + refs[0], found.content)
        read = registry.execute(
            "read_file", {"file_ref": refs[0]}, AgentMode.WORK
        )
        self.assertTrue(read.ok, read.content)
        self.assertIn("会议内容", read.content)
        # fs-search 和 fs-read 是两枚租约：检索用掉的次数不该让读取少一次。
        self.assertEqual(read.data["lease"]["capability"], CAPABILITY_FS_READ)
        self.assertEqual(
            read.data["lease"]["remaining_operations"], READ_LEASE_OPERATIONS - 1
        )

    def test_tool_output_never_leaks_the_absolute_directory(self):
        self.write("notes/deep.md", "内容")
        runtime = self.runtime()
        runtime.select_workspace(self.projects)
        self.assertEqual(runtime.file_index.wait_for_index(15), 0)
        registry = runtime._build_tool_registry(AgentMode.WORK, "msg_leak")
        found = registry.execute("find_files", {"query": "deep"}, AgentMode.WORK)
        read = registry.execute(
            "read_file", {"file_ref": found.data["file_refs"][0]}, AgentMode.WORK
        )
        for result in (found, read):
            self.assertNotIn(str(self.projects), result.content)

    def test_tool_text_says_the_hit_count_is_a_floor(self):
        for index in range(CANDIDATE_SCAN_LIMIT + 7):
            self.write("bulk/report_{:04d}.md".format(index))
        runtime = self.runtime()
        runtime.select_workspace(self.projects)
        self.assertEqual(runtime.file_index.wait_for_index(30), 0)
        registry = runtime._build_tool_registry(AgentMode.WORK, "msg_floor")
        found = registry.execute(
            "find_files", {"query": "report"}, AgentMode.WORK
        )
        self.assertTrue(found.ok, found.content)
        self.assertTrue(found.data["matched_capped"])
        self.assertIn("命中至少", found.content)
        self.assertNotIn("还有 {} 个未返回".format(
            found.data["matched"] - len(found.data["candidates"])), found.content)

    def test_find_files_without_any_grant_says_so(self):
        runtime = self.runtime()
        registry = runtime._build_tool_registry(AgentMode.WORK, "msg_none")
        result = registry.execute("find_files", {"query": "anything"}, AgentMode.WORK)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["candidates"], [])
        self.assertIn("没有命中", result.content)

    def test_read_file_requires_a_reference(self):
        runtime = self.runtime()
        registry = runtime._build_tool_registry(AgentMode.WORK, "msg_ref")
        result = registry.execute("read_file", {}, AgentMode.WORK)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "invalid_arguments")

    def test_rejected_read_reports_its_reason(self):
        self.write("blob.md", b"\x00\x01")
        runtime = self.runtime()
        runtime.select_workspace(self.projects)
        self.assertEqual(runtime.file_index.wait_for_index(15), 0)
        registry = runtime._build_tool_registry(AgentMode.WORK, "msg_deny")
        found = registry.execute("find_files", {"query": "blob"}, AgentMode.WORK)
        result = registry.execute(
            "read_file", {"file_ref": found.data["file_refs"][0]}, AgentMode.WORK
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "binary")

    def test_resume_file_index_schedules_cold_directories(self):
        self.write("cold.md")
        grant = self.grant()
        runtime = self.runtime()
        scheduled = runtime.resume_file_index()
        self.assertIn(grant.grant_id, scheduled)
        self.assertEqual(runtime.file_index.wait_for_index(15), 0)
        self.assertEqual(
            runtime.store.get_file_index_state(grant.grant_id)["status"], INDEX_READY
        )
        self.assertEqual(runtime.resume_file_index(), [])

    def test_resume_file_index_rescans_a_scan_that_never_finished(self):
        # running 在启动这一刻只可能是"上一个进程带着扫描线程一起没了"留下的半成品，
        # 而检索侧把 running 当可用状态——不重扫就会拿半份目录回答，并把没走到的
        # 文件说成不存在。
        self.write("a.md")
        self.write("b.md")
        grant = self.indexed_grant()
        runtime = self.runtime()
        runtime.store.save_file_index_state(
            grant.grant_id,
            {"status": INDEX_RUNNING, "files_indexed": 1, "started_at": "2026-11-05T00:00:00Z"},
        )
        self.assertEqual(runtime.resume_file_index(), [grant.grant_id])
        self.assertEqual(runtime.file_index.wait_for_index(15), 0)
        state = runtime.store.get_file_index_state(grant.grant_id)
        self.assertEqual(state["status"], INDEX_READY)
        self.assertEqual(state["files_indexed"], 2)
        self.assertEqual(runtime.resume_file_index(), [])

    def test_explicit_grants_work_without_a_selected_workspace(self):
        self.write("report.md", "设置页授权的内容")
        grant = self.access.grant_directory(
            self.projects,
            [
                CAPABILITY_FS_READ,
                CAPABILITY_FS_SEARCH,
                CAPABILITY_FS_WRITE,
                CAPABILITY_FS_DELETE,
            ],
        )
        runtime = self.runtime()
        self.assertIsNone(runtime.workspace())
        self.assertEqual(runtime.file_index.wait_for_index(15), 0)
        names = set(runtime.tool_names(AgentMode.WORK))
        self.assertTrue(
            {"find_files", "read_file", "write_file", "delete_file"}
            <= names
        )
        registry = runtime._build_tool_registry(AgentMode.WORK, "msg_settings")
        found = registry.execute(
            "find_files", {"query": "report"}, AgentMode.WORK
        )
        self.assertTrue(found.ok, found.content)
        self.assertEqual(found.data["candidates"][0]["grant_id"], grant.grant_id)
        self.assertEqual(
            found.data["write_grants"][0]["grant_id"], grant.grant_id
        )
        self.assertEqual(
            found.data["delete_grants"][0]["grant_id"], grant.grant_id
        )
        read = registry.execute(
            "read_file",
            {"file_ref": found.data["file_refs"][0]},
            AgentMode.WORK,
        )
        self.assertTrue(read.ok, read.content)
        self.assertIn("设置页授权的内容", read.content)
        write = registry.execute(
            "write_file",
            {
                "grant_id": grant.grant_id,
                "relative_path": "new.md",
                "content": "不需要工作区",
            },
            AgentMode.WORK,
        )
        self.assertTrue(write.ok, write.content)
        self.assertIsNotNone(write.proposal)
        self.assertFalse((self.projects / "new.md").exists())

    def test_workspace_grant_can_write_but_read_tools_still_work(self):
        # 工作区一直带 fs-write；S2 的只读工具不该因为这条就变成需要确认。
        self.write("note.md", "工作区")
        runtime = self.runtime()
        runtime.select_workspace(self.projects)
        self.assertEqual(runtime.file_index.wait_for_index(15), 0)
        registry = runtime._build_tool_registry(AgentMode.WORK, "msg_ws")
        self.assertTrue(
            registry.execute("find_files", {"query": "note"}, AgentMode.WORK).ok
        )
        self.assertEqual(registry.risks()["find_files"], RiskLevel.FS_READ)


class DoctorReportTest(IndexFixture):
    def test_report_describes_progress_and_is_read_only(self):
        self.write("a.md")
        self.write("node_modules/x.js")
        grant = self.indexed_grant()
        before = self.raw(
            "SELECT COUNT(*) FROM file_index_state"
        )[0][0]
        report = index_state_report(self.data / "companion.db")
        self.assertTrue(report["available"])
        self.assertEqual(report["searchable_count"], 1)
        self.assertEqual(report["catalog_rows"], 1)
        self.assertEqual(report["deleted_rows"], 0)
        self.assertEqual(report["never_indexed"], 0)
        self.assertEqual(report["failed"], 0)
        self.assertEqual(report["missing_directories"], 0)
        entry = report["directories"][0]
        self.assertEqual(entry["grant_id"], grant.grant_id)
        self.assertEqual(entry["status"], INDEX_READY)
        self.assertTrue(entry["directory_present"])
        self.assertGreaterEqual(entry["files_skipped"], 1)
        self.assertEqual(
            self.raw("SELECT COUNT(*) FROM file_index_state")[0][0], before
        )
        self.assertEqual(int(self.raw("PRAGMA user_version")[0][0]), SCHEMA_VERSION)

    def test_report_flags_cold_and_unusable_directories(self):
        self.write("a.md")
        self.grant()
        volume = self.root / "volume"
        volume.mkdir()
        (volume / "b.md").write_text("x", encoding="utf-8")
        self.access.grant_directory(volume, [CAPABILITY_FS_READ, CAPABILITY_FS_SEARCH])
        report = index_state_report(self.data / "companion.db")
        self.assertEqual(report["never_indexed"], 2)
        self.assertEqual(report["catalog_rows"], 0)
        shutil.rmtree(volume)
        after = index_state_report(self.data / "companion.db")
        self.assertEqual(after["missing_directories"], 1)

    def test_report_on_a_pre_v14_database_explains_itself(self):
        self.raw("DROP TABLE file_catalog;")
        report = index_state_report(self.data / "companion.db")
        self.assertFalse(report["available"])
        self.assertIn("v14", report["reason"])

    def test_report_does_not_nag_about_the_agents_own_data_directory(self):
        self.grant(path=self.data)
        report = index_state_report(self.data / "companion.db")
        entry = report["directories"][0]
        self.assertTrue(entry["own_data"])
        self.assertFalse(entry["searchable"])
        self.assertEqual(report["searchable_count"], 0)
        self.assertEqual(report["never_indexed"], 0)


class IndexCapTest(unittest.TestCase):
    def test_indexed_path_depth_stays_under_the_guard_limit(self):
        # 扫描深度上限必须严格小于路径规则的上限，否则索引会产出读不到的文件。
        self.assertLessEqual(INDEX_MAX_DEPTH, MAX_PATH_DEPTH)
