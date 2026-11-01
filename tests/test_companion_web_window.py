"""Window-level web contracts: native drag delegation and the session menu.

The Qt shell owns the actual window move so the cursor may leave the WebView
mid-drag; the page must not re-introduce a page-level mousemove drag. The
session menu must survive the mousedown that precedes its item's click.
"""

import contextlib
import tempfile
import time
import unittest
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

ASSETS = (
    Path(__file__).resolve().parent.parent / "agent_framework" / "companion" / "web" / "assets"
)
SHELL = ASSETS.parent / "shell.py"

HOST_STUB = """
window.__hostCalls = [];
window.__host = {
  ready: function () {},
  beginWindowDrag: function () { window.__hostCalls.push("drag"); },
  beginWindowResize: function (edge) { window.__hostCalls.push("resize:" + edge); },
  moveWindow: function (x, y) { window.__hostCalls.push("move:" + x + "," + y); },
};
window.qt = { webChannelTransport: {} };
"""

QWEBCHANNEL_STUB = """
window.QWebChannel = function (transport, callback) {
  callback({ objects: { host: window.__host } });
};
"""

LEGACY_HOST_STUB = """
window.__hostCalls = [];
window.__host = {
  ready: function () {},
  moveWindow: function (x, y) { window.__hostCalls.push("move:" + x + "," + y); },
};
window.qt = { webChannelTransport: {} };
"""


class StubSearch:
    def search(self, query, limit=5):
        return []


def asset(name):
    return (ASSETS / name).read_text(encoding="utf-8")


class WindowContractTests(unittest.TestCase):
    def section(self, name, start, end):
        source = asset(name)
        begin = source.index(start)
        return source[begin:source.index(end, begin)]

    def test_main_window_hands_the_move_to_the_shell(self):
        source = asset("app.js")
        self.assertNotIn("screenX - event.clientX", source)
        drag = self.section("app.js", "function bindWindowDrag()", "function connectHost()")
        self.assertIn('typeof state.host.beginWindowDrag === "function"', drag)
        self.assertIn("state.host.beginWindowDrag()", drag)
        self.assertIn('closest("button, input, textarea")', drag)
        # The legacy fallback must keep the pointer offset as well.
        self.assertIn("x: event.clientX", drag)
        self.assertIn("moveEvent.screenX - grab.x", drag)

    def test_overlay_uses_the_pointer_offset_and_still_snaps(self):
        source = asset("overlay.js")
        self.assertNotIn("screenX - event.clientX", source)
        block = self.section("overlay.js", "const startDrag", "el.pet.addEventListener")
        self.assertIn("x: event.clientX", block)
        self.assertIn("y: event.clientY", block)
        self.assertIn(
            "state.host.moveOverlay(event.screenX - grab.x, event.screenY - grab.y)",
            source,
        )
        self.assertIn("state.host.snapOverlay()", source)

    def test_session_menu_closes_only_outside_itself(self):
        block = self.section("app.js", "function showContextMenu", "// ---------- events ----------")
        self.assertIn("if (menu.contains(mouseEvent.target)) return;", block)
        self.assertIn('keyEvent.key === "Escape"', block)
        self.assertIn('setAttribute("role", "menu")', block)
        self.assertIn('setAttribute("role", "menuitem")', block)
        self.assertIn('window.addEventListener("scroll", dismiss, true)', block)
        self.assertNotIn("setTimeout(() => {", block)

    def test_shell_exposes_a_native_caption_drag(self):
        source = SHELL.read_text(encoding="utf-8")
        self.assertIn("def _send_caption_drag(hwnd)", source)
        self.assertIn("def begin_drag(self)", source)
        self.assertIn("def beginWindowDrag(self)", source)
        self.assertIn("WM_NCLBUTTONDOWN", source)
        self.assertIn("HTCAPTION", source)
        self.assertIn("ReleaseCapture()", source)
        self.assertIn("startSystemMove()", source)


