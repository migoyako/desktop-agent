"""S1 — directory grants, task leases and the workspace compatibility layer."""

from datetime import datetime, timedelta, timezone
import json
import tempfile
import threading
import unittest
from pathlib import Path
import zipfile

from agent_framework.companion.access_control import (
    GRANT_ACTIVE,
    GRANT_REVOKED,
    LEASE_ACTIVE,
    LEASE_RELEASED,
    LEASE_REVOKED,
    MAX_DIRECTORY_GRANTS,
    MAX_LEASE_OPERATIONS,
    MAX_LEASE_RESOURCES,
    MAX_LEASE_TTL_SECONDS,
    AccessControlError,
    AccessControlService,
    normalize_capabilities,
    state_report,
)
from agent_framework.companion.file_actions import NoteGuard
from agent_framework.companion.models import AgentMode
from agent_framework.companion.path_safety import (
    PathSafetyError,
    WorkspaceGuard,
    check_grantable_directory,
    path_key,
    resource_identity,
)
from agent_framework.companion.store import (
    LEGACY_WORKSPACE_CAPABILITIES,
    SCHEMA_VERSION,
    CompanionStore,
)


def utc_stamp(seconds_from_now: int) -> str:
    moment = datetime.now(timezone.utc) + timedelta(seconds=seconds_from_now)
    return moment.strftime("%Y-%m-%dT%H:%M:%S+00:00")


class GrantFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_dir = self.root / "data"
        self.store = CompanionStore(self.data_dir / "companion.db")
        self.access = AccessControlService(self.store)
        self.projects = self.root / "projects"
        self.projects.mkdir()
        self.secrets = self.root / ".ssh"
        self.secrets.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def raw(self, sql, params=()):
        with self.store._connection() as connection:
            return connection.execute(sql, params).fetchall()

    def rows(self, table):
        return [dict(row) for row in self.raw("SELECT * FROM {}".format(table))]

    def write_v12_workspace_row(self, path, enabled=1):
        """Put back the single pre-v13 row, as an older database would have it."""
        self.raw(
            """
            INSERT INTO workspace_grants(id, path, created_at, enabled)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                path = excluded.path, enabled = excluded.enabled
            """,
            (str(path), utc_stamp(-3600), enabled),
        )


