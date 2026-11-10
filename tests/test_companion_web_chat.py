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
from dataclasses import replace
from pathlib import Path

from agent_framework.companion.app import create_application_service
from agent_framework.companion.documents import source_items
from agent_framework.companion.models import AgentMode, WORK_NORMAL
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
        self.root = root
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
        page.wait_for_selector('body[data-ready="true"]')
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

    def test_task_approve_button_submits_once_without_a_second_dialog(self):
        task = self.adapter.runtime.create_work_task(
            "把今天的总结写入项目文件", force=True
        )
        stored_task = self.store.get_work_task(task["task"].task_id)
        stored_task.card = replace(stored_task.card, executor="测试转发器")
        self.store.update_work_task(stored_task)
        self.adapter.runtime.work.executor = (
            lambda card, receipt: {
                "status": "completed",
                "summary": "测试转发完成",
            }
        )
        page = self.open_page()
        if not page.locator("#inspector").is_visible():
            page.click("#toggleInspector")
        page.get_by_role("tab", name="任务", exact=True).click()
        page.wait_for_selector('[data-task-act="approve"]')
        page.click('[data-task-act="approve"]')
        self.wait_for(
            page,
            "document.querySelectorAll('[role=\"dialog\"]').length === 0",
            "批准后没有二次弹窗",
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            current = self.store.get_work_task(task["task"].task_id)
            receipt = self.store.get_work_receipt_for_task(task["task"].task_id)
            if (
                current is not None
                and current.state == WORK_NORMAL
                and receipt is not None
                and receipt.status == "completed"
            ):
                break
            time.sleep(0.2)
        else:
            self.fail("任务卡批准按钮没有完成转发回执")

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

    def test_a_document_source_opens_to_its_chunk_and_its_original_file(self):
        runtime = self.adapter.runtime
        runtime.documents.ingest(
            "deploy.md",
            (
                "# 部署手册\n## 前置检查\n部署前先确认数据库备份已经完成。\n"
                "## 回滚步骤\n回滚执行 rollback.sh，并把版本号写回上一个标签。\n"
            ).encode("utf-8"),
            persona_id="",
        )
        self.seed_turn()
        hits = runtime.documents.search("回滚 版本号 标签", limit=5, persona_id=None)
        runtime.last_doc_sources = source_items(hits)
        runtime.last_tool_sources = [
            {"title": "测试来源", "url": "https://example.com", "snippet": "网页摘录"}
        ]

        page = self.open_page()
        # Clicking the tab before the first bootstrap lands is a click on a
        # button nobody has wired yet, so wait for the transcript to render.
        self.wait_for(
            page,
            "document.querySelectorAll('#msgList [data-mid]').length === 2",
            "首轮消息已渲染",
        )
        page.click('[data-tab="来源"]')
        self.wait_for(
            page,
            "document.querySelectorAll('.source-card .source-toggle').length === {}".format(
                len(runtime.last_doc_sources) + 1
            ),
            "来源条目已渲染",
        )

        self.assertEqual(0, page.locator(".source-detail").count())
        doc = page.locator(".source-toggle", has_text="deploy.md · 第 3 块")
        self.assertEqual(1, doc.count())
        self.assertEqual("本机文档", doc.locator(".kind").inner_text())

        doc.click()
        page.wait_for_selector(".source-detail")
        self.assertEqual("true", doc.get_attribute("aria-expanded"))

        card = page.locator(".source-item", has_text="deploy.md · 第 3 块")
        text = card.inner_text()
        self.assertIn("块号：第 3 块", text)
        self.assertIn("小节：部署手册 > 回滚步骤", text)
        self.assertIn("摘录：", text)
        self.assertIn("rollback.sh", text)
        path = [
            line.split("：", 1)[1].strip()
            for line in text.splitlines()
            if line.startswith("原文：")
        ][0]
        # 定位 has to mean the real file, in this test's own data directory.
        self.assertTrue((self.root / path).is_file(), path)
        self.assertIn("rollback.sh", (self.root / path).read_text(encoding="utf-8"))

        web = page.locator(".source-toggle", has_text="测试来源")
        self.assertEqual("网页", web.locator(".kind").inner_text())
        web.click()
        self.assertIn(
            "地址：https://example.com", web.locator("xpath=../div").inner_text()
        )

        doc.click()
        page.wait_for_function(
            "() => !document.body.textContent.includes('块号：第 3 块')"
        )
        self.assertEqual("false", doc.get_attribute("aria-expanded"))

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
