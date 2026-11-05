"""Desktop overlay: proactive queue payload, geometry helpers and browser flows."""

import base64
import contextlib
import json
import tempfile
import time
import unittest
import urllib.request
import zipfile
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
from agent_framework.companion.web.shell import (
    MAIN_SIZE,
    _cursor_script,
    _hit_script,
    _rect_matches,
    overlay_size_for,
)
from agent_framework.llm import OfflineLLMClient

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

HOST_STUB = """
window.__hostCalls = [];
window.__host = {
  moveOverlay: function (x, y) { window.__hostCalls.push("overlay:" + x + "," + y); },
  snapOverlay: function () { window.__hostCalls.push("snap"); },
};
window.qt = { webChannelTransport: {} };
"""

QWEBCHANNEL_STUB = """
window.QWebChannel = function (transport, callback) {
  callback({ objects: { host: window.__host } });
};
"""


def write_mesh_archive(root):
    """A structurally valid archive whose sparse rig the runtime will reject."""
    folder = root / "assets" / "remielle"
    folder.mkdir(parents=True)
    curve = lambda x0, x1: {
        "x0": x0,
        "x1": x1,
        "top": [0.0] * 24,
        "bot": [1.0] * 24,
    }
    files = {
        "avatar.json": json.dumps(
            {"format": "mesh-avatar", "version": 1, "name": "overlay-mesh"}
        ),
        "rig.json": json.dumps({"version": 1}),
        "built/layers.json": json.dumps(
            {
                "size": [16, 16],
                "layers": {"eye0_ball": [0, 0, 2, 2]},
                "eyes": [curve(2, 4), curve(8, 10)],
            }
        ),
        "built/base.png": PNG_1PX,
        "built/hairmask.png": PNG_1PX,
        "built/eye0_ball.png": PNG_1PX,
    }
    with zipfile.ZipFile(folder / "avatar.mavatar", "w", zipfile.ZIP_STORED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)


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

    def test_poll_scripts_interpolate_the_cursor_and_keep_the_braces(self):
        # The script body is full of JavaScript braces; interpolating it with
        # str.format raises KeyError inside the 120ms timer slot, and an
        # unhandled exception in a Qt slot aborts the whole process.
        hit = _hit_script(7, -2)
        self.assertIn("window.__companionHit(x,y)", hit)
        self.assertIn("window.__companionCursor(x,y)", hit)
        self.assertTrue(hit.endswith("})(7,-2)"))
        self.assertTrue(_cursor_script(-1, 300).endswith("})(-1,300)"))


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
        self.root = Path(self.temp.name)
        self.service, self.adapter = make_adapter(self.root)
        self.adapter.start()
        self.store = self.service.runtime.store
        self.context = self.browser.new_context(
            viewport={
                "width": overlay_size_for(MAIN_SIZE[1])[0],
                "height": overlay_size_for(MAIN_SIZE[1])[1],
            }
        )
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

    def open_overlay_with_host(self):
        page = self.context.new_page()
        page.add_init_script("window.open = () => null;")
        page.add_init_script(HOST_STUB)
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

    def test_mesh_archive_is_selected_and_failures_disable_the_pet(self):
        write_mesh_archive(self.root)
        self.open_overlay()
        deadline = time.time() + 15
        while time.time() < deadline:
            if not self.adapter.overlay_runtime()["ok"]:
                break
            time.sleep(0.15)
        runtime = self.adapter.overlay_runtime()
        self.assertFalse(runtime["ok"])
        self.assertIn("mesh", runtime["reason"].lower())
        self.wait_for(
            "document.querySelector('#pet').hidden === true",
            "加载失败后不显示替代形象",
        )
        self.assertEqual(
            0, self.page.eval_on_selector("#pet", "el => el.querySelectorAll('img').length")
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

    def test_drag_keeps_the_grab_offset_and_snaps(self):
        page = self.open_overlay_with_host()
        self.wait_for(
            "document.querySelector('#status').textContent.length > 0",
            "浮窗完成首轮加载",
        )
        # The fixture ships no model; the drag handlers are bound regardless.
        page.evaluate("document.querySelector('#pet').hidden = false")
        page.evaluate(
            """() => {
              const fire = (target, type, x, y, sx, sy) => target.dispatchEvent(
                new MouseEvent(type, {
                  bubbles: true, button: 0,
                  clientX: x, clientY: y, screenX: sx, screenY: sy,
                })
              );
              fire(document.querySelector('#pet'), 'mousedown', 150, 100, 1000, 800);
              fire(window, 'mousemove', 230, 130, 1080, 830);
              fire(window, 'mouseup', 230, 130, 1080, 830);
            }"""
        )
        # Window origin at mousedown is 1000-150/800-100; the move keeps that
        # origin and follows the 80/30 delta. The old screenX-clientX grab
        # would have moved the window to 230,30 instead.
        self.assertEqual(
            ["overlay:930,730", "snap"],
            page.evaluate("window.__hostCalls"),
        )

    def test_live2d_fit_is_stable_when_reapplied(self):
        page = self.open_overlay()
        self.wait_for(
            "typeof window.CompanionStage?.fit === 'function'",
            "Live2D 舞台提供 fit 入口",
        )
        sizes = page.evaluate(
            """() => {
              const model = {
                internalModel: { originalWidth: 1000, originalHeight: 2000 },
                scale: {
                  x: 0.01, y: 0.01,
                  set(value) { this.x = value; this.y = value; },
                },
                anchor: {
                  x: 0, y: 0,
                  set(x, y) { this.x = x; this.y = y; },
                },
                x: 0, y: 0,
                get width() { return 1000 * this.scale.x; },
                get height() { return 2000 * this.scale.y; },
              };
              const stage = {
                model,
                app: { screen: { width: 400, height: 300 } },
              };
              CompanionStage.fit(stage);
              const first = [model.scale.x, model.width, model.height];
              CompanionStage.fit(stage);
              const second = [model.scale.x, model.width, model.height];
              return { first, second };
            }"""
        )
        self.assertAlmostEqual(sizes["first"][0], 0.147)
        self.assertAlmostEqual(sizes["first"][0], sizes["second"][0])
        self.assertAlmostEqual(sizes["first"][1], sizes["second"][1])
        self.assertAlmostEqual(sizes["first"][2], sizes["second"][2])

    def test_collapse_hides_the_pet_and_persists(self):
        page = self.open_overlay()
        self.wait_for(
            "document.querySelector('#status').textContent.length > 0",
            "浮窗完成首轮加载",
        )
        # The fixture ships no Live2D model, so the figure is hidden by
        # design; this test only exercises the collapse mechanics.
        page.evaluate("document.querySelector('#pet').hidden = false")
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

    def test_poll_scripts_drive_the_page_cursor_hooks(self):
        page = self.open_overlay()
        self.wait_for(
            "typeof window.__companionCursor === 'function' && "
            "typeof window.__companionHit === 'function'",
            "浮窗注册命中与视线接口",
        )
        page.evaluate(
            "(function(){window.__calls=[];"
            "var record=function(name){return function(x,y){"
            "window.__calls.push([name,x,y]);return true;};};"
            "window.__companionHit=record('hit');"
            "window.__companionCursor=record('cursor');})()"
        )
        # Nothing on the page polls these hooks; only the shell does.
        self.assertEqual(0, page.evaluate("window.__calls.length"))
        # Out-of-window pixels are the whole point: the figure keeps turning
        # toward a cursor that has already left the 300x250 card.
        self.assertTrue(page.evaluate(_hit_script(12, -3)))
        page.evaluate(_cursor_script(260, 480))
        self.assertEqual(
            [["hit", 12, -3], ["cursor", 12, -3], ["cursor", 260, 480]],
            page.evaluate("window.__calls"),
        )


if __name__ == "__main__":
    unittest.main()