class SchemaTest(GrantFixture):
    def test_fresh_database_has_grants_and_leases(self):
        self.assertEqual(SCHEMA_VERSION, 17)
        tables = {
            row[0]
            for row in self.raw(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertTrue({"access_grants", "grant_leases"} <= tables)
        indexes = {
            row[0]
            for row in self.raw(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        self.assertTrue(
            {
                "idx_access_grants_path",
                "idx_access_grants_status",
                "idx_access_grants_workspace_selected",
                "idx_grant_leases_task",
                "idx_grant_leases_active",
            }
            <= indexes
        )

    def test_v12_database_gains_grants_and_promotes_the_workspace(self):
        workspace = str(self.projects)
        self.store.ensure_session("main", AgentMode.CHAT)
        self.store.add_message("main", "user", "旧数据", AgentMode.CHAT)
        message_before = len(self.store.list_messages("main", 10))
        with self.store._connection() as connection:
            connection.executescript(
                "DROP TABLE grant_leases; DROP TABLE access_grants;"
            )
        # A real v12 database carries the single enabled workspace row.
        self.write_v12_workspace_row(workspace)
        self.raw("PRAGMA user_version = 12")
        reopened = CompanionStore(self.data_dir / "companion.db")
        self.assertEqual(
            int(self.raw("PRAGMA user_version")[0][0]), SCHEMA_VERSION
        )
        legacy = reopened.get_legacy_workspace_grant()
        self.assertIsNotNone(legacy)
        self.assertEqual(legacy["path"], workspace)
        self.assertEqual(
            legacy["capabilities"], sorted(LEGACY_WORKSPACE_CAPABILITIES)
        )
        self.assertFalse(
            {"fs-delete", "fs-export"} <= set(legacy["capabilities"])
        )
        self.assertEqual(
            int(self.raw("SELECT enabled FROM workspace_grants WHERE id = 1")[0][0]),
            0,
        )
        self.assertEqual(len(reopened.list_messages("main", 10)), message_before)
        self.assertTrue(Path(reopened.migration_backup_path).is_file())

    def test_promotion_runs_once_and_does_not_resurrect_a_revoked_workspace(self):
        self.write_v12_workspace_row(self.projects)
        CompanionStore(self.data_dir / "companion.db")
        first = self.store.get_legacy_workspace_grant()["grant_id"]
        # A second launch with the old row still flagged enabled reuses the row
        # instead of adding another grant for the same directory.
        self.write_v12_workspace_row(self.projects)
        CompanionStore(self.data_dir / "companion.db")
        legacy = self.rows("access_grants")
        self.assertEqual(len([row for row in legacy if row["legacy_workspace"]]), 1)
        self.assertEqual(
            [row for row in legacy if row["legacy_workspace"]][0]["grant_id"], first
        )

        self.store.clear_workspace()
        self.write_v12_workspace_row(self.projects)
        CompanionStore(self.data_dir / "companion.db")
        self.assertIsNone(self.store.get_workspace())
        self.assertEqual(
            int(self.raw("SELECT enabled FROM workspace_grants WHERE id = 1")[0][0]),
            0,
        )

    def test_pre_v13_archive_import_keeps_the_authorization(self):
        """A v13 database restored over an older archive self-heals the workspace."""
        self.write_v12_workspace_row(self.projects)
        # No migration runs here: the database already sits at v13.
        CompanionStore(self.data_dir / "companion.db")
        self.assertEqual(self.store.get_workspace(), str(self.projects))


class GrantingTest(GrantFixture):
    def test_grant_replaces_the_capability_set_of_the_same_directory(self):
        grant = self.access.grant_directory(self.projects, ["fs-read", "fs-search"])
        self.assertEqual(grant.status, GRANT_ACTIVE)
        self.assertEqual(grant.canonical_root, str(self.projects.resolve()))
        again = self.access.grant_directory(self.projects, ["fs-read"])
        self.assertEqual(again.grant_id, grant.grant_id)
        self.assertEqual(again.capabilities, ("fs-read",))
        self.assertEqual(len(self.access.list_grants()), 1)

    def test_unknown_or_empty_capabilities_are_refused(self):
        with self.assertRaises(AccessControlError) as caught:
            normalize_capabilities(["fs-read", "fs-launch"])
        self.assertEqual(caught.exception.code, "unknown_capability")
        with self.assertRaises(AccessControlError) as empty:
            normalize_capabilities([])
        self.assertEqual(empty.exception.code, "no_capability")
        self.assertEqual(
            normalize_capabilities('["fs-write", "fs-read", "fs-read"]'),
            ("fs-read", "fs-write"),
        )
        with self.assertRaises(AccessControlError):
            self.access.grant_directory(self.projects, ["fs-shred"])

    def test_credential_directories_need_an_explicit_override(self):
        with self.assertRaises(PathSafetyError) as refused:
            self.access.grant_directory(self.secrets, ["fs-read"])
        self.assertIn(".ssh", str(refused.exception))
        self.assertEqual(self.store.count_access_grants(GRANT_ACTIVE), 0)

        granted = self.access.grant_directory(
            self.secrets, ["fs-read"], allow_sensitive=True
        )
        self.assertTrue(granted.sensitivity_override)
        projects = self.access.grant_directory(self.projects, ["fs-read"])
        self.assertFalse(projects.sensitivity_override)
        self.assertFalse(self.access.find_grant(self.projects).sensitivity_override)

    def test_home_directory_is_not_grantable_as_a_whole(self):
        from pathlib import Path as P

        with self.assertRaises(PathSafetyError):
            check_grantable_directory(P.home())
        check_grantable_directory(P.home(), allow_sensitive=True)

    def test_unusable_roots_are_refused(self):
        cases = [
            str(self.projects / "missing"),
            "relative/dir",
            "\\\\server\\share",
            Path(self.projects.anchor),
            self.projects / "notes.txt",
        ]
        (self.projects / "notes.txt").write_text("x", encoding="utf-8")
        for case in cases:
            with self.subTest(case=str(case)):
                with self.assertRaises(PathSafetyError):
                    self.access.grant_directory(case, ["fs-read"])

    def test_symlinked_root_is_refused(self):
        target = self.root / "target"
        target.mkdir()
        link = self.root / "link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("本机不允许创建符号链接")
        with self.assertRaises(PathSafetyError):
            self.access.grant_directory(link, ["fs-read"])

    def test_grant_count_is_capped(self):
        directories = []
        for index in range(MAX_DIRECTORY_GRANTS + 1):
            folder = self.root / "dir{}".format(index)
            folder.mkdir()
            directories.append(folder)
        for folder in directories[:MAX_DIRECTORY_GRANTS]:
            self.access.grant_directory(folder, ["fs-read"])
        with self.assertRaises(AccessControlError) as caught:
            self.access.grant_directory(directories[-1], ["fs-read"])
        self.assertEqual(caught.exception.code, "grant_limit")
        # Re-granting an already authorized directory never trips the cap.
        self.access.grant_directory(directories[0], ["fs-read", "fs-search"])

    def test_revoke_cascades_to_live_leases(self):
        grant = self.access.grant_directory(self.projects, ["fs-read", "fs-write"])
        lease = self.access.issue_lease(grant.grant_id, "task_a", "fs-write")
        self.access.revoke_grant(grant.grant_id)
        closed = self.access.get_lease(lease.lease_id)
        self.assertEqual(closed.status, LEASE_REVOKED)
        self.assertTrue(closed.released_at)
        again = self.access.revoke_grant(grant.grant_id)
        self.assertEqual(again.status, GRANT_REVOKED)
        with self.assertRaises(AccessControlError) as missing:
            self.access.revoke_grant("grant_nope")
        self.assertEqual(missing.exception.code, "grant_missing")
        usable, reason = self.access.grant_for_capability(
            self.projects, "fs-write"
        )
        self.assertIsNone(usable)
        self.assertIn("已撤销", reason)

    def test_grant_for_capability_reports_what_is_missing(self):
        grant = self.access.grant_directory(self.projects, ["fs-read"])
        usable, reason = self.access.grant_for_capability(self.projects, "fs-write")
        self.assertIsNone(usable)
        self.assertIn("fs-write", reason)
        usable, reason = self.access.grant_for_capability(
            self.root / "other", "fs-read"
        )
        self.assertIsNone(usable)
        self.assertIn("还没有授权", reason)
        with self.assertRaises(AccessControlError):
            self.access.grant_for_capability(self.projects, "fs-launch")
        self.store.touch_access_grant(grant.grant_id)
        self.assertTrue(self.store.get_access_grant(grant.grant_id)["last_used_at"])

    def test_only_one_row_carries_the_workspace_flag(self):
        self.store.set_workspace(str(self.projects))
        other = self.root / "other"
        other.mkdir()
        self.store.set_workspace(str(other))
        flagged = [
            row
            for row in self.rows("access_grants")
            if row["legacy_workspace"] and row["status"] == GRANT_ACTIVE
        ]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["path"], str(other))
        self.assertEqual(self.store.get_workspace(), str(other))
        self.store.clear_workspace()
        self.assertIsNone(self.store.get_workspace())
        self.store.clear_workspace()  # idempotent

    def test_selecting_workspace_does_not_downgrade_an_explicit_grant(self):
        grant = self.access.grant_directory(
            self.projects,
            ["fs-read", "fs-search", "fs-write", "fs-delete"],
        )
        selected = self.store.set_workspace(str(self.projects))
        self.assertEqual(selected["grant_id"], grant.grant_id)
        self.assertTrue(selected["workspace_selected"])
        self.assertFalse(selected["legacy_workspace"])
        self.assertEqual(
            set(selected["capabilities"]),
            {"fs-read", "fs-search", "fs-write", "fs-delete"},
        )
        self.assertEqual(self.store.get_workspace(), str(self.projects))
        self.assertIsNotNone(
            self.access.grant_for_capability(self.projects, "fs-delete")[0]
        )

    def test_clearing_workspace_keeps_the_explicit_grant(self):
        grant = self.access.grant_directory(
            self.projects, ["fs-read", "fs-search", "fs-write", "fs-delete"]
        )
        self.store.set_workspace(str(self.projects))
        self.store.clear_workspace()
        self.assertIsNone(self.store.get_workspace())
        current = self.access.get_grant(grant.grant_id)
        self.assertEqual(current.status, GRANT_ACTIVE)
        self.assertFalse(current.workspace_selected)
        self.assertFalse(current.legacy_workspace)
        self.assertEqual(
            set(current.capabilities),
            {"fs-read", "fs-search", "fs-write", "fs-delete"},
        )

    def test_regranting_the_selected_workspace_enables_generic_actions(self):
        selected = self.store.set_workspace(str(self.projects))
        self.assertTrue(selected["legacy_workspace"])
        explicit = self.access.grant_directory(
            self.projects,
            ["fs-read", "fs-search", "fs-write", "fs-delete"],
        )
        self.assertEqual(explicit.grant_id, selected["grant_id"])
        self.assertTrue(explicit.workspace_selected)
        self.assertFalse(explicit.legacy_workspace)
        self.assertEqual(
            self.access.grants_for_capability("fs-delete", exclude_legacy=True),
            [explicit],
        )
        self.assertEqual(self.store.get_workspace(), str(self.projects))


class IdentityTest(GrantFixture):
    def test_identity_is_bound_on_first_use(self):
        self.raw(
            """
            INSERT INTO access_grants(
                grant_id, resource_kind, path, path_key, capabilities, status,
                created_at, updated_at
            ) VALUES ('grant_offline', 'directory', ?, ?, '["fs-read"]', 'active',
                      '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
            """,
            (str(self.projects), path_key(self.projects)),
        )
        grant = self.access.get_grant("grant_offline")
        self.assertFalse(grant.volume_id)
        usable, reason = self.access.check_identity(grant)
        self.assertTrue(usable, reason)
        bound = self.access.get_grant("grant_offline")
        current = resource_identity(self.projects)
        self.assertEqual(bound.volume_id, current["device"])
        self.assertEqual(bound.file_id, current["file_id"])

    def test_moved_directory_requires_reauthorization(self):
        grant = self.access.grant_directory(self.projects, ["fs-read"])
        self.access.check_identity(grant)
        lease = self.access.issue_lease(grant.grant_id, "task_a", "fs-read")
        self.raw(
            "UPDATE access_grants SET volume_id = '999999' WHERE grant_id = ?",
            (grant.grant_id,),
        )
        stale = self.access.get_grant(grant.grant_id)
        usable, reason = self.access.check_identity(stale)
        self.assertFalse(usable)
        self.assertIn("身份已变化", reason)
        usable, reason = self.access.grant_for_capability(self.projects, "fs-read")
        self.assertIsNone(usable)
        with self.assertRaises(AccessControlError) as caught:
            self.access.use_lease(lease.lease_id)
        self.assertEqual(caught.exception.code, "grant_identity_changed")
        # Re-authorizing the same path re-binds the identity instead of stranding it.
        self.access.grant_directory(self.projects, ["fs-read"])
        usable, reason = self.access.grant_for_capability(self.projects, "fs-read")
        self.assertIsNotNone(usable)
        self.assertEqual(reason, "")

    def test_missing_directory_is_refused_but_stays_revokable(self):
        gone = self.root / "gone"
        gone.mkdir()
        grant = self.access.grant_directory(gone, ["fs-read"])
        gone.rmdir()
        usable, reason = self.access.check_identity(grant)
        self.assertFalse(usable)
        self.assertIn("重新授权", reason)
        with self.assertRaises(AccessControlError) as caught:
            self.access.issue_lease(grant.grant_id, "task_a", "fs-read")
        self.assertEqual(caught.exception.code, "grant_identity_changed")


class LeaseTest(GrantFixture):
    def setUp(self):
        super().setUp()
        self.grant = self.access.grant_directory(
            self.projects, ["fs-read", "fs-write", "fs-search"]
        )

    def test_lease_bounds_are_enforced(self):
        cases = [
            (dict(ttl_seconds=0), "bad_ttl"),
            (dict(ttl_seconds=MAX_LEASE_TTL_SECONDS + 1), "bad_ttl"),
            (dict(max_operations=0), "bad_operations"),
            (dict(max_operations=MAX_LEASE_OPERATIONS + 1), "bad_operations"),
            (dict(task_id="  "), "no_task"),
            (dict(capability="fs-delete"), "capability_not_granted"),
            (dict(capability="fs-launch"), "unknown_capability"),
        ]
        for kwargs, code in cases:
            params = dict(
                grant_id=self.grant.grant_id,
                task_id="task_a",
                capability="fs-read",
            )
            params.update(kwargs)
            with self.subTest(**kwargs):
                with self.assertRaises(AccessControlError) as caught:
                    self.access.issue_lease(**params)
                self.assertEqual(caught.exception.code, code)
        with self.assertRaises(AccessControlError) as missing:
            self.access.issue_lease("grant_nope", "task_a", "fs-read")
        self.assertEqual(missing.exception.code, "grant_missing")
        with self.assertRaises(AccessControlError) as crowded:
            self.access.issue_lease(
                self.grant.grant_id,
                "task_a",
                "fs-read",
                resource_ids=[
                    "r{}".format(index) for index in range(MAX_LEASE_RESOURCES + 1)
                ],
            )
        self.assertEqual(crowded.exception.code, "too_many_resources")

    def test_budget_expiry_and_preconditions(self):
        lease = self.access.issue_lease(
            self.grant.grant_id, "task_a", "fs-write", max_operations=2
        )
        self.assertTrue(lease.nonce)
        self.assertEqual(lease.resource_ids, ())
        first = self.access.use_lease(lease.lease_id)
        self.assertEqual(first.operations_used, 1)
        self.assertEqual(first.remaining_operations(), 1)
        self.access.use_lease(lease.lease_id)
        with self.assertRaises(AccessControlError) as spent:
            self.access.use_lease(lease.lease_id)
        self.assertEqual(spent.exception.code, "lease_exhausted")

        expired = self.store.add_grant_lease(
            grant_id=self.grant.grant_id,
            task_id="task_b",
            capability="fs-read",
            expires_at=utc_stamp(-1),
            nonce="n",
        )
        with self.assertRaises(AccessControlError) as late:
            self.access.use_lease(expired["lease_id"])
        self.assertEqual(late.exception.code, "lease_expired")

        with self.assertRaises(AccessControlError) as missing:
            self.access.use_lease("lease_nope")
        self.assertEqual(missing.exception.code, "lease_missing")
        self.assertIsNone(self.access.get_lease("lease_nope"))

    def test_task_and_digest_binding(self):
        lease = self.access.issue_lease(
            self.grant.grant_id, "task_a", "fs-read", confirm_digest="digest_a"
        )
        with self.assertRaises(AccessControlError) as wrong_task:
            self.access.use_lease(lease.lease_id, task_id="task_b")
        self.assertEqual(wrong_task.exception.code, "lease_task_mismatch")
        with self.assertRaises(AccessControlError) as wrong_digest:
            self.access.use_lease(lease.lease_id, confirm_digest="digest_b")
        self.assertEqual(wrong_digest.exception.code, "lease_digest_mismatch")
        self.access.use_lease(lease.lease_id, task_id="task_a", confirm_digest="digest_a")

    def test_capability_revoked_while_running_stops_the_lease(self):
        lease = self.access.issue_lease(self.grant.grant_id, "task_a", "fs-write")
        self.access.grant_directory(self.grant.path, ["fs-read"])
        with self.assertRaises(AccessControlError) as caught:
            self.access.use_lease(lease.lease_id)
        self.assertEqual(caught.exception.code, "capability_not_granted")

    def test_release_task_and_active_view(self):
        first = self.access.issue_lease(self.grant.grant_id, "task_a", "fs-read")
        second = self.access.issue_lease(self.grant.grant_id, "task_b", "fs-read")
        stale = self.store.add_grant_lease(
            grant_id=self.grant.grant_id,
            task_id="task_c",
            capability="fs-read",
            expires_at=utc_stamp(-5),
            nonce="n",
        )
        live = {item.lease_id for item in self.access.active_leases()}
        self.assertEqual(live, {first.lease_id, second.lease_id})
        self.assertEqual(
            {item.task_id for item in self.access.list_leases(grant_id=self.grant.grant_id)},
            {"task_a", "task_b", "task_c"},
        )
        # A lease nobody closed still reads active in the table; effective_status
        # is the answer a caller has to trust.
        self.assertEqual(self.access.get_lease(stale["lease_id"]).status, LEASE_ACTIVE)
        self.assertEqual(
            self.access.get_lease(stale["lease_id"]).effective_status(), "expired"
        )
        self.assertEqual(self.access.release_task("task_a"), 1)
        self.assertEqual(self.access.get_lease(first.lease_id).status, LEASE_RELEASED)
        self.assertRegex(
            self.access.get_lease(first.lease_id).released_at,
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
        )
        self.assertEqual(
            self.access.get_lease(first.lease_id).effective_status(), LEASE_RELEASED
        )
        self.assertEqual(self.access.get_lease(second.lease_id).status, LEASE_ACTIVE)
        self.assertEqual(self.access.release_lease(second.lease_id), 1)
        self.assertEqual(self.access.release_task("task_a"), 0)
        # Filtering by grant catches only what is still open for that grant.
        self.assertEqual(
            self.store.finish_grant_leases(LEASE_RELEASED, grant_id=self.grant.grant_id),
            1,
        )
        self.assertEqual(
            self.access.get_lease(stale["lease_id"]).status, LEASE_RELEASED
        )
        with self.assertRaises(ValueError):
            self.store.finish_grant_leases(LEASE_RELEASED)
        with self.assertRaises(ValueError):
            self.store.finish_grant_leases("cancelled", task_id="task_a")

    def test_concurrent_callers_cannot_both_spend_the_last_operation(self):
        lease = self.access.issue_lease(
            self.grant.grant_id, "task_a", "fs-read", max_operations=3
        )
        results = []
        barrier = threading.Barrier(6)

        def spend():
            barrier.wait()
            try:
                self.access.use_lease(lease.lease_id)
                results.append(True)
            except AccessControlError:
                results.append(False)

        threads = [threading.Thread(target=spend) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results.count(True), 3)
        final = self.access.get_lease(lease.lease_id)
        self.assertEqual(final.operations_used, 3)
        self.assertEqual(final.remaining_operations(), 0)

    def test_revoke_grant_during_a_task_blocks_the_next_operation(self):
        lease = self.access.issue_lease(self.grant.grant_id, "task_a", "fs-write")
        self.access.use_lease(lease.lease_id)
        self.access.revoke_grant(self.grant.grant_id)
        with self.assertRaises(AccessControlError) as caught:
            self.access.use_lease(lease.lease_id)
        self.assertIn(caught.exception.code, {"grant_revoked", "lease_closed"})


class WorkspaceCompatTest(GrantFixture):
    def test_runtime_workspace_round_trips_through_grants(self):
        from agent_framework.companion.runtime import CompanionRuntime

        runtime = CompanionRuntime(
            store=self.store,
            llm_client=None,
            search_service=None,
            data_dir=self.data_dir,
            session_id="main",
            offline=True,
        )
        self.assertIsNone(runtime.workspace())
        chosen = runtime.select_workspace(str(self.projects))
        self.assertEqual(chosen, self.projects.resolve())
        self.assertEqual(runtime.workspace(), str(self.projects.resolve()))
        grant = self.access.find_grant(self.projects.resolve())
        self.assertTrue(grant.legacy_workspace)
        self.assertEqual(grant.source, "workspace")
        self.assertEqual(
            sorted(grant.capabilities),
            sorted(LEGACY_WORKSPACE_CAPABILITIES),
        )
        runtime.clear_workspace()
        self.assertIsNone(runtime.workspace())

        with self.assertRaises(PathSafetyError):
            runtime.select_workspace(str(self.secrets))
        self.assertIsNone(runtime.workspace())
        runtime.select_workspace(str(self.secrets), allow_sensitive=True)
        override = self.access.find_grant(self.secrets.resolve())
        self.assertEqual(override.path, str(self.secrets.resolve()))
        self.assertTrue(override.sensitivity_override)

    def test_workspace_helpers_still_accept_a_raw_path_string(self):
        self.store.set_workspace(str(self.projects))
        self.assertEqual(self.store.get_workspace(), str(self.projects))
        self.store.set_workspace("{}\\".format(str(self.projects).rstrip("\\")))
        self.assertEqual(
            path_key(self.store.get_workspace()), path_key(self.projects)
        )


class PendingProposalTest(GrantFixture):
    def test_revoking_the_workspace_blocks_a_queued_proposal(self):
        from agent_framework.companion.models import ProposalStatus, SearchResult
        from agent_framework.companion.runtime import CompanionRuntime
        from agent_framework.companion.search import SearchService
        from agent_framework.llm import OfflineLLMClient

        class StubSearch:
            def search(self, query, limit=5):
                return [
                    SearchResult(
                        title="Example result",
                        url="https://example.com/result",
                        snippet="A short search summary.",
                    )
                ]

        runtime = CompanionRuntime(
            store=self.store,
            llm_client=OfflineLLMClient(),
            search_service=SearchService(StubSearch()),
            data_dir=self.data_dir,
            session_id="main",
            offline=True,
        )
        runtime.select_workspace(str(self.projects))
        runtime.apply_settings({"tool_permission": "strict"})
        runtime.set_mode(AgentMode.WORK)
        result = runtime.handle_message(
            "搜索 Python Agent 并保存为笔记", AgentMode.WORK
        )
        proposal = result["proposal"]
        self.assertIsNotNone(proposal)
        runtime.apply_settings({"tool_permission": "balanced"})

        self.store.clear_workspace()
        rejected = runtime.confirm_proposal(proposal.action_id, AgentMode.WORK)
        self.assertEqual(rejected["receipt"].status, "not_executed")
        self.assertEqual(
            rejected["receipt"].result["code"], "workspace_missing"
        )
        self.assertEqual(
            self.store.get_proposal(proposal.action_id).status,
            ProposalStatus.NOT_EXECUTED,
        )
        self.assertEqual(list(self.projects.rglob("*.md")), [])

    def test_note_service_refuses_without_an_active_grant(self):
        from agent_framework.companion.file_actions import NoteActionService

        service = NoteActionService(self.store, self.data_dir)
        self.store.set_workspace(str(self.projects))
        proposal = service.create_proposal("session", "pending note", "# body")
        self.store.clear_workspace()
        with self.assertRaises(PathSafetyError) as caught:
            service.create_proposal("session", "second note", "# body")
        self.assertIn("授权工作目录", str(caught.exception))
        # The queued proposal still names a directory the user has withdrawn.
        usable, reason = self.access.grant_for_capability(
            self.projects, "fs-write"
        )
        self.assertIsNone(usable)
        self.assertIn("已撤销", reason)
        self.assertFalse(Path(proposal.target_summary).exists())


class PathRuleTest(GrantFixture):
    def test_note_guard_narrows_the_generic_containment(self):
        guard = WorkspaceGuard(self.projects)
        note = NoteGuard(self.projects)
        self.assertEqual(guard.resolve("a/b.txt"), note.resolve("a/b.txt"))
        self.assertEqual(guard.resolve("script.py"), self.projects / "script.py")
        with self.assertRaises(PathSafetyError):
            note.resolve("script.py")

    def test_containment_rules(self):
        guard = WorkspaceGuard(self.projects)
        (self.projects / "notes").mkdir()
        cases = [
            "../outside.md",
            str(self.root / "absolute.md"),
            "C:/Windows/win.md",
            "notes/../notes/a.md",
            "notes/./a.md",
            "notes//a.md",
            "notes/.git/config",
            "notes/.env",
            "notes/node_modules/a.md",
            "notes/con.md",
            "notes/trailing.md ",
            "notes/bad<name>.md",
            "a/b/c/d/e/f/g/h/i.md",
        ]
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(PathSafetyError):
                    guard.resolve(case)
        self.assertEqual(
            guard.resolve("notes\\deep\\a.md"),
            self.projects / "notes" / "deep" / "a.md",
        )

    def test_blocked_inner_directory_cannot_be_created(self):
        guard = NoteGuard(self.projects)
        with self.assertRaises(PathSafetyError):
            guard.resolve("notes/.ssh/id.md")


class MaintenanceWiringTest(GrantFixture):
    def test_grant_tables_are_covered_by_export_purge_and_integrity(self):
        from agent_framework.companion.maintenance import (
            EXPORT_TABLES,
            EXPECTED_INDEXES,
            PURGE_ALL_TABLES,
            export_data,
            integrity_report,
            purge_all,
        )

        for name in ("access_grants", "grant_leases"):
            self.assertIn(name, EXPORT_TABLES)
            self.assertIn(name, PURGE_ALL_TABLES)
            self.assertLess(
                PURGE_ALL_TABLES.index("grant_leases"),
                PURGE_ALL_TABLES.index("access_grants"),
            )
        for index in (
            "idx_access_grants_path",
            "idx_access_grants_status",
            "idx_grant_leases_task",
            "idx_grant_leases_active",
        ):
            self.assertIn(index, EXPECTED_INDEXES)

        self.store.ensure_session("main", AgentMode.CHAT)
        self.access.grant_directory(self.projects, ["fs-read"])
        self.access.issue_lease(self.grant_id_for(self.projects), "task_a", "fs-read")
        report = integrity_report(self.data_dir / "companion.db")
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["missing_indexes"], [])

        result = export_data(self.data_dir, output_path=self.root / "export.zip")
        self.assertEqual(result["counts"]["access_grants"], 1)
        self.assertEqual(result["counts"]["grant_leases"], 1)
        with zipfile.ZipFile(result["path"]) as archive:
            document = json.loads(archive.read("export.json").decode("utf-8"))
        self.assertEqual(document["database"]["schema_version"], SCHEMA_VERSION)
        self.assertEqual(
            [row["path"] for row in document["tables"]["access_grants"]],
            [str(self.projects.resolve())],
        )
        self.assertEqual(len(document["tables"]["grant_leases"]), 1)

        counts = purge_all(self.store, data_dir=self.data_dir)["counts"]
        self.assertEqual(counts["access_grants"], 1)
        self.assertEqual(counts["grant_leases"], 1)
        self.assertEqual(self.store.count_access_grants(GRANT_ACTIVE), 0)
        self.assertEqual(self.access.list_leases(), [])

    def grant_id_for(self, path):
        return self.access.find_grant(path).grant_id


class DoctorReportTest(GrantFixture):
    def test_state_report_describes_grants_without_touching_them(self):
        grant = self.access.grant_directory(self.projects, ["fs-read"])
        loose = self.root / "loose"
        loose.mkdir()
        self.raw(
            """
            INSERT INTO access_grants(
                grant_id, resource_kind, path, path_key, capabilities, status,
                created_at, updated_at
            ) VALUES ('grant_loose', 'directory', ?, ?, '["fs-read"]', 'active',
                      '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
            """,
            (str(loose), path_key(loose)),
        )
        self.access.issue_lease(grant.grant_id, "task_a", "fs-read")
        self.store.add_grant_lease(
            grant_id=grant.grant_id,
            task_id="task_b",
            capability="fs-read",
            expires_at=utc_stamp(-1),
            nonce="n",
        )
        report = state_report(self.data_dir / "companion.db")
        self.assertTrue(report["available"], report)
        self.assertEqual(report["active_count"], 2)
        self.assertEqual(report["live_lease_count"], 1)
        self.assertEqual(report["expired_lease_count"], 1)
        self.assertEqual(report["drifted_count"], 0)
        self.assertFalse(report["override_count"])
        self.assertEqual(report["workspace"], "")
        self.assertEqual(
            [item["identity_bound"] for item in report["grants"] if item["grant_id"] == "grant_loose"],
            [False],
        )
        # A read-only self-check must not bind the identity it is only reporting.
        row = [item for item in self.rows("access_grants") if item["grant_id"] == "grant_loose"][0]
        self.assertEqual(row["volume_id"], "")

    def test_report_flags_drift_and_override(self):
        self.store.set_workspace(str(self.projects))
        override = self.access.grant_directory(
            self.secrets, ["fs-read"], allow_sensitive=True
        )
        self.access.check_identity(override)
        self.raw(
            "UPDATE access_grants SET volume_id = '777' WHERE grant_id = ?",
            (self.access.find_grant(self.projects).grant_id,),
        )
        report = state_report(self.data_dir / "companion.db")
        self.assertEqual(report["override_count"], 1)
        self.assertEqual(report["drifted_count"], 1)
        self.assertEqual(report["workspace"], self.store.get_workspace())
        self.assertEqual(
            [item["reason"] for item in report["grants"] if item["drifted"]],
            ["identity_changed"],
        )

    def test_report_survives_a_pre_v13_database(self):
        with self.store._connection() as connection:
            connection.executescript(
                "DROP TABLE grant_leases; DROP TABLE access_grants;"
            )
        report = state_report(self.data_dir / "companion.db")
        self.assertFalse(report["available"])
        self.assertIn("v13", report["reason"])


if __name__ == "__main__":
    unittest.main()
