"""S5 launch-chain configuration, detection, preflight and execution."""

import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from agent_framework.companion.access_control import AccessControlService
from agent_framework.companion.app_catalog import AppCatalogService
from agent_framework.companion.app_launch import AppLaunchService
from agent_framework.companion.desktop_uia import (
    UiAutomationAdapter,
    UiAutomationError,
)
from agent_framework.companion.file_actions import (
    NotExecutedError,
    UncertainExecutionError,
)
from agent_framework.companion.launch_chain import (
    LAUNCH_CHAIN_TOOL,
    LaunchChainError,
    LaunchChainService,
    TRIGGER_API,
    TRIGGER_ARGUMENT,
    TRIGGER_URI,
    TRIGGER_UIA,
    chain_config_digest,
    match_target_processes,
    normalize_detection_rules,
    normalize_readiness_rules,
    normalize_trigger_config,
)
from agent_framework.companion.maintenance import (
    EXPECTED_INDEXES,
    EXPORT_TABLES,
    PURGE_ALL_TABLES,
)
from agent_framework.companion.models import (
    AgentMode,
    EXECUTOR_CHAIN,
    ProposalStatus,
    RiskLevel,
)
from agent_framework.companion.path_safety import app_ref, app_target_key
from agent_framework.companion.policy import (
    ALLOW,
    ASK,
    DENY,
    PERMISSION_RELAXED,
    PERMISSION_STRICT,
    decide_tool_policy,
)
from agent_framework.companion.store import (
    APP_READY,
    APP_TARGET_EXE,
    SCHEMA_VERSION,
    CompanionStore,
)


class _SilentLLM:
    def chat(self, *args, **kwargs):
        raise AssertionError("launch-chain tests must not call the model")

    def stream(self, *args, **kwargs):
        raise AssertionError("launch-chain tests must not call the model")


class ChainFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = CompanionStore(self.root / "companion.db")
        self.access = AccessControlService(self.store)
        self.catalog = AppCatalogService(self.store)
        self.apps = AppLaunchService(self.store, self.catalog, self.access)

    def raw(self, sql, params=()):
        with self.store._connection() as connection:
            return connection.execute(sql, params).fetchall()

    def add_app(self, name, filename, identity):
        target = self.root / filename
        target.write_bytes(b"MZ" + bytes(126))
        target_key = app_target_key(str(target))
        app_id = app_ref(target_key)
        self.store.upsert_app_catalog(
            [
                {
                    "app_id": app_id,
                    "name": name,
                    "target_kind": APP_TARGET_EXE,
                    "target": str(target),
                    "target_key": target_key,
                    "identity_digest": identity,
                    "status": APP_READY,
                }
            ]
        )
        self.access.grant_app(str(target))
        return self.store.get_app_catalog(app_id)

    def recheck(self, app_id):
        row = self.store.get_app_catalog(app_id)
        return {"ok": True, "row": row, "reason": "", "changed": False}

    def make_chain(
        self,
        service=None,
        trigger_kind="auto",
        trigger_config=None,
        **rules
    ):
        starter = self.add_app("Launcher", "launcher.exe", "starter-id")
        target = self.add_app("Target", "target.exe", "target-id")
        service = service or LaunchChainService(
            self.store, self.catalog, self.apps
        )
        detection = {
            "require_window": True,
            "require_descendant": True,
        }
        detection.update(rules)
        chain = service.save_chain(
            "Launcher to Target",
            starter["app_id"],
            target["app_id"],
            trigger_kind=trigger_kind,
            trigger_config=trigger_config or {},
            detection_rules=detection,
            readiness_rules={
                "timeout_seconds": 1,
                "poll_interval_seconds": 0.05,
            },
        )
        return chain, starter, target


