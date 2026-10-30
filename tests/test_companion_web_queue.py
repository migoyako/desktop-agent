"""Real-browser regression for the busy-time pending queue (M12.3 C5).

Typing while a run is active must not be refused: the text waits in the dock,
drains one turn per run in order, can be withdrawn or edited before it is sent,
and a flush that fails keeps the item instead of dropping it. Playwright is
optional; the checks are plain DOM reads plus the store.
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
class PendingQueueBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix="companion-queue-")
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
        self.release = threading.Event()
        self.calls = []
        self.page = None

    def tearDown(self):
        self.release.set()
        if self.page is not None:
            self.page.close()
        self.adapter.stop()
        self.temp.cleanup()

    # -- helpers --------------------------------------------------------

    def install_stream(self):
        """The first turn streams until released; later turns answer at once."""
        calls = self.calls
        release = self.release
        store = self.store

        def stream(text, mode, generation_id="", cancel_event=None):
            calls.append(text)
            store.add_message("main", "user", text, mode)
            if len(calls) == 1:
                yield {"kind": "delta", "text": "稍等"}
                release.wait(60)
            answer = "回复：" + text
            store.add_message("main", "assistant", answer, AgentMode.CHAT)
            yield {"kind": "delta", "text": answer}
            yield {
                "kind": "done",
                "result": {
                    "answer": answer,
                    "proposal": None,
                    "receipt": None,
                    "task": None,
                    "task_receipt": None,
                },
            }

        self.adapter.runtime.stream_message = stream

    def start_turn(self, text):
        status, payload = self.request("/api/turn", {"text": text, "mode": "chat"})
        self.assertEqual(202, status)
        return payload

    def user_texts(self):
        return [
            row["content"]
            for row in self.store.list_messages("main", limit=50)
            if row["role"] == "user"
        ]

    def open_page(self):
        page = self.browser.new_page(viewport={"width": 1180, "height": 860})
        self.page = page
        page.goto(
            "http://{}/?t={}".format(self.adapter.address, self.adapter.token),
            wait_until="load",
        )
        return page

    def wait_for(self, page, expression, description, timeout=30):
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

    RUNNING = "document.querySelector('#send').classList.contains('stop')"

    # -- tests ----------------------------------------------------------

    def test_input_typed_during_a_run_waits_in_the_dock(self):
        page = self.open_page()
        self.install_stream()
        self.start_turn("第一条")
        self.wait_for(page, self.RUNNING, "前端进入运行态")

        page.fill("#input", "排在后面的话")
        page.press("#input", "Enter")
        self.wait_for(page, self.queue_count_expr(1), "待发队列出现")

        self.assertEqual("", page.eval_on_selector("#input", "node => node.value"))
        self.assertIn("待发队列", page.inner_text("#toast"))
        self.assertEqual(["第一条"], self.user_texts())

        self.release.set()
        self.wait_for(
            page,
            "document.querySelector('#queueDock').hidden && "
            "document.querySelectorAll('#msgList [data-mid]').length === 4",
            "队列消息自动发送并落到历史",
        )
        self.assertEqual(["第一条", "排在后面的话"], self.user_texts())

    def test_queued_turns_are_sent_in_order(self):
        page = self.open_page()
        self.install_stream()
        self.start_turn("第一条")
        self.wait_for(page, self.RUNNING, "前端进入运行态")
        for text in ("第二条", "第三条"):
            page.fill("#input", text)
            page.press("#input", "Enter")
        self.wait_for(page, self.queue_count_expr(2), "两条待发")
        self.assertEqual(["第一条"], self.user_texts())

        self.release.set()
        self.wait_for(
            page,
            "document.querySelector('#queueDock').hidden && "
            "document.querySelectorAll('#msgList [data-mid]').length === 6",
            "三条消息与三条回复",
        )
        self.assertEqual(["第一条", "第二条", "第三条"], self.user_texts())
        self.assertEqual(["第一条", "第二条", "第三条"], self.calls)

    def test_a_queued_item_can_be_withdrawn_or_edited(self):
        page = self.open_page()
        self.install_stream()
        self.start_turn("第一条")
        self.wait_for(page, self.RUNNING, "前端进入运行态")
        for text in ("会被撤回", "会被编辑"):
            page.fill("#input", text)
            page.press("#input", "Enter")
        self.wait_for(page, self.queue_count_expr(2), "两条待发")

        page.click("#queueList .queue-item:nth-child(1) [data-queue-drop]")
        self.wait_for(page, self.queue_count_expr(1), "撤回一条")
        self.assertIn("已撤回", page.inner_text("#toast"))

        page.click("#queueList .queue-item [data-queue-edit]")
        self.wait_for(page, "document.querySelector('#queueDock').hidden", "编辑取回后队列为空")
        self.assertEqual("会被编辑", page.eval_on_selector("#input", "node => node.value"))
        self.assertTrue(
            page.evaluate("document.activeElement === document.querySelector('#input')"),
            "编辑后输入框应获得焦点",
        )

        self.release.set()
        self.wait_for(
            page,
            "!document.querySelector('#streamRow') && "
            "document.querySelectorAll('#msgList [data-mid]').length === 2",
            "首轮结束且没有额外发送",
        )
        time.sleep(0.6)
        self.assertEqual(["第一条"], self.user_texts())

    def test_a_failed_flush_keeps_the_item_and_a_switch_clears_it(self):
        page = self.open_page()
        self.install_stream()
        self.start_turn("第一条")
        self.wait_for(page, self.RUNNING, "前端进入运行态")

        page.fill("#input", "发不出去的话")
        page.press("#input", "Enter")
        self.wait_for(page, self.queue_count_expr(1), "一条待发")

        page.evaluate(
            """() => {
              window.__realFetch = window.fetch;
              window.fetch = (url, options) => String(url).indexOf('/api/turn') >= 0
                ? Promise.resolve(new Response(JSON.stringify({error: '模拟拒绝'}), {
                    status: 409, headers: {'Content-Type': 'application/json'},
                  }))
                : window.__realFetch(url, options);
            }"""
        )
        self.release.set()
        self.wait_for(
            page,
            "document.querySelectorAll('#queueList .queue-item').length === 1 && "
            "document.querySelector('#toast').textContent.indexOf('没有发出去') >= 0",
            "提交失败后保留待发项",
        )
        self.assertEqual(["第一条"], self.user_texts())

        # Switching sessions anywhere (here through the API) clears the dock with
        # a notice, because the queued text belongs to the session it was typed in.
        status, payload = self.request("/api/sessions/create", {"mode": "chat"})
        self.assertEqual(200, status)
        self.assertNotEqual("main", payload["session_id"])
        self.wait_for(
            page,
            "document.querySelector('#queueDock').hidden && "
            "document.querySelector('#toast').textContent.indexOf('待发队列已清空') >= 0",
            "切换会话清空队列",
        )
        self.assertEqual(0, page.evaluate("document.querySelectorAll('#queueList .queue-item').length"))

    def test_the_dock_keeps_its_geometry_at_two_hundred_percent(self):
        page = self.open_page()
        self.install_stream()
        self.start_turn("第一条")
        self.wait_for(page, self.RUNNING, "前端进入运行态")
        for text in (
            "排队的第一条消息：把周报里的口径再核对一遍，尤其是数字部分",
            "排队的第二条",
        ):
            page.fill("#input", text)
            page.press("#input", "Enter")
        self.wait_for(page, self.queue_count_expr(2), "两条待发")

        status, _payload = self.request("/api/ui", {"font_scale": 200})
        self.assertEqual(200, status)
        page.set_viewport_size({"width": 780, "height": 560})
        self.wait_for(
            page,
            "getComputedStyle(document.documentElement).getPropertyValue('--scale').trim() === '2'",
            "字号档位生效",
        )
        layout = page.evaluate(
            """() => {
              const composer = document.querySelector('#composer');
              const dock = document.querySelector('#queueDock');
              const input = document.querySelector('#input');
              const send = document.querySelector('#send');
              const chat = document.querySelector('#chat');
              const boxes = (node) => node.getBoundingClientRect();
              return {
                overflowX: composer.scrollWidth - composer.clientWidth,
                dockHidden: dock.hidden,
                dockWidth: Math.round(boxes(dock).width),
                composerWidth: Math.round(boxes(composer).width),
                composerHeight: Math.round(boxes(composer).height),
                dockHeight: Math.round(boxes(dock).height),
                listHeight: Math.round(boxes(document.querySelector('#queueList')).height),
                runHeight: Math.round(boxes(document.querySelector('#runStrip')).height),
                pillsHeight: Math.round(boxes(document.querySelector('#pills')).height),
                inputHeight: Math.round(boxes(input).height),
                footHeight: Math.round(boxes(document.querySelector('.composer-foot')).height),
                statusHeight: Math.round(boxes(document.querySelector('.statusbar')).height),
                mainHeight: Math.round(boxes(document.querySelector('.main')).height),
                mainScroll: document.querySelector('.main').scrollHeight,
                buttonHeights: Array.from(
                  document.querySelectorAll('#queueList .queue-item button')
                ).map((node) => Math.round(boxes(node).height)),
                inputInside: boxes(input).bottom <= boxes(composer).bottom + 1,
                sendInside: boxes(send).bottom <= window.innerHeight + 1,
                chat: Math.round(boxes(chat).height),
                viewport: window.innerHeight,
              };
            }"""
        )
        self.assertEqual(0, layout["overflowX"])
        self.assertFalse(layout["dockHidden"])
        self.assertLessEqual(layout["dockWidth"], layout["composerWidth"])
        self.assertTrue(all(h >= 16 for h in layout["buttonHeights"]), layout)
        self.assertTrue(layout["inputInside"], layout)
        self.assertTrue(layout["sendInside"], layout)
        # The run bar, the dock and 200% type in the minimum window leave little
        # room; the dock must shrink (it scrolls) rather than squeeze the chat away.
        self.assertGreater(layout["chat"], 0, layout)
        self.assertLessEqual(layout["mainScroll"], layout["viewport"] + 1, layout)

    # -- expressions ----------------------------------------------------

    @staticmethod
    def queue_count_expr(count):
        return (
            "document.querySelectorAll('#queueList .queue-item').length === {}".format(
                count
            )
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


if __name__ == "__main__":
    unittest.main()
