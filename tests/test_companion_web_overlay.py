"""Desktop overlay: proactive queue payload, geometry helpers and browser flows."""

import contextlib
import json
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from agent_framework.companion.app import create_application_service
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.companion.ui.state import (
    UiStateStore,
    clamp_position,
    snap_to_edges,
)
from agent_framework.companion.web import WebAdapter
from agent_framework.companion.web.layout_audit import _scratch_temp
from agent_framework.companion.web.shell import _rect_matches
from agent_framework.llm import OfflineLLMClient

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


class StubSearch:
    def search(self, query, limit=5):
        return []


def make_adapter(root):
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
    service = create_application_service(runtime)
    return service, WebAdapter(service, data_dir=root)


class OverlayGeometryTests(unittest.TestCase):
    def test_snap_to_edges_pulls_flush_only_when_near(self):
        screens = [(0, 0, 1920, 1080)]
        self.assertEqual((0, 0), snap_to_edges(12, 9, 300, 250, screens))
        self.assertEqual((1620, 830), snap_to_edges(1618, 832, 300, 250, screens))
        self.assertEqual((900, 400), snap_to_edges(900, 400, 300, 250, screens))

    def test_snap_ignores_offscreen_and_missing_screens(self):
        self.assertEqual((50, 60), snap_to_edges(50, 60, 300, 250, []))
        screens = [(-1920, 0, 1920, 1080), (0, 0, 1920, 1080)]
        self.assertEqual((-1920, 0), snap_to_edges(-1910, 8, 300, 250, screens))

    def test_clamp_keeps_its_own_contract(self):
        screens = [(0, 0, 1920, 1080)]
        self.assertEqual((1620, 830), clamp_position(5000, 5000, 300, 250, screens))

    def test_collapsed_flag_round_trips(self):
        with tempfile.TemporaryDirectory() as folder:
            store = UiStateStore(folder)
            self.assertFalse(store.overlay_collapsed())
            store.set_overlay_collapsed(True)
            self.assertTrue(UiStateStore(folder).overlay_collapsed())
            store.set_overlay_collapsed(False)
            self.assertFalse(store.overlay_collapsed())

    def test_rect_matches_allows_dpi_rounding(self):
        self.assertTrue(_rect_matches((0, 0, 1920, 1080), (1, 0, 1919, 1080)))
        self.assertFalse(_rect_matches((0, 0, 1920, 1040), (0, 0, 1920, 1080)))


class OverlayPayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service, self.adapter = make_adapter(self.root)
        self.adapter.start()
        self.store = self.service.runtime.store

    def tearDown(self):
        self.adapter.stop()
        self.temp.cleanup()

    def seed_event(self, content, read=False):
        event = self.store.add_proactive_event(
            session_id="main",
            source_type="memory",
            source_id="mem-1",
            trigger_reason="测试",
            content=content,
            status="delivered",
            read_at="2026-10-30T10:00:00+00:00" if read else None,
        )
        return event

    def test_unread_messages_reach_the_overlay_newest_first(self):
        old = self.seed_event("先喝口水")
        new = self.seed_event("再看远处")
        self.seed_event("已经读过了", read=True)
        payload = self.adapter.overlay()
        self.assertTrue(payload["proactive"]["enabled"])
        ids = [item["id"] for item in payload["proactive"]["unread"]]
        self.assertEqual([new.event_id, old.event_id], ids)
        self.assertEqual("再看远处", payload["proactive"]["unread"][0]["text"])

    def test_do_not_disturb_empties_the_queue(self):
        self.seed_event("先喝口水")
        self.service.runtime.update_proactive_settings(enabled=False)
        payload = self.adapter.overlay()
        self.assertFalse(payload["proactive"]["enabled"])
        self.assertEqual([], payload["proactive"]["unread"])

    def test_queue_is_capped_at_three(self):
        for index in range(5):
            self.seed_event("第 {} 条".format(index))
        payload = self.adapter.overlay()
        self.assertEqual(3, len(payload["proactive"]["unread"]))

    def test_collapsed_state_round_trips_through_the_ui_endpoint(self):
        body = json.dumps({"overlay_collapsed": True}).encode("utf-8")
        request = urllib.request.Request(
            "http://{}/api/ui".format(self.adapter.address),
            data=body,
            headers={
                "X-Companion-Token": self.adapter.token,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(200, response.status)
        self.assertTrue(self.adapter.overlay()["collapsed"])
        self.assertTrue(UiStateStore(self.root).overlay_collapsed())


@unittest.skipIf(sync_playwright is None, "未安装 playwright（见 requirements-optional.txt）")
class OverlayBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix="companion-overlay-")
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
        self.service, self.adapter = make_adapter(root)
        self.adapter.start()
        self.store = self.service.runtime.store
        self.context = self.browser.new_context(viewport={"width": 300, "height": 250})
        self.page = None

    def tearDown(self):
        self.context.close()
        self.adapter.stop()
        self.temp.cleanup()

    def open_overlay(self):
        page = self.context.new_page()
        # The page opens chat pages with window.open; keep the test single-tab.
        page.add_init_script("window.open = () => null;")
        self.page = page
        page.goto(
            "http://{}/overlay?t={}".format(self.adapter.address, self.adapter.token),
            wait_until="load",
        )
        return page

    def wait_for(self, expression, description, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.15)
            try:
                if self.page.evaluate("Boolean({})".format(expression)):
                    return
            except Exception:
                pass
        self.fail("condition not met: " + description)

    def seed_event(self, content):
        return self.store.add_proactive_event(
            session_id="main",
            source_type="memory",
            source_id="mem-1",
            trigger_reason="测试",
            content=content,
            status="delivered",
        )

    def test_unread_message_speaks_once_and_click_marks_it_read(self):
        event = self.seed_event("该起来喝水了")
        page = self.open_overlay()
        self.wait_for(
            "document.querySelector('#bubbleText').textContent.includes('该起来喝水了')",
            "气泡显示主动消息",
        )
        page.evaluate("document.querySelector('#bubble').click()")
        deadline = time.time() + 10
        while time.time() < deadline:
            if self.store.list_proactive_events("main")[0].read_at:
                break
            time.sleep(0.15)
        self.assertTrue(self.store.list_proactive_events("main")[0].read_at)
        self.wait_for(
            "!document.querySelector('#bubble').classList.contains('show')",
            "点击后气泡收起",
        )

    def test_messages_are_spoken_one_at_a_time_in_order(self):
        self.seed_event("第一条")
        self.seed_event("第二条")
        page = self.open_overlay()
        self.wait_for(
            "document.querySelector('#bubbleText').textContent.includes('第一条')",
            "先出现最早的一条",
        )
        page.evaluate("document.querySelector('#bubble').click()")
        self.wait_for(
            "document.querySelector('#bubbleText').textContent.includes('第二条')",
            "确认第一条后才轮到第二条",
        )

    def test_broadcast_pushes_without_waiting_for_the_poll(self):
        page = self.open_overlay()
        self.wait_for(
            "document.querySelector('#status').textContent.length > 0",
            "浮窗完成首轮加载",
        )
        self.seed_event("定时器还没到，也应当出现")
        self.adapter.broadcast({"type": "refresh"})
        self.wait_for(
            "document.querySelector('#bubbleText').textContent.includes('也应当出现')",
            "事件广播推送主动消息",
            timeout=5,
        )

    def test_context_menu_toggles_do_not_disturb(self):
        page = self.open_overlay()
        self.wait_for(
            "document.querySelector('#status').textContent.length > 0",
            "浮窗完成首轮加载",
        )
        page.evaluate(
            "document.querySelector('#overlay').dispatchEvent("
            "new MouseEvent('contextmenu', {bubbles: true, clientX: 40, clientY: 40}))"
        )
        self.wait_for(
            "!document.querySelector('#menu').hidden",
            "右键菜单打开",
        )
        self.assertIn("勿扰：关", page.inner_text("#menuDnd"))
        page.click("#menuDnd")
        deadline = time.time() + 10
        while time.time() < deadline:
            if not self.service.runtime.proactive_status()["enabled"]:
                break
            time.sleep(0.15)
        self.assertFalse(self.service.runtime.proactive_status()["enabled"])
        self.wait_for(
            "document.querySelector('#menuDnd').textContent.includes('勿扰：开')",
            "菜单反映勿扰状态",
        )

    def test_collapse_hides_the_pet_and_persists(self):
        page = self.open_overlay()
        self.wait_for(
            "document.querySelector('#status').textContent.length > 0",
            "浮窗完成首轮加载",
        )
        page.hover("#pet")
        page.click("#quickCollapse")
        self.wait_for(
            "document.body.classList.contains('collapsed') && "
            "document.querySelector('#ball').hidden === false && "
            "document.querySelector('#pet').hidden === true",
            "折叠成小球",
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            if self.adapter.ui_state.overlay_collapsed():
                break
            time.sleep(0.15)
        self.assertTrue(self.adapter.ui_state.overlay_collapsed())
        page.reload(wait_until="load")
        self.wait_for(
            "document.body.classList.contains('collapsed')",
            "刷新后仍保持折叠",
        )
        self.assertEqual("56px", page.evaluate(
            "getComputedStyle(document.querySelector('#ball')).width"
        ))


if __name__ == "__main__":
    unittest.main()