class SchemaTests(ChainFixture):
    def test_v15_database_gains_launch_chains(self):
        with self.store._connection() as connection:
            connection.execute("DROP TABLE launch_chains")
            connection.execute("PRAGMA user_version = 15")
        reopened = CompanionStore(self.root / "companion.db")
        tables = {
            row[0]
            for row in self.raw(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        indexes = {
            row[0]
            for row in self.raw(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        self.assertEqual(SCHEMA_VERSION, 17)
        self.assertEqual(
            int(self.raw("PRAGMA user_version")[0][0]), SCHEMA_VERSION
        )
        self.assertIn("launch_chains", tables)
        self.assertTrue(
            {
                "idx_launch_chains_enabled",
                "idx_launch_chains_starter",
                "idx_launch_chains_target",
            }
            <= indexes
        )
        self.assertIn("launch_chains", EXPORT_TABLES)
        self.assertIn("launch_chains", PURGE_ALL_TABLES)
        self.assertTrue(
            {"idx_launch_chains_enabled", "idx_launch_chains_starter"}
            <= set(EXPECTED_INDEXES)
        )
        self.assertIsNotNone(reopened)


class ConfigurationTests(ChainFixture):
    def setUp(self):
        super().setUp()
        self.patch = mock.patch.object(
            self.catalog, "recheck_identity", side_effect=self.recheck
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_chain_persists_normalized_rules_and_stable_digest(self):
        chain, starter, target = self.make_chain(
            window_title_contains=["Target"]
        )
        self.assertEqual(chain["starter_app_id"], starter["app_id"])
        self.assertEqual(chain["target_app_id"], target["app_id"])
        self.assertEqual(
            chain["detection_rules"]["window_title_contains"], ["Target"]
        )
        self.assertTrue(chain["enabled"])
        self.assertEqual(chain["config_digest"], chain_config_digest(chain))
        self.assertIn(chain, self.store.list_launch_chains(enabled=True))

    def test_duplicate_names_and_identical_apps_are_refused(self):
        chain, starter, _target = self.make_chain()
        with self.assertRaises(LaunchChainError) as identical:
            LaunchChainService(
                self.store, self.catalog, self.apps
            ).save_chain(
                chain["name"],
                starter["app_id"],
                starter["app_id"],
            )
        self.assertEqual(identical.exception.code, "chain_apps_identical")
        with self.assertRaises(LaunchChainError) as duplicate:
            LaunchChainService(
                self.store, self.catalog, self.apps
            ).save_chain(
                chain["name"],
                chain["starter_app_id"],
                chain["target_app_id"],
            )
        self.assertEqual(duplicate.exception.code, "chain_name_duplicate")

    def test_unknown_rule_names_are_rejected(self):
        with self.assertRaises(LaunchChainError) as detection:
            normalize_detection_rules({"window_name": "typo"})
        self.assertEqual(detection.exception.code, "invalid_detection_rules")
        with self.assertRaises(LaunchChainError) as readiness:
            normalize_readiness_rules({"timeout": 1})
        self.assertEqual(readiness.exception.code, "invalid_readiness_rules")

    def test_trigger_configs_are_normalized_and_whitelisted(self):
        self.assertEqual(
            normalize_trigger_config(
                TRIGGER_ARGUMENT, {"arguments": ["--project", "A B"]}
            ),
            {"arguments": ["--project", "A B"]},
        )
        self.assertEqual(
            normalize_trigger_config(
                TRIGGER_URI, {"uri": "codexplusplus://open/project"}
            ),
            {"uri": "codexplusplus://open/project"},
        )
        with self.assertRaises(LaunchChainError) as blocked:
            normalize_trigger_config(TRIGGER_URI, {"uri": "file:///C:/secret"})
        self.assertEqual(blocked.exception.code, "chain_trigger_invalid")
        with self.assertRaises(LaunchChainError) as unknown:
            normalize_trigger_config(
                TRIGGER_ARGUMENT, {"arguments": ["--open"], "shell": "x"}
            )
        self.assertEqual(unknown.exception.code, "chain_trigger_invalid")

    def test_uia_requires_an_explicit_adapter(self):
        chain, _starter, _target = self.make_chain(
            trigger_kind=TRIGGER_UIA,
            trigger_config={"automation_id": "open-project"},
        )
        without = LaunchChainService(self.store, self.catalog, self.apps)
        self.assertNotIn(chain, without.runnable_chains())
        with_adapter = LaunchChainService(
            self.store,
            self.catalog,
            self.apps,
            trigger_target=lambda *_args: {"kind": "uia"},
        )
        self.assertIn(chain, with_adapter.runnable_chains())

    def test_chain_requires_confirmation_in_work_and_execute(self):
        for mode in (AgentMode.WORK, AgentMode.EXECUTE):
            self.assertEqual(
                decide_tool_policy(mode, RiskLevel.LAUNCH_CHAIN), ASK
            )
            self.assertEqual(
                decide_tool_policy(
                    mode, RiskLevel.LAUNCH_CHAIN, PERMISSION_RELAXED
                ),
                ASK,
            )
        for mode in (AgentMode.CHAT, AgentMode.QUIET):
            self.assertEqual(
                decide_tool_policy(mode, RiskLevel.LAUNCH_CHAIN), DENY
            )
        self.assertEqual(
            decide_tool_policy(
                AgentMode.WORK, RiskLevel.LAUNCH_CHAIN, PERMISSION_STRICT
            ),
            DENY,
        )
        self.assertEqual(
            decide_tool_policy(
                AgentMode.WORK, RiskLevel.APP_LAUNCH
            ),
            ALLOW,
        )


class DetectionTests(unittest.TestCase):
    def test_match_excludes_preexisting_and_requires_descendant(self):
        target = {"target": r"C:\Apps\target.exe"}
        rules = normalize_detection_rules(
            {"require_descendant": True, "require_window": True}
        )
        processes = [
            {"pid": 10, "ppid": 1, "name": "old.exe", "exe_path": target["target"]},
            {"pid": 20, "ppid": 1, "name": "launcher.exe", "exe_path": r"C:\Apps\launcher.exe"},
            {"pid": 30, "ppid": 20, "name": "target.exe", "exe_path": target["target"]},
            {"pid": 40, "ppid": 1, "name": "target.exe", "exe_path": target["target"]},
        ]
        windows = [
            {"pid": 10, "title": "Old", "class_name": "OldWindow"},
            {"pid": 30, "title": "Target", "class_name": "TargetWindow"},
            {"pid": 40, "title": "Other", "class_name": "OtherWindow"},
        ]
        matches = match_target_processes(
            processes,
            windows,
            target,
            rules,
            before_pids=[10, 20],
            starter_pid=20,
        )
        self.assertEqual([item["pid"] for item in matches], [30])
        self.assertEqual(matches[0]["windows"][0]["title"], "Target")


class _FakeUiaInfo:
    def __init__(self, pid=42, handle=501, name="Launcher", control_type="Button"):
        self.process_id = pid
        self.handle = handle
        self.name = name
        self.class_name = "LauncherWindow"
        self.automation_id = "open-project"
        self.control_type = control_type


class _FakeUiaControl:
    def __init__(self):
        self.element_info = _FakeUiaInfo(handle=502, name="Open project")
        self.invoked = 0

    def invoke(self):
        self.invoked += 1


class _FakeUiaSpec:
    def __init__(self, control):
        self.control = control
        self.waited = None
        self.kwargs = {}

    def wait(self, state, timeout):
        self.waited = (state, timeout)

    def wrapper_object(self):
        return self.control


class _FakeUiaWindow:
    def __init__(self, pid=42, title="Codex++", handle=501):
        self.element_info = _FakeUiaInfo(
            pid=pid, handle=handle, name=title
        )
        self.control = _FakeUiaControl()
        self.spec = None

    def child_window(self, **kwargs):
        self.spec = _FakeUiaSpec(self.control)
        self.spec.kwargs = kwargs
        return self.spec


class _FakeUiaDesktop:
    def __init__(self, windows):
        self._windows = windows

    def windows(self):
        return self._windows


class UiAutomationTests(unittest.TestCase):
    def adapter(self, windows):
        desktop = _FakeUiaDesktop(windows)
        return (
            UiAutomationAdapter(
                desktop_factory=lambda: desktop,
                availability_check=lambda: (True, ""),
            ),
            desktop,
        )

    def test_uia_clicks_only_a_control_in_the_starter_window(self):
        window = _FakeUiaWindow()
        adapter, _desktop = self.adapter([window, _FakeUiaWindow(pid=99)])
        evidence = adapter.trigger(
            TRIGGER_UIA,
            {"app_id": "starter"},
            {"app_id": "target"},
            42,
            {
                "window_title_contains": "Codex",
                "window_class": "LauncherWindow",
                "automation_id": "open-project",
                "control_name": "",
                "control_type": "Button",
            },
        )
        self.assertEqual(evidence["window"]["pid"], 42)
        self.assertEqual(evidence["control"]["automation_id"], "open-project")
        self.assertEqual(evidence["action"], "invoke")
        self.assertEqual(window.control.invoked, 1)
        self.assertEqual(window.spec.kwargs["auto_id"], "open-project")

    def test_uia_refuses_ambiguous_windows_instead_of_guessing(self):
        adapter, _desktop = self.adapter(
            [_FakeUiaWindow(), _FakeUiaWindow(handle=777)]
        )
        with self.assertRaises(UiAutomationError) as caught:
            adapter.trigger(
                TRIGGER_UIA,
                {},
                {},
                42,
                {
                    "automation_id": "open-project",
                    "window_title_contains": "Codex",
                },
            )
        self.assertEqual(caught.exception.code, "uia_window_ambiguous")

    def test_uia_waits_for_the_starter_window_to_appear(self):
        window = _FakeUiaWindow()

        class LateDesktop:
            calls = 0

            def windows(self):
                self.calls += 1
                return [] if self.calls < 3 else [window]

        desktop = LateDesktop()
        adapter = UiAutomationAdapter(
            desktop_factory=lambda: desktop,
            availability_check=lambda: (True, ""),
            sleep=lambda _seconds: None,
        )
        evidence = adapter.trigger(
            TRIGGER_UIA,
            {},
            {},
            42,
            {
                "window_title_contains": "Codex",
                "automation_id": "open-project",
            },
        )
        self.assertEqual(desktop.calls, 3)
        self.assertEqual(evidence["action"], "invoke")


class ExecutionTests(ChainFixture):
    def setUp(self):
        super().setUp()
        self.patch = mock.patch.object(
            self.catalog, "recheck_identity", side_effect=self.recheck
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.before = [
            {"pid": 10, "ppid": 1, "name": "python.exe", "exe_path": ""}
        ]
        self.after = [
            *self.before,
            {
                "pid": 100,
                "ppid": 10,
                "name": "launcher.exe",
                "exe_path": str(self.root / "launcher.exe"),
            },
            {
                "pid": 300,
                "ppid": 100,
                "name": "target.exe",
                "exe_path": str(self.root / "target.exe"),
            },
        ]

    def snapshot_pair(self, sequence):
        state = {"index": 0}

        def processes():
            index = min(state["index"], len(sequence) - 1)
            state["index"] += 1
            return sequence[index]

        def windows():
            current = sequence[min(state["index"] - 1, len(sequence) - 1)]
            return [
                {
                    "pid": item["pid"],
                    "title": "Target",
                    "class_name": "TargetWindow",
                }
                for item in current
                if item["name"] == "target.exe"
            ]

        return processes, windows

    def proposal_service(self, processes, windows, launch=None):
        return LaunchChainService(
            self.store,
            self.catalog,
            self.apps,
            snapshot_processes=processes,
            snapshot_windows=windows,
            launch=launch or (lambda _row: {"pid": 100}),
        )

    def test_preexisting_target_is_refused_unless_reuse_is_explicit(self):
        chain, _starter, _target = self.make_chain()
        existing = [
            *self.before,
            {
                "pid": 300,
                "ppid": 1,
                "name": "target.exe",
                "exe_path": str(self.root / "target.exe"),
            },
        ]
        process_fn, window_fn = self.snapshot_pair([existing])
        service = self.proposal_service(process_fn, window_fn)
        with self.assertRaises(LaunchChainError) as caught:
            service.create_proposal("main", chain["chain_id"], AgentMode.WORK)
        self.assertEqual(caught.exception.code, "chain_target_preexisting")

        chain = service.save_chain(
            chain["name"],
            chain["starter_app_id"],
            chain["target_app_id"],
            chain_id=chain["chain_id"],
            detection_rules={
                "reuse_existing": True,
                "require_window": True,
            },
            readiness_rules=chain["readiness_rules"],
        )
        process_fn, window_fn = self.snapshot_pair([existing])
        proposal = self.proposal_service(
            process_fn, window_fn
        ).create_proposal("main", chain["chain_id"], AgentMode.WORK)
        self.assertIs(proposal.risk, RiskLevel.LAUNCH_CHAIN)
        self.assertEqual(proposal.executor, EXECUTOR_CHAIN)
        self.assertEqual(proposal.tool_name, LAUNCH_CHAIN_TOOL)

    def test_completed_receipt_requires_a_new_matching_target(self):
        chain, _starter, _target = self.make_chain()
        proposals_fn, proposal_windows = self.snapshot_pair([self.before])
        proposal = self.proposal_service(
            proposals_fn, proposal_windows
        ).create_proposal("main", chain["chain_id"], AgentMode.WORK)
        process_fn, window_fn = self.snapshot_pair(
            [self.before, self.before, self.after]
        )
        launched = []

        def launch(row):
            launched.append(row["app_id"])
            return {"launcher": "create_process", "pid": 100}

        receipt = self.proposal_service(
            process_fn, window_fn, launch=launch
        ).execute(proposal)
        self.assertEqual(receipt.status, "completed")
        self.assertEqual(launched, [chain["starter_app_id"]])
        self.assertEqual(receipt.result["target_process"]["pid"], 300)
        self.assertFalse(receipt.undoable)
        self.assertIn("已确认", receipt.summary)

    def test_timeout_after_launch_is_uncertain_not_success(self):
        chain, _starter, _target = self.make_chain()
        proposal_fn, proposal_windows = self.snapshot_pair([self.before])
        proposal = self.proposal_service(
            proposal_fn, proposal_windows
        ).create_proposal("main", chain["chain_id"], AgentMode.WORK)
        clock = {"now": 0.0}

        def sleep(seconds):
            clock["now"] += seconds

        process_fn, window_fn = self.snapshot_pair([self.before])
        service = LaunchChainService(
            self.store,
            self.catalog,
            self.apps,
            snapshot_processes=process_fn,
            snapshot_windows=window_fn,
            launch=lambda _row: {"pid": 100},
            sleep=sleep,
            clock=lambda: clock["now"],
        )
        with self.assertRaises(UncertainExecutionError):
            service.execute(proposal)

    def test_cancel_after_launch_is_uncertain(self):
        chain, _starter, _target = self.make_chain()
        proposal_fn, proposal_windows = self.snapshot_pair([self.before])
        proposal = self.proposal_service(
            proposal_fn, proposal_windows
        ).create_proposal("main", chain["chain_id"], AgentMode.WORK)
        event = threading.Event()
        process_fn, window_fn = self.snapshot_pair([self.before])
        service = LaunchChainService(
            self.store,
            self.catalog,
            self.apps,
            snapshot_processes=process_fn,
            snapshot_windows=window_fn,
            launch=lambda _row: (
                event.set() or {"pid": 100}
            ),
            stop_event=event,
        )
        with self.assertRaises(UncertainExecutionError):
            service.execute(proposal)

    def test_identity_drift_blocks_before_launch(self):
        chain, starter, _target = self.make_chain()
        process_fn, window_fn = self.snapshot_pair([self.before])
        service = self.proposal_service(process_fn, window_fn)
        proposal = service.create_proposal(
            "main", chain["chain_id"], AgentMode.WORK
        )
        with self.store._connection() as connection:
            connection.execute(
                "UPDATE app_catalog SET identity_digest = ? WHERE app_id = ?",
                ("changed-after-proposal", starter["app_id"]),
            )
        launched = []
        with self.assertRaises(NotExecutedError):
            self.proposal_service(
                *self.snapshot_pair([self.before]),
                launch=lambda row: launched.append(row),
            ).execute(proposal)
        self.assertEqual(launched, [])

    def test_argument_trigger_uses_only_registered_arguments(self):
        chain, _starter, _target = self.make_chain(
            trigger_kind=TRIGGER_ARGUMENT,
            trigger_config={"arguments": ["--project", "A B"]},
        )
        proposal_fn, proposal_windows = self.snapshot_pair([self.before])
        proposal = self.proposal_service(
            proposal_fn, proposal_windows
        ).create_proposal("main", chain["chain_id"], AgentMode.WORK)
        process_fn, window_fn = self.snapshot_pair(
            [self.before, self.before, self.after]
        )
        launched = []
        receipt = self.proposal_service(
            process_fn,
            window_fn,
            launch=lambda row: (
                launched.append(row) or {"pid": 100}
            ),
        ).execute(proposal)
        self.assertEqual(receipt.status, "completed")
        self.assertEqual(launched[0]["app_id"], chain["starter_app_id"])
        self.assertIn('--project "A B"', launched[0]["arguments"])

    def test_uri_trigger_uses_frozen_shell_target(self):
        chain, _starter, _target = self.make_chain(
            trigger_kind=TRIGGER_URI,
            trigger_config={"uri": "codexplusplus://open/project"},
        )
        proposal_fn, proposal_windows = self.snapshot_pair([self.before])
        proposal = self.proposal_service(
            proposal_fn, proposal_windows
        ).create_proposal("main", chain["chain_id"], AgentMode.WORK)
        process_fn, window_fn = self.snapshot_pair(
            [self.before, self.before, self.after]
        )
        opened = []
        receipt = LaunchChainService(
            self.store,
            self.catalog,
            self.apps,
            snapshot_processes=process_fn,
            snapshot_windows=window_fn,
            launch=lambda _row: {"pid": 100},
            open_uri=lambda uri: opened.append(uri) or 55,
        ).execute(proposal)
        self.assertEqual(opened, ["codexplusplus://open/project"])
        self.assertEqual(receipt.result["trigger"]["shell_instance"], 55)

    def test_api_trigger_launches_a_registered_entry(self):
        api_app = self.add_app("API Bridge", "bridge.exe", "bridge-id")
        chain, _starter, _target = self.make_chain(
            trigger_kind=TRIGGER_API,
            trigger_config={
                "api_app_id": api_app["app_id"],
                "arguments": ["open", "--project", "A"],
            },
        )
        proposal_fn, proposal_windows = self.snapshot_pair([self.before])
        proposal = self.proposal_service(
            proposal_fn, proposal_windows
        ).create_proposal("main", chain["chain_id"], AgentMode.WORK)
        process_fn, window_fn = self.snapshot_pair(
            [self.before, self.before, self.after]
        )
        launched = []
        receipt = self.proposal_service(
            process_fn,
            window_fn,
            launch=lambda row: (
                launched.append(row) or {"pid": 100}
            ),
        ).execute(proposal)
        self.assertEqual(receipt.status, "completed")
        self.assertEqual(
            [row["app_id"] for row in launched],
            [chain["starter_app_id"], api_app["app_id"]],
        )
        self.assertIn("--project A", launched[1]["arguments"])

    def test_uia_trigger_uses_the_injected_adapter_and_emits_progress(self):
        chain, _starter, _target = self.make_chain(
            trigger_kind=TRIGGER_UIA,
            trigger_config={
                "window_title_contains": "Launcher",
                "automation_id": "open-project",
                "control_type": "Button",
            },
        )
        calls = []
        stages = []

        def trigger(*args):
            calls.append(args)
            return {"kind": "uia", "action": "invoke"}

        adapter = mock.Mock(side_effect=trigger)
        adapter.available.return_value = (True, "")
        proposal_fn, proposal_windows = self.snapshot_pair([self.before])
        service = LaunchChainService(
            self.store,
            self.catalog,
            self.apps,
            snapshot_processes=proposal_fn,
            snapshot_windows=proposal_windows,
            launch=lambda _row: {"pid": 100},
            trigger_target=adapter,
        )
        service.set_progress_callback(
            lambda item: stages.append(item["stage"])
        )
        proposal = service.create_proposal(
            "main", chain["chain_id"], AgentMode.WORK
        )
        process_fn, window_fn = self.snapshot_pair(
            [self.before, self.before, self.after]
        )
        service._snapshot_processes = process_fn
        service._snapshot_windows = window_fn
        receipt = service.execute(proposal)
        self.assertEqual(receipt.status, "completed")
        self.assertEqual(calls[0][0], TRIGGER_UIA)
        self.assertEqual(calls[0][3], 100)
        self.assertEqual(
            stages,
            ["checking", "launching", "triggering", "waiting", "completed"],
        )


class RecordingTests(ChainFixture):
    def setUp(self):
        super().setUp()
        self.patch = mock.patch.object(
            self.catalog, "recheck_identity", side_effect=self.recheck
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_recording_lists_new_catalog_process_without_granting(self):
        starter = self.add_app("Launcher", "launcher.exe", "starter-id")
        target = self.add_app("Target", "target.exe", "target-id")
        before = [
            {"pid": 10, "ppid": 1, "name": "python.exe", "exe_path": ""}
        ]
        after = [
            *before,
            {
                "pid": 300,
                "ppid": 10,
                "name": "target.exe",
                "exe_path": str(self.root / "target.exe"),
            },
        ]
        state = {"index": 0}

        def processes():
            value = before if state["index"] == 0 else after
            state["index"] += 1
            return value

        def windows():
            return (
                []
                if state["index"] <= 1
                else [
                    {
                        "pid": 300,
                        "hwnd": 900,
                        "title": "Target project",
                        "class_name": "TargetWindow",
                    }
                ]
            )

        service = LaunchChainService(
            self.store,
            self.catalog,
            self.apps,
            snapshot_processes=processes,
            snapshot_windows=windows,
        )
        grants_before = len(self.access.list_grants(status="active"))
        started = service.start_recording(starter["app_id"])
        self.assertTrue(started["active"])
        status = service.recording_status(started["recording_id"])
        self.assertEqual(status["candidate_count"], 1)
        candidate = status["candidates"][0]
        self.assertEqual(candidate["app_id"], target["app_id"])
        self.assertTrue(candidate["app_allowed"])
        self.assertEqual(candidate["windows"][0]["title"], "Target project")
        self.assertEqual(
            len(self.access.list_grants(status="active")), grants_before
        )
        self.assertTrue(service.cancel_recording(started["recording_id"]))

    def test_target_detection_returns_read_only_instance_candidates(self):
        chain, _starter, _target = self.make_chain()
        processes = [
            {
                "pid": 300,
                "ppid": 1,
                "name": "target.exe",
                "exe_path": str(self.root / "target.exe"),
            }
        ]
        windows = [
            {
                "pid": 300,
                "hwnd": 900,
                "title": "Target",
                "class_name": "TargetWindow",
            }
        ]
        service = LaunchChainService(
            self.store,
            self.catalog,
            self.apps,
            snapshot_processes=lambda: processes,
            snapshot_windows=lambda: windows,
        )
        result = service.detect_chain_targets(chain["chain_id"])
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["candidates"][0]["pid"], 300)
        self.assertFalse(result["ambiguous"])


class RuntimeIntegrationTests(ChainFixture):
    def setUp(self):
        super().setUp()
        self.patch = mock.patch.object(
            self.catalog, "recheck_identity", side_effect=self.recheck
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.before = [
            {"pid": 10, "ppid": 1, "name": "python.exe", "exe_path": ""}
        ]
        self.after = [
            *self.before,
            {
                "pid": 100,
                "ppid": 10,
                "name": "launcher.exe",
                "exe_path": str(self.root / "launcher.exe"),
            },
            {
                "pid": 300,
                "ppid": 100,
                "name": "target.exe",
                "exe_path": str(self.root / "target.exe"),
            },
        ]

    def test_runtime_registers_and_executes_chain_tool(self):
        from agent_framework.companion.runtime import CompanionRuntime

        chain, _starter, _target = self.make_chain()
        runtime = CompanionRuntime(
            self.store,
            _SilentLLM(),
            mock.Mock(),
            self.root,
            offline=True,
        )
        runtime.set_mode(AgentMode.WORK)
        runtime.app_catalog.recheck_identity = self.recheck
        sequence = [self.before, self.before, self.before, self.after]
        state = {"index": 0}

        def processes():
            index = min(state["index"], len(sequence) - 1)
            state["index"] += 1
            return sequence[index]

        def windows():
            current = sequence[min(state["index"] - 1, len(sequence) - 1)]
            return [
                {
                    "pid": item["pid"],
                    "title": "Target",
                    "class_name": "TargetWindow",
                }
                for item in current
                if item["name"] == "target.exe"
            ]

        runtime.chains = LaunchChainService(
            self.store,
            runtime.app_catalog,
            runtime.apps,
            snapshot_processes=processes,
            snapshot_windows=windows,
            launch=lambda _row: {"launcher": "create_process", "pid": 100},
        )
        runtime._executors[EXECUTOR_CHAIN] = runtime.chains
        self.assertIn(
            "run_launch_chain", runtime.tool_names(AgentMode.WORK)
        )
        registry = runtime._core_tool_registry(AgentMode.WORK, None)
        result = registry.execute(
            "run_launch_chain",
            {"chain": chain["chain_id"]},
            AgentMode.WORK,
        )
        self.assertTrue(result.ok, result.content)
        self.assertIsNotNone(result.proposal)
        self.store.create_proposal(result.proposal)
        outcome = runtime.confirm_proposal(result.proposal.action_id)
        self.assertEqual(outcome["receipt"].status, "completed")
        self.assertEqual(
            self.store.get_proposal(result.proposal.action_id).status,
            ProposalStatus.COMPLETED,
        )


if __name__ == "__main__":
    unittest.main()
