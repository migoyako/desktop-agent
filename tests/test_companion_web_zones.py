"""M15 slice 4 in a real browser: the five remaining settings zones.

Static contracts can prove the markup is there; they cannot prove that clicking
「保存」moves a number from the page into the store, that a file chooser really
writes into the avatar slot, or that 「清空记忆」 asks before it deletes. These
tests drive the settings window in Chromium against a throwaway data directory.
"""

import base64
import contextlib
import os
import tempfile
import unittest
from pathlib import Path

from agent_framework.companion.app import create_application_service
from agent_framework.companion.mcp import McpService, keys
from agent_framework.companion.models import AgentMode
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import SCHEMA_VERSION, CompanionStore
from agent_framework.companion.web import WebAdapter
from agent_framework.companion.web.layout_audit import (
    PROBE,
    _post_ui,
    _scratch_temp,
)
from agent_framework.companion.mcp.jsonrpc import McpError
from agent_framework.llm import OfflineLLMClient

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

HOST_STUB = """
window.__hostCalls = [];
window.__host = {
  ready: function () {},
  beginWindowDrag: function () {},
  moveWindow: function () {},
};
window.qt = { webChannelTransport: {} };
"""

QWEBCHANNEL_STUB = """
window.QWebChannel = function (transport, callback) {
  callback({ objects: { host: window.__host } });
};
"""


class StubSearch:
    def search(self, query, limit=5):
        return []


# 面板流程要真的能「连上并列出工具」，但测试绝不为了界面去开一个子进程或出网。
MCP_TOOLS = [
    {
        "name": "echo_text",
        "description": "Repeat the text back.",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
    },
    {
        "name": "write_note",
        "description": "Write a note on the server.",
        "annotations": {"readOnlyHint": False},
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
    },
]


class StubMcpTransport:
    def __init__(self):
        self.connected_flag = False
        self.methods = []

    @property
    def connected(self):
        return self.connected_flag

    def connect(self):
        self.connected_flag = True

    def request(self, method, params=None, timeout=None):
        self.methods.append(method)
        if method == "initialize":
            return {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "stub"},
            }
        if method == "tools/list":
            return {"tools": MCP_TOOLS}
        raise McpError("没有这个方法。", "stub")

    def notify(self, method, params=None):
        pass

    def close(self):
        self.connected_flag = False


