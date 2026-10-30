"""Real-browser regression for the keyed transcript (M12.3-d).

The web transcript must reconcile by message id instead of rebuilding itself:
history nodes keep their identity across refreshes, a live run only owns the
stream row, and the stream row survives mid-run refreshes and is removed when
the run ends. Playwright is optional; the probe is plain DOM identity.
"""

import contextlib
import json
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from agent_framework.companion.app import create_application_service
from agent_framework.companion.models import AgentMode
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import CompanionStore
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


@unittest.skipIf(sync_playwright is None, "未安装 playwright（见 requirements-optional.txt）")
class KeyedTranscriptBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix="companion-chat-")
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
        root = Path(self.temp.name)
        store = CompanionStore(root / "companion.db")
        runtime = CompanionRuntime(
            store=store,
            llm_client=OfflineLLMClient(),
            search_service=SearchService(StubSearch()),
            data_dir=root,
            session_id="main",
            offline=True,
        )
        runtime.select_workspace(str(root))
        self.service = create_application_service(runtime)
        self.adapter = WebAdapter(self.service, data_dir=root).start()
        self.store = self.adapter.runtime.store
        self.page = None

    def tearDown(self):
        if self.page is not None:
            self.page.close()
        self.adapter.stop()
        self.temp.cleanup()

    # -- helpers --------------------------------------------------------

    def seed(self, count):
        for index in range(count):
            role = "user" if index % 2 == 0 else "assistant"
            self.store.add_message(
                "main", role, "第 {} 条历史消息".format(index + 1), AgentMode.CHAT
            )

    def open_page(self):
        page = self.browser.new_page(viewport={"width": 1180, "height": 860})
        self.page = page
        page.goto(
            "http://{}/?t={}".format(self.adapter.address, self.adapter.token),
            wait_until="load",
        )
        return page

    def wait_for(self, page, expression, description, timeout=20):
        """Poll the page, nudging it with refreshes so the check never depends
        on how fast the event stream happened to connect. The nudge is slower
        than the page's 140ms refresh debounce so a pending render can land."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.adapter.broadcast({"type": "refresh"})
            time.sleep(0.3)
            try:
                if page.evaluate("Boolean({})".format(expression)):
                    return
            except Exception:
                pass
        self.fail("condition not met: " + description)

    def count_nodes(self, page):
        return page.evaluate(
            "document.querySelectorAll('#msgList [data-mid]').length"
        )

    def remember_nodes(self, page, name):
        page.evaluate(
            """(name) => {
              const nodes = {};
              document.querySelectorAll('#msgList [data-key]').forEach((node) => {
                nodes[node.dataset.key] = node;
              });
              window[name] = nodes;
            }""",
            name,
        )

    def assert_nodes_kept(self, page, name):
        kept = page.evaluate(
            """(name) => {
              const nodes = window[name];
              return Object.entries(nodes).every(([key, node]) => {
                const now = document.querySelector('#msgList [data-key="' + key + '"]');
                return node === now && node.isConnected;
              });
            }""",
            name,
        )
        self.assertTrue(kept, "历史消息节点被重建了")

    # -- tests ----------------------------------------------------------

    def test_history_nodes_survive_appends(self):
        self.seed(24)
        page = self.open_page()
        self.wait_for(page, "document.querySelectorAll('#msgList [data-mid]').length === 24", "24 条历史")
        self.remember_nodes(page, "__history")
        self.assertGreaterEqual(
            page.evaluate("document.querySelectorAll('#msgList .day').length"), 1
        )

        for index in range(3):
            self.store.add_message(
                "main", "user", "新增 {}".format(index), AgentMode.CHAT
            )
            self.wait_for(
                page,
                "document.querySelectorAll('#msgList [data-mid]').length === {}".format(
                    25 + index
                ),
                "第 {} 条新消息".format(index + 1),
            )
            self.assert_nodes_kept(page, "__history")

        self.assertEqual(27, self.count_nodes(page))
        self.assertEqual(
            27,
            page.evaluate("new Set(Array.from(document.querySelectorAll('#msgList [data-mid]')).map((n) => n.dataset.mid)).size"),
        )

    def test_a_live_run_only_owns_the_stream_row(self):
        self.seed(20)
        page = self.open_page()
        self.wait_for(page, "document.querySelectorAll('#msgList [data-mid]').length === 20", "20 条历史")
        self.remember_nodes(page, "__history")
        page.evaluate(
            """() => {
              window.__first = document.querySelector('#msgList [data-mid]');
            }"""
        )

        release = threading.Event()

        def slow_stream(text, mode, generation_id="", cancel_event=None):
            yield {"kind": "delta", "text": "一"}
            yield {"kind": "delta", "text": "二"}
            yield {"kind": "tool", "tool_name": "search_notes", "state": "running"}
            release.wait(20)
            self.store.add_message("main", "assistant", "一二", AgentMode.CHAT)
            yield {
                "kind": "done",
                "result": {
                    "answer": "一二",
                    "proposal": None,
                    "receipt": None,
                    "task": None,
                    "task_receipt": None,
                },
            }

        self.adapter.runtime.stream_message = slow_stream
        try:
            status, _ = self.request("/api/turn", {"text": "时间线", "mode": "chat"})
            self.assertEqual(202, status)
            self.wait_for(page, "document.querySelector('#streamRow')", "流式行出现")
            page.evaluate(
                """() => {
                  window.__stream = document.querySelector('#streamRow');
                }"""
            )
            self.assert_nodes_kept(page, "__history")

            self.store.add_message("main", "user", "运行中追加", AgentMode.CHAT)
            self.wait_for(
                page,
                "document.querySelectorAll('#msgList [data-mid]').length === 21",
                "运行中追加的消息",
            )
            self.assert_nodes_kept(page, "__history")
            kept_stream = page.evaluate(
                "window.__stream === document.querySelector('#streamRow')"
            )
            self.assertTrue(kept_stream, "刷新把流式行重建了")
        finally:
            release.set()

        self.wait_for(
            page,
            "!document.querySelector('#streamRow') && "
            "document.querySelectorAll('#msgList [data-mid]').length === 22",
            "运行结束并落到历史",
        )
        self.assertTrue(
            page.evaluate("window.__first === document.querySelector('#msgList [data-mid]')"),
            "首条历史节点被重建了",
        )
        self.assert_nodes_kept(page, "__history")
        self.assertGreaterEqual(
            page.evaluate("document.querySelectorAll('#msgList .day').length"), 1
        )

    def test_the_empty_state_yields_to_the_first_message(self):
        page = self.open_page()
        self.wait_for(
            page,
            "document.querySelector('#chatEmpty') && "
            "!document.querySelector('#chatEmpty').hidden",
            "空状态可见",
        )
        self.store.add_message("main", "user", "你好", AgentMode.CHAT)
        self.wait_for(
            page,
            "document.querySelectorAll('#msgList [data-mid]').length === 1 && "
            "document.querySelector('#chatEmpty').hidden",
            "空状态让位给消息",
        )

    # -- message actions ------------------------------------------------

    def seed_turn(self, text="你好"):
        self.service.run_sync(text)

    def test_actions_stay_hidden_until_the_bubble_is_reached(self):
        self.seed_turn()
        page = self.open_page()
        self.wait_for(
            page,
            "document.querySelectorAll('#msgList [data-mid]').length === 2",
            "两轮消息已渲染",
        )
        regenerate = page.locator('#msgList .msg.ag [data-msg-act="regenerate"]')
        self.assertEqual(1, regenerate.count())
        self.assertEqual(
            0, page.locator('#msgList .msg.me [data-msg-act="regenerate"]').count()
        )
        self.assertEqual("0", regenerate.evaluate("el => getComputedStyle(el).opacity"))
        page.hover("#msgList .msg.ag .bubble")
        self.wait_for(
            page,
            "Number(getComputedStyle(document.querySelector('#msgList .msg.ag [data-msg-act=\"regenerate\"]')).opacity) > 0.95",
            "悬停后按钮显形",
        )
        page.mouse.move(0, 0)
        regenerate.focus()
        self.wait_for(
            page,
            "Number(getComputedStyle(document.querySelector('#msgList .msg.ag [data-msg-act=\"regenerate\"]')).opacity) > 0.95",
            "键盘聚焦后按钮显形",
        )

    def test_regenerate_replaces_the_latest_bubble_in_place(self):
        self.seed_turn()
        page = self.open_page()
        self.wait_for(
            page,
            "document.querySelectorAll('#msgList [data-mid]').length === 2",
            "两轮消息已渲染",
        )
        old_id = page.evaluate(
            "Number(document.querySelectorAll('#msgList [data-mid]')[1].dataset.mid)"
        )
        page.hover("#msgList .msg.ag .bubble")
        page.click('#msgList .msg.ag [data-msg-act="regenerate"]')
        self.wait_for(
            page,
            "document.querySelectorAll('#msgList [data-mid]').length === 2 && "
            "Number(document.querySelectorAll('#msgList [data-mid]')[1].dataset.mid) !== {}".format(old_id),
            "最新回复已被替换",
        )
        roles = page.evaluate(
            "Array.from(document.querySelectorAll('#msgList [data-mid]')).map((node) => node.className)"
        )
        self.assertIn("me", roles[0])
        self.assertIn("ag", roles[1])

    def test_remember_reports_the_saved_memory(self):
        self.seed_turn()
        page = self.open_page()
        self.wait_for(
            page,
            "document.querySelectorAll('#msgList [data-mid]').length === 2",
            "两轮消息已渲染",
        )
        page.hover("#msgList .msg.ag .bubble")
        page.click('#msgList .msg.ag [data-msg-act="remember"]')
        self.wait_for(
            page,
            "document.querySelector('#toast').textContent.includes('已保存为长期记忆')",
            "记忆保存提示",
        )

    def test_source_action_opens_the_evidence_tab(self):
        self.seed_turn()
        self.adapter.runtime.last_tool_sources = [
            {"title": "测试来源", "url": "https://example.com", "snippet": ""}
        ]
        page = self.open_page()
        self.wait_for(
            page,
            "document.querySelector('#msgList .msg.ag [data-msg-act=\"source\"]') !== null",
            "来源入口出现",
        )
        page.hover("#msgList .msg.ag .bubble")
        page.click('#msgList .msg.ag [data-msg-act="source"]')
        self.wait_for(
            page,
            "document.querySelector('[data-tab=\"来源\"]').getAttribute('aria-selected') === 'true' && "
            "document.querySelector('#inspectorBody').textContent.includes('测试来源')",
            "来源面板已打开",
        )

    # -- HTTP -----------------------------------------------------------

    def request(self, path, body):
        request = urllib.request.Request(
            "http://{}{}".format(self.adapter.address, path),
            data=json.dumps(body).encode("utf-8"),
            headers={
                "X-Companion-Token": self.adapter.token,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