@unittest.skipIf(sync_playwright is None, "未安装 playwright（见 requirements-optional.txt）")
class WindowBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix="companion-window-")
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
        self.context = self.browser.new_context(viewport={"width": 1180, "height": 860})
        self.page = None

    def tearDown(self):
        self.context.close()
        self.adapter.stop()
        self.temp.cleanup()

    # -- helpers --------------------------------------------------------

    def seed_session(self):
        self.store.add_message("main", "user", "第一条历史消息", AgentMode.CHAT)
        return self.store.list_sessions()[0]["id"]

    def open_page(self, host_stub=HOST_STUB):
        page = self.context.new_page()
        page.add_init_script(host_stub)
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
            "http://{}/?t={}".format(self.adapter.address, self.adapter.token),
            wait_until="load",
        )
        return page

    def wait_for(self, expression, description, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.adapter.broadcast({"type": "refresh"})
            time.sleep(0.3)
            try:
                if self.page.evaluate("Boolean({})".format(expression)):
                    return
            except Exception:
                pass
        self.fail("condition not met: " + description)

    def start_topbar_drag(self, page):
        page.evaluate(
            """() => {
              const fire = (target, type, x, y, sx, sy) => target.dispatchEvent(
                new MouseEvent(type, {
                  bubbles: true, button: 0,
                  clientX: x, clientY: y, screenX: sx, screenY: sy,
                })
              );
              fire(document.querySelector('#topbar'), 'mousedown', 500, 20, 1200, 200);
              fire(window, 'mousemove', 560, 50, 1260, 230);
              fire(window, 'mouseup', 560, 50, 1260, 230);
            }"""
        )

    # -- cases ----------------------------------------------------------

    def test_topbar_drag_is_delegated_and_never_moves_by_mouse_events(self):
        self.seed_session()
        page = self.open_page()
        self.wait_for("document.querySelector('#sessionList .session')", "会话栏渲染")
        self.start_topbar_drag(page)
        self.assertEqual(["drag"], page.evaluate("window.__hostCalls"))
        page.evaluate(
            """() => document.querySelector('#menuBtn').dispatchEvent(
                 new MouseEvent('mousedown', {bubbles: true, button: 0})
               )"""
        )
        self.assertEqual(["drag"], page.evaluate("window.__hostCalls"))

    def test_legacy_shell_falls_back_to_the_corrected_pointer_offset(self):
        self.seed_session()
        page = self.open_page(LEGACY_HOST_STUB)
        self.wait_for("document.querySelector('#sessionList .session')", "会话栏渲染")
        page.evaluate(
            """() => {
              const fire = (target, type, x, y, sx, sy) => target.dispatchEvent(
                new MouseEvent(type, {
                  bubbles: true, button: 0,
                  clientX: x, clientY: y, screenX: sx, screenY: sy,
                })
              );
              fire(document.querySelector('#topbar'), 'mousedown', 500, 20, 1200, 200);
              fire(window, 'mousemove', 560, 50, 1260, 230);
              fire(window, 'mouseup', 560, 50, 1260, 230);
            }"""
        )
        # Window origin 700,180 + delta 60,30. The old window-origin grab
        # would have sent 560,200 instead.
        self.assertEqual(["move:760,210"], page.evaluate("window.__hostCalls"))

    def test_session_menu_survives_mousedown_and_pins(self):
        session_id = self.seed_session()
        page = self.open_page()
        self.wait_for("document.querySelector('#sessionList .session')", "会话栏渲染")
        page.click("#sessionList .session", button="right")
        page.wait_for_selector("#sessionMenu", state="visible")
        button = page.locator("#sessionMenu button").nth(1)
        box = button.bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        self.assertEqual(1, page.locator("#sessionMenu").count())
        page.mouse.up()
        deadline = time.time() + 10
        pinned = ""
        while time.time() < deadline:
            rows = [row for row in self.store.list_sessions() if row["id"] == session_id]
            pinned = rows[0]["pinned_at"] if rows else ""
            if pinned:
                break
            time.sleep(0.15)
        self.assertTrue(pinned)
        page.wait_for_selector("#sessionMenu", state="detached")

    def test_session_menu_closes_on_outside_click_and_escape(self):
        self.seed_session()
        page = self.open_page()
        self.wait_for("document.querySelector('#sessionList .session')", "会话栏渲染")
        page.click("#sessionList .session", button="right")
        page.wait_for_selector("#sessionMenu", state="visible")
        page.mouse.click(5, 400)
        page.wait_for_selector("#sessionMenu", state="detached")
        page.click("#sessionList .session", button="right")
        page.wait_for_selector("#sessionMenu", state="visible")
        page.keyboard.press("Escape")
        page.wait_for_selector("#sessionMenu", state="detached")

    def test_edge_band_reports_the_edge_without_stealing_presses(self):
        session_id = self.seed_session()
        page = self.open_page()
        self.wait_for("document.querySelector('#sessionList .session')", "会话栏渲染")
        page.evaluate(
            """() => {
              const corner = document.elementFromPoint(2, 2);
              corner.dispatchEvent(new MouseEvent("mousedown", {
                bubbles: true, button: 0, clientX: 2, clientY: 2,
              }));
              const control = document.querySelector("#topbar button[data-cmd=minimize]");
              control.dispatchEvent(new MouseEvent("mousedown", {
                bubbles: true, button: 0,
                clientX: window.innerWidth - 2, clientY: 2,
              }));
            }"""
        )
        # Bare chrome in the band reaches the shell; a button in the same band
        # does not, so window controls keep working.
        self.assertEqual(["resize:topleft"], page.evaluate("window.__hostCalls"))
        # The press that dismisses a menu is also a corner press: the resize
        # handler must not stop propagation or the menu would never close.
        page.click("#sessionList .session", button="right")
        page.wait_for_selector("#sessionMenu", state="visible")
        page.mouse.click(5, 400)
        page.wait_for_selector("#sessionMenu", state="detached")
        self.assertEqual(
            ["resize:topleft", "resize:left"], page.evaluate("window.__hostCalls")
        )
        rows = [row for row in self.store.list_sessions() if row["id"] == session_id]
        self.assertEqual(1, len(rows))


if __name__ == "__main__":
    unittest.main()