@unittest.skipIf(sync_playwright is None, "未安装 playwright（见 requirements-optional.txt）")
class ZoneBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix="companion-zones-")
        cls.stack = contextlib.ExitStack()
        cls.stack.enter_context(_scratch_temp(Path(cls.scratch.name)))
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:  # browser binary missing or unable to start
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
        self.root = Path(self.temp.name)
        # 凭据面永远不落在这台机器的 config/mcp_keys.json 上（Q2）。
        self.previous_keys = os.environ.get(keys.KEYS_PATH_ENV)
        os.environ[keys.KEYS_PATH_ENV] = str(self.root / "mcp_keys.json")
        store = CompanionStore(self.root / "companion.db")
        runtime = CompanionRuntime(
            store=store,
            llm_client=OfflineLLMClient(),
            search_service=SearchService(StubSearch()),
            data_dir=self.root,
            session_id="main",
            offline=True,
        )
        runtime.select_workspace(str(self.root))
        self.service = create_application_service(runtime)
        self.adapter = WebAdapter(self.service, data_dir=self.root).start()
        self.store = self.adapter.runtime.store
        self.context = self.browser.new_context(viewport={"width": 1180, "height": 860})
        self.page = None
        self.errors = []

    def tearDown(self):
        if self.previous_keys is None:
            os.environ.pop(keys.KEYS_PATH_ENV, None)
        else:
            os.environ[keys.KEYS_PATH_ENV] = self.previous_keys
        if self.page is not None:
            self.assertEqual([], self.errors)
        self.context.close()
        self.adapter.stop()
        self.temp.cleanup()

    # -- helpers --------------------------------------------------------

    def open_page(self, path="/settings"):
        page = self.context.new_page()
        page.add_init_script(HOST_STUB)
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.on(
            "console",
            lambda message: self.errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.route(
            "**/qwebchannel.js",
            lambda route: route.fulfill(
                status=200,
                content_type="application/javascript",
                body=QWEBCHANNEL_STUB,
            ),
        )
        self.page = page
        page.goto(
            "http://{}{}?t={}".format(self.adapter.address, path, self.adapter.token),
            wait_until="load",
        )
        page.wait_for_selector("#nav [data-cat]")
        return page

    def open_zone(self, page, category, zone):
        page.click('[data-cat="{}"]'.format(category))
        page.wait_for_selector('[data-panel="{}"]'.format(zone))
        page.click('[data-panel="{}"]'.format(zone))
        page.wait_for_selector(".panel")

    def open_zone_by_search(self, page, zone):
        """Walk the real nav until the entry shows up, so the category table is
        never duplicated here. Returns the panel heading it landed on."""
        current = ""
        heading = page.query_selector(".panel h2")
        if heading is not None:
            current = heading.inner_text()
        for category in ("company", "work", "model", "data"):
            page.click('[data-cat="{}"]'.format(category))
            entry = page.query_selector('[data-panel="{}"]'.format(zone))
            if entry is None:
                continue
            entry.click()
            # Opening a panel is async (it reloads the settings view first), so
            # waiting for any .panel would let the previous zone answer for it.
            page.wait_for_function(
                "previous => { const node = document.querySelector('.panel h2');"
                " return Boolean(node) && node.textContent"
                " && node.textContent !== previous; }",
                arg=current,
            )
            return page.eval_on_selector(".panel h2", "node => node.textContent")
        self.fail("分区入口不存在：" + zone)

    def wait_toast(self, page, needle):
        page.wait_for_function(
            "needle => { const el = document.querySelector('#toast');"
            " return el && el.textContent.indexOf(needle) >= 0; }",
            arg=needle,
        )

    def stored(self):
        return self.store.load_settings() or {}

    # -- zones ----------------------------------------------------------

    def test_the_rag_sliders_persist_and_the_library_stays_marked_absent(self):
        page = self.open_page()
        self.open_zone(page, "model", "rag")

        unwired = page.query_selector_all(".unwired")
        self.assertEqual(2, len(unwired))
        self.assertIsNone(page.query_selector(".unwired input, .unwired select, .unwired button"))
        self.assertIsNotNone(page.query_selector(".ctxbar-track"), "S03 的预算条没画出来")

        page.fill("#st-memory_top_k", "7")
        page.fill("#st-memory_score_threshold", "0.45")
        page.click("[data-save-settings]")
        self.wait_toast(page, "已保存并生效")

        saved = self.stored()
        self.assertEqual(7, saved["memory_top_k"])
        self.assertAlmostEqual(0.45, saved["memory_score_threshold"])
        self.assertEqual("7", page.input_value("#st-memory_top_k"))

    def test_the_rag_note_promises_only_what_an_offline_runtime_can_do(self):
        page = self.open_page()
        self.open_zone(page, "model", "rag")

        panel = page.inner_text(".panel")
        self.assertIn("Top-K 管两条链", panel)
        self.assertIn("阈值只作用于记忆链", panel)
        # This fixture runs offline, so the panel has to say the document chain
        # stays out of the prompt instead of advertising the 来源 channel.
        self.assertIn("离线模式", panel)
        self.assertIn("记忆与文档都不进上下文", panel)
        self.assertNotIn("文档命中会同时出现在右栏「来源」里", panel)

        # The half of the note that claims a working backend has to be true in
        # this same data directory, not just somewhere in the test suite.
        runtime = self.adapter.runtime
        runtime.documents.ingest(
            "deploy.md",
            (
                "# 部署手册\n## 回滚步骤\n"
                "回滚执行 rollback.sh，并把版本号写回上一个标签。\n"
            ).encode("utf-8"),
            persona_id="",
        )
        self.assertTrue(runtime.documents.search("回滚 版本号 标签", limit=5, persona_id=None))

    def test_the_mcp_panel_adds_a_server_and_switches_tools_for_real(self):
        stub = StubMcpTransport()
        runtime = self.adapter.runtime
        runtime.mcp = McpService(transport_factory=lambda spec: stub)
        self.addCleanup(runtime.mcp.close_all)
        page = self.open_page()
        self.open_zone(page, "model", "mcp")

        # 去灰显（切片 4）：这一区不再有任何「未接入」框，也不再有假的 0 个统计。
        self.assertEqual([], page.query_selector_all(".unwired"))
        self.assertIn("还没有添加 MCP 服务器", page.inner_text(".panel"))
        self.assertIsNone(page.query_selector(".stat-grid"))

        page.click("[data-mcp-add]")
        page.wait_for_selector(".dialog-scrim")
        page.fill("#mc-name", "Demo")
        page.fill("#mc-command", "python")
        page.fill("#mc-args", "server.py")
        page.fill("#mc-secrets", "API_TOKEN=super-secret-value")
        page.click(".dialog .dialog-ok")
        self.wait_toast(page, "已连接：发现 2 个工具")

        record = self.store.get_mcp_server("demo")
        self.assertIsNotNone(record)
        self.assertEqual([], record["tools"], "新加的服务器必须工具全关（S04b）")
        # 凭据只在 mcp_keys.json 里；界面、数据库都拿不到原文（Q2）。
        self.assertIn(
            "super-secret-value",
            (self.root / "mcp_keys.json").read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            b"super-secret-value", (self.root / "companion.db").read_bytes()
        )

        page.wait_for_selector("[data-mcp-enable]")
        self.assertNotIn("super-secret-value", page.inner_text(".panel"))
        self.assertIn("1 个服务", page.inner_text(".mcp-bar"))

        page.click('[data-mcp-toggle="demo"]')
        page.wait_for_selector("[data-mcp-tool]")
        switches = page.query_selector_all("[data-mcp-tool]")
        self.assertEqual(2, len(switches))
        self.assertEqual(
            ["false", "false"], [node.get_attribute("aria-pressed") for node in switches]
        )
        self.assertIn("远端写入", page.inner_text(".mcp-tools"))

        page.click('[data-mcp-tool="demo"][data-tool-name="echo_text"]')
        page.wait_for_function(
            "() => { const node = document.querySelector("
            '\'[data-mcp-tool="demo"][data-tool-name="echo_text"]\');'
            " return Boolean(node) && node.classList.contains('on'); }"
        )
        self.assertEqual(["echo_text"], self.store.get_mcp_server("demo")["tools"])
        self.assertEqual("1 / 2 个工具", page.inner_text(".mcp-tools-count"))

        page.click('[data-mcp-remove="demo"]')
        page.wait_for_selector(".dialog-scrim")
        self.assertIn("mcp_keys.json", page.inner_text(".dialog-message"))
        page.click(".dialog .dialog-ok")
        page.wait_for_selector(".empty-note")
        self.assertIsNone(self.store.get_mcp_server("demo"))
        self.assertEqual(
            {}, keys.server_credentials("demo"), "移除后凭据条目必须一起走"
        )

    def test_the_memory_switch_and_the_clear_action_act_on_the_store(self):
        self.store.add_message("main", "user", "我喜欢深色主题", AgentMode.CHAT)
        self.store.add_memory("main", "preference", "喜欢深色", 1, 0.9, 0.8, status="confirmed")
        self.store.add_memory("main", "fact", "周三有评审", 1, 0.8, 0.7, status="confirmed")
        self.store.add_memory(
            "main", "fact", "项目截止在下周", 1, 0.9, 0.9,
            status="confirmed", protected=True,
        )
        page = self.open_page()
        self.open_zone(page, "company", "memory")

        page.click("[data-switch]")
        page.click("[data-save-settings]")
        self.wait_toast(page, "已保存并生效")
        self.assertIs(self.stored()["memory_enabled"], False)

        button = page.query_selector("[data-purge-memories]")
        self.assertIn("清空 3 条", button.inner_text())
        button.click()
        page.wait_for_selector(".dialog-scrim")
        self.assertIn("不可撤销", page.inner_text(".dialog-message"))
        page.click(".dialog .dialog-ok")
        self.wait_toast(page, "已清空 2 条")

        kinds = [memory.content for memory in self.store.list_memories("main")]
        self.assertEqual(["项目截止在下周"], kinds)
        page.wait_for_function(
            "() => { const node = document.querySelector('[data-purge-memories]');"
            " return Boolean(node) && node.textContent.indexOf('清空 1 条') >= 0; }"
        )

    def test_the_persona_panel_writes_the_chosen_picture_into_the_slot(self):
        upload = self.root / "我的照片.png"
        upload.write_bytes(PNG)
        page = self.open_page()
        self.open_zone(page, "company", "persona")

        self.assertIsNone(page.query_selector(".persona-avatar"))
        self.assertIn("当前没有头像", page.inner_text(".panel"))

        page.set_input_files("#avatarFile", str(upload))
        page.click("[data-avatar-import]")
        self.wait_toast(page, "头像已导入")
        page.wait_for_selector(".persona-avatar")

        slots = sorted(path.name for path in (self.root / "assets").rglob("avatar.*"))
        self.assertEqual(["avatar.png"], slots)
        self.assertEqual(PNG, next((self.root / "assets").rglob("avatar.png")).read_bytes())
        self.assertEqual(PNG, upload.read_bytes(), "原始文件被动过")

    def test_the_advanced_zone_saves_the_tier_and_shows_real_diagnostics(self):
        page = self.open_page()
        self.open_zone(page, "data", "advanced")

        groups = page.eval_on_selector_all(
            ".group",
            "nodes => nodes.map(n => [n.open, n.querySelector('summary').textContent])",
        )
        self.assertEqual([True, False], [item[0] for item in groups])
        self.assertIn("权限与确认", groups[0][1])
        self.assertIn("诊断", groups[1][1])
        options = page.eval_on_selector_all(
            "#st-tool_permission option", "nodes => nodes.map(n => n.value)"
        )
        self.assertEqual(["strict", "balanced", "relaxed"], options)

        page.select_option("#st-tool_permission", "relaxed")
        page.fill("#st-max_tool_rounds", "5")
        page.click("[data-save-settings]")
        self.wait_toast(page, "已保存并生效")
        saved = self.stored()
        self.assertEqual("relaxed", saved["tool_permission"])
        self.assertEqual(5, saved["max_tool_rounds"])
        self.assertEqual("relaxed", self.adapter.permission_view()["current"])

        grid = page.eval_on_selector(".diag-grid", "node => node.textContent")
        self.assertIn(self.root.name, grid.replace("\\", "/"))
        self.assertIn("v{}".format(SCHEMA_VERSION), grid)
        self.assertIn("python doctor.py", grid + page.eval_on_selector(
            ".group:last-of-type", "node => node.textContent"
        ))

    def test_each_zone_reaches_its_own_panel_and_holds_at_200_percent(self):
        zones = ("persona", "rag", "mcp", "memory", "advanced")
        _post_ui(self.adapter, {"font_scale": 200})
        page = self.open_page()
        page.set_viewport_size({"width": 680, "height": 560})

        titles = []
        for zone in zones:
            with self.subTest(zone=zone):
                titles.append(self.open_zone_by_search(page, zone))
                self.assertEqual([], page.evaluate(PROBE, "#closeBtn")["problems"])
        self.assertEqual(len(set(titles)), len(zones), titles)


if __name__ == "__main__":
    unittest.main()
