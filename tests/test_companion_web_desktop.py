"""Web contract for the integrated desktop-capability settings page."""

import contextlib
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from agent_framework.companion.app import create_application_service
from agent_framework.companion.models import AgentMode, RiskLevel
from agent_framework.companion.path_safety import app_ref, app_target_key
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import APP_READY, APP_TARGET_EXE, CompanionStore
from agent_framework.companion.web import WebAdapter
from agent_framework.companion.web.layout_audit import _scratch_temp
from agent_framework.llm import OfflineLLMClient

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


class StubSearch:
    def search(self, query, limit=5):
        return []


class DesktopWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.content_temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.content_root = Path(self.content_temp.name)
        self.store = CompanionStore(self.root / "companion.db")
        self.runtime = CompanionRuntime(
            store=self.store,
            llm_client=OfflineLLMClient(),
            search_service=SearchService(StubSearch()),
            data_dir=self.root,
            session_id="main",
            offline=True,
        )
        self.runtime.set_mode(AgentMode.WORK)
        self.service = create_application_service(self.runtime)
        self.adapter = WebAdapter(self.service, data_dir=self.root).start()
        self.base = "http://" + self.adapter.address

    def tearDown(self):
        self.adapter.stop()
        self.runtime.file_index.shutdown()
        self.content_temp.cleanup()
        self.temp.cleanup()

    def request(self, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={
                "X-Companion-Token": self.adapter.token,
                "Content-Type": "application/json",
            },
            method="POST" if body is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(
                    response.read().decode("utf-8")
                )
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8") or "{}")

    def test_payload_exposes_all_four_desktop_sections(self):
        status, payload = self.request("/api/desktop")
        self.assertEqual(200, status)
        for key in (
            "directories",
            "apps",
            "chains",
            "summary",
            "capabilities",
            "trigger_kinds",
            "uia",
            "recording",
        ):
            self.assertIn(key, payload)
        self.assertEqual(5, len(payload["capabilities"]))
        self.assertEqual(6, len(payload["trigger_kinds"]))
        self.assertFalse(payload["recording"]["active"])
        self.assertNotIn("delete_file", self.runtime.tool_names(AgentMode.WORK))

    def test_directory_search_and_delete_create_a_confirmation_proposal(self):
        folder = self.content_root / "authorized"
        folder.mkdir()
        document = folder / "report.md"
        document.write_text("quarterly report", encoding="utf-8")
        status, payload = self.request(
            "/api/desktop",
            {
                "action": "grant_directory",
                "path": str(folder),
                "capabilities": ["fs-read", "fs-search", "fs-delete"],
            },
        )
        self.assertEqual(200, status)
        grant_id = payload["grant"]["grant_id"]
        self.assertIn("delete_file", self.runtime.tool_names(AgentMode.WORK))
        self.runtime.file_index.index_grant(grant_id)

        status, payload = self.request(
            "/api/desktop",
            {"action": "search_files", "query": "report"},
        )
        self.assertEqual(200, status)
        candidate = payload["search"]["candidates"][0]
        self.assertEqual("report.md", candidate["name"])

        status, payload = self.request(
            "/api/desktop",
            {"action": "delete_file", "file_ref": candidate["file_ref"]},
        )
        self.assertEqual(200, status)
        self.assertEqual(payload["proposal"]["risk"], RiskLevel.FS_DELETE.value)
        pending = self.store.list_pending_proposals("main")
        self.assertEqual(1, len(pending))
        self.assertEqual("delete_file", pending[0].tool_name)

    def test_settings_grant_survives_the_conversation_workspace_selection(self):
        folder = self.content_root / "authorized"
        folder.mkdir()
        (folder / "report.md").write_text("report", encoding="utf-8")
        self.runtime.select_workspace(str(folder))
        status, payload = self.request(
            "/api/desktop",
            {
                "action": "grant_directory",
                "path": str(folder),
                "capabilities": [
                    "fs-read",
                    "fs-search",
                    "fs-write",
                    "fs-delete",
                ],
            },
        )
        self.assertEqual(200, status)
        row = payload["desktop"]["directories"][0]
        self.assertTrue(row["workspace_selected"])
        self.assertFalse(row["legacy_workspace"])
        self.assertEqual(
            set(row["capabilities"]),
            {"fs-read", "fs-search", "fs-write", "fs-delete"},
        )
        self.assertEqual(self.runtime.workspace(), str(folder))
        self.assertIn("write_file", self.runtime.tool_names(AgentMode.WORK))
        self.assertIn("delete_file", self.runtime.tool_names(AgentMode.WORK))

    def test_move_from_settings_executes_without_a_second_confirmation(self):
        folder = self.content_root / "authorized"
        folder.mkdir()
        source = folder / "report.md"
        source.write_text("report", encoding="utf-8")
        status, payload = self.request(
            "/api/desktop",
            {
                "action": "grant_directory",
                "path": str(folder),
                "capabilities": ["fs-read", "fs-search", "fs-write"],
            },
        )
        self.assertEqual(200, status)
        grant_id = payload["grant"]["grant_id"]
        self.runtime.file_index.index_grant(grant_id)
        status, payload = self.request(
            "/api/desktop",
            {"action": "search_files", "query": "report"},
        )
        self.assertEqual(200, status)
        file_ref = payload["search"]["candidates"][0]["file_ref"]
        status, payload = self.request(
            "/api/desktop",
            {
                "action": "move_file",
                "file_ref": file_ref,
                "target_grant_id": grant_id,
                "target_rel_path": "archive/report.md",
            },
        )
        self.assertEqual(200, status)
        self.assertTrue(payload["auto_executed"])
        self.assertEqual("completed", payload["execution"]["status"])
        self.assertFalse(source.exists())
        self.assertTrue((folder / "archive" / "report.md").is_file())

    def test_shell_app_registration_is_authorized_without_a_fake_identity(self):
        status, payload = self.request(
            "/api/desktop",
            {
                "action": "register_app",
                "name": "回收站",
                "target": "shell:RecycleBinFolder",
            },
        )
        self.assertEqual(200, status)
        app = [
            row
            for row in payload["desktop"]["apps"]
            if row["name"] == "回收站"
        ][0]
        self.assertTrue(app["allowed"])
        self.assertEqual("shell", app["target_kind"])
        self.assertIn("open_app", self.runtime.tool_names(AgentMode.WORK))

    def test_chain_can_be_saved_toggled_and_offered_as_a_proposal(self):
        for name, filename, digest in (
            ("Launcher", "launcher.exe", "starter"),
            ("Target", "target.exe", "target"),
        ):
            path = self.root / filename
            path.write_bytes(b"MZ" + bytes(126))
            key = app_target_key(str(path))
            self.store.upsert_app_catalog(
                [
                    {
                        "app_id": app_ref(key),
                        "name": name,
                        "target_kind": APP_TARGET_EXE,
                        "target": str(path),
                        "target_key": key,
                        "identity_digest": digest,
                        "status": APP_READY,
                    }
                ]
            )
            self.runtime.access.grant_app(str(path))
        apps = [
            row
            for row in self.request("/api/desktop")[1]["apps"]
            if row["name"] in ("Launcher", "Target")
        ]
        by_name = {row["name"]: row for row in apps}
        status, payload = self.request(
            "/api/desktop",
            {
                "action": "save_chain",
                "name": "Launcher to Target",
                "starter_app_id": by_name["Launcher"]["app_id"],
                "target_app_id": by_name["Target"]["app_id"],
                "timeout_seconds": 3,
                "require_descendant": True,
                "require_window": True,
            },
        )
        self.assertEqual(200, status)
        chain = payload["chain"]
        self.assertTrue(chain["enabled"])
        self.assertEqual("auto", chain["trigger_kind"])

        status, payload = self.request(
            "/api/desktop",
            {
                "action": "toggle_chain",
                "chain_id": chain["chain_id"],
                "enabled": False,
            },
        )
        self.assertEqual(200, status)
        self.assertFalse(payload["chain"]["enabled"])

    def test_argument_trigger_is_saved_through_the_desktop_api(self):
        for name, filename, digest in (
            ("Launcher", "launcher.exe", "starter"),
            ("Target", "target.exe", "target"),
        ):
            path = self.root / filename
            path.write_bytes(b"MZ" + bytes(126))
            key = app_target_key(str(path))
            self.store.upsert_app_catalog(
                [
                    {
                        "app_id": app_ref(key),
                        "name": name,
                        "target_kind": APP_TARGET_EXE,
                        "target": str(path),
                        "target_key": key,
                        "identity_digest": digest,
                        "status": APP_READY,
                    }
                ]
            )
            self.runtime.access.grant_app(str(path))
        apps = {
            row["name"]: row
            for row in self.request("/api/desktop")[1]["apps"]
            if row["name"] in ("Launcher", "Target")
        }
        status, payload = self.request(
            "/api/desktop",
            {
                "action": "save_chain",
                "name": "Argument chain",
                "starter_app_id": apps["Launcher"]["app_id"],
                "target_app_id": apps["Target"]["app_id"],
                "trigger_kind": "argument",
                "arguments": ["--project", "A B"],
                "timeout_seconds": 3,
                "require_window": True,
                "require_descendant": True,
            },
        )
        self.assertEqual(200, status)
        chain = payload["chain"]
        self.assertEqual(chain["trigger_kind"], "argument")
        self.assertEqual(
            chain["trigger_config"]["arguments"], ["--project", "A B"]
        )


@unittest.skipIf(sync_playwright is None, "未安装 playwright")
class DesktopBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix="companion-desktop-")
        cls.stack = contextlib.ExitStack()
        cls.stack.enter_context(_scratch_temp(Path(cls.scratch.name)))
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            cls.playwright.stop()
            cls.stack.close()
            cls.scratch.cleanup()
            raise unittest.SkipTest("浏览器不可用：{}".format(error))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.stack.close()
        cls.scratch.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.content_temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.content_root = Path(self.content_temp.name)
        self.store = CompanionStore(self.root / "companion.db")
        self.runtime = CompanionRuntime(
            store=self.store,
            llm_client=OfflineLLMClient(),
            search_service=SearchService(StubSearch()),
            data_dir=self.root,
            session_id="main",
            offline=True,
        )
        self.runtime.set_mode(AgentMode.WORK)
        folder = self.content_root / "authorized"
        folder.mkdir()
        (folder / "report.md").write_text("quarterly report", encoding="utf-8")
        grant = self.runtime.grant_directory(
            folder, ["fs-read", "fs-search", "fs-delete"]
        )
        self.runtime.file_index.index_grant(grant.grant_id)
        self.service = create_application_service(self.runtime)
        self.adapter = WebAdapter(self.service, data_dir=self.root).start()
        self.context = self.browser.new_context(
            viewport={"width": 1180, "height": 860}
        )
        self.errors = []

    def tearDown(self):
        self.assertEqual([], self.errors)
        self.context.close()
        self.adapter.stop()
        self.runtime.file_index.shutdown()
        self.content_temp.cleanup()
        self.temp.cleanup()

    def open_page(self, path="/settings"):
        page = self.context.new_page()
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.on(
            "console",
            lambda message: self.errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.goto(
            "http://{}{}?t={}".format(
                self.adapter.address, path, self.adapter.token
            ),
            wait_until="load",
        )
        page.wait_for_selector(
            "#nav [data-cat]" if path.startswith("/settings") else "#shell"
        )
        return page

    def test_desktop_panel_searches_and_creates_the_delete_card(self):
        page = self.open_page()
        page.click('[data-cat="desktop"]')
        page.click('[data-panel="desktop"]')
        page.wait_for_selector(".desk-tabs")
        for tab in ("files", "apps", "chains", "record", "directories"):
            page.click('[data-desk-tab="{}"]'.format(tab))
            self.assertTrue(page.locator(".panel").count())

        page.click('[data-desk-tab="chains"]')
        self.assertEqual(
            6,
            page.locator("#deskChainTrigger option").count(),
        )
        page.select_option("#deskChainTrigger", "uia")
        self.assertFalse(
            page.locator('[data-trigger-field="uia"]').is_hidden()
        )
        page.select_option("#deskChainTrigger", "auto")

        page.click('[data-desk-tab="files"]')
        page.fill("#deskFileQuery", "report")
        page.click('[data-desk-act="search-files"]')
        page.wait_for_selector('[data-desk-act="delete-file"]')
        shots = Path("data/layout_audit")
        shots.mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=str(shots / "desktop-files-100.png"),
            full_page=True,
        )
        self.adapter.update_ui({"font_scale": 200})
        page.reload(wait_until="load")
        page.wait_for_selector("#nav [data-cat]")
        page.click('[data-cat="desktop"]')
        page.click('[data-panel="desktop"]')
        page.click('[data-desk-tab="apps"]')
        page.screenshot(
            path=str(shots / "desktop-apps-200.png"),
            full_page=True,
        )
        overflow = page.evaluate(
            "() => Array.from(document.querySelectorAll('.panel, .desk-item, .desk-tabs, .desk-actions'))"
            ".filter(node => node.scrollWidth > node.clientWidth + 1).length"
        )
        self.assertEqual(0, overflow)
        page.click('[data-desk-tab="files"]')
        page.fill("#deskFileQuery", "report")
        page.click('[data-desk-act="search-files"]')
        page.wait_for_selector('[data-desk-act="delete-file"]')
        page.click('[data-desk-act="delete-file"]')
        page.wait_for_selector(".dialog-ok")
        page.click(".dialog-ok")
        page.wait_for_function(
            "() => document.querySelector('#toast').textContent.indexOf('删除提案') >= 0"
        )

        main = self.open_page("/")
        main.wait_for_selector('[data-approval]')
        card = main.locator("[data-approval]").last
        self.assertIn("移入回收站", card.inner_text())
        card.screenshot(
            path=str(shots / "desktop-delete-card-200.png")
        )
        overflow = main.evaluate(
            "() => Array.from(document.querySelectorAll('.approval, .approval .meta .row, .approval .actions'))"
            ".filter(node => node.scrollWidth > node.clientWidth + 1).length"
        )
        self.assertEqual(0, overflow)


if __name__ == "__main__":
    unittest.main()
