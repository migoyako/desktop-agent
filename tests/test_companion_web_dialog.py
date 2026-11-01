"""In-page dialog contracts (S2).

QtWebEngine does not implement window.confirm/window.prompt, so the shell treats
their return value as a falsy cancellation. Every destructive action must use
the shared page dialog instead, and the dialog itself must survive Escape,
backdrop clicks and IME composition.
"""

import contextlib
import re
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


def asset(name):
    return (ASSETS / name).read_text(encoding="utf-8")


def rule_block(sheet, selector):
    pattern = re.compile(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}")
    match = pattern.search(sheet)
    if match is None:
        raise AssertionError("rule not found: " + selector)
    return match.group(1)


class DialogContractTests(unittest.TestCase):
    def test_no_page_uses_the_native_dialogs(self):
        for path in sorted(ASSETS.glob("*.js")):
            source = path.read_text(encoding="utf-8")
            with self.subTest(script=path.name):
                self.assertNotIn("window.confirm", source)
                self.assertNotIn("window.prompt", source)

    def test_every_page_loads_the_dialog_before_its_script(self):
        for page, script in (
            ("index.html", "app.js"),
            ("settings.html", "settings.js"),
            ("console.html", "console.js"),
        ):
            html = asset(page)
            with self.subTest(page=page):
                self.assertIn('<script src="/assets/dialog.js"></script>', html)
                self.assertLess(
                    html.index("/assets/dialog.js"),
                    html.index("/assets/{}".format(script)),
                )

    def test_dialog_is_modal_and_cancellable(self):
        script = asset("dialog.js")
        for fragment in (
            'window.CompanionDialog',
            'setAttribute("role", "dialog")',
            'setAttribute("aria-modal", "true")',
            'event.key === "Escape"',
            'event.key === "Tab"',
            'event.isComposing',
            'event.keyCode === 229',
            'event.target === scrim',
            "trigger.focus()",
            "input.select()",
        ):
            self.assertIn(fragment, script, fragment)

    def test_dialog_sizes_and_inks_from_tokens(self):
        sheet = asset("tokens.css")
        card = rule_block(sheet, ".dialog-scrim .dialog")
        for token in ("var(--surface)", "var(--radius-lg)", "var(--sp-lg)", "var(--shadow-3)"):
            self.assertIn(token, card, token)
        self.assertIsNone(re.search(r"(?m)^\s*(height|font-size):\s*\d+px", card))
        actions = rule_block(sheet, ".dialog-actions")
        self.assertIn("var(--sp-md)", actions)
        self.assertIn("var(--danger)", rule_block(sheet, ".dialog-actions .primary.danger"))


@unittest.skipIf(sync_playwright is None, "未安装 playwright（见 requirements-optional.txt）")
class DialogBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix="companion-dialog-")
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

    def open_page(self):
        page = self.context.new_page()
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
            "http://{}/?t={}".format(self.adapter.address, self.adapter.token),
            wait_until="load",
        )
        return page

    def wait_for(self, expression, description, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.adapter.broadcast({"type": "refresh"})
            time.sleep(0.25)
            try:
                if self.page.evaluate("Boolean({})".format(expression)):
                    return
            except Exception:
                pass
        self.fail("condition not met: " + description)

    def open_menu_item(self, label):
        page = self.page
        page.click("#sessionList .session", button="right")
        page.wait_for_selector("#sessionMenu", state="visible")
        page.get_by_role("menuitem", name=label).click()
        page.wait_for_selector(".dialog", state="visible")

    def session_ids(self):
        return [row["id"] for row in self.store.list_sessions()]

    def wait_for_title(self, session_id, title, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            rows = [row for row in self.store.list_sessions() if row["id"] == session_id]
            if rows and rows[0]["title"] == title:
                return
            time.sleep(0.15)
        self.fail("title not updated: " + title)

    # -- cases ----------------------------------------------------------

    def test_delete_confirms_in_page_then_removes_the_session(self):
        session_id = self.seed_session()
        self.open_page()
        self.wait_for("document.querySelector('#sessionList .session')", "会话栏渲染")
        self.open_menu_item("删除")
        self.assertEqual("dialog", self.page.get_attribute(".dialog", "role"))
        self.assertEqual("true", self.page.get_attribute(".dialog", "aria-modal"))
        self.assertEqual(
            "取消",
            self.page.evaluate("document.activeElement.textContent.trim()"),
        )
        self.page.click(".dialog-cancel")
        self.page.wait_for_selector(".dialog-scrim", state="detached")
        self.assertIn(session_id, self.session_ids())

        self.open_menu_item("删除")
        self.page.click(".dialog .dialog-ok")
        self.page.wait_for_selector(".dialog-scrim", state="detached")
        deadline = time.time() + 10
        while time.time() < deadline and session_id in self.session_ids():
            time.sleep(0.15)
        self.assertNotIn(session_id, self.session_ids())

    def test_escape_and_backdrop_cancel_without_side_effects(self):
        session_id = self.seed_session()
        self.open_page()
        self.wait_for("document.querySelector('#sessionList .session')", "会话栏渲染")
        self.open_menu_item("删除")
        self.page.keyboard.press("Escape")
        self.page.wait_for_selector(".dialog-scrim", state="detached")
        self.assertIn(session_id, self.session_ids())

        self.open_menu_item("删除")
        self.page.mouse.click(6, 6)
        self.page.wait_for_selector(".dialog-scrim", state="detached")
        self.assertIn(session_id, self.session_ids())

    def test_rename_accepts_enter_but_not_ime_composition(self):
        session_id = self.seed_session()
        self.open_page()
        self.wait_for("document.querySelector('#sessionList .session')", "会话栏渲染")
        self.open_menu_item("重命名")
        self.page.fill(".dialog-input", "新的会话名")
        self.page.evaluate(
            "document.querySelector('.dialog-input').dispatchEvent("
            "new KeyboardEvent('keydown', {key: 'Enter', isComposing: true, bubbles: true}))"
        )
        self.page.wait_for_timeout(200)
        self.assertEqual(1, self.page.locator(".dialog").count())
        self.assertNotEqual("新的会话名", self.store.list_sessions()[0]["title"])

        self.page.keyboard.press("Enter")
        self.page.wait_for_selector(".dialog-scrim", state="detached")
        self.wait_for_title(session_id, "新的会话名")

    def test_prompt_focuses_the_prefilled_input(self):
        session_id = self.seed_session()
        summary = [row for row in self.service.session_summaries() if row["id"] == session_id][0]
        expected = str(summary.get("title") or summary.get("topic") or "").strip()
        self.open_page()
        self.wait_for("document.querySelector('#sessionList .session')", "会话栏渲染")
        self.open_menu_item("重命名")
        self.assertEqual(
            "field dialog-input",
            self.page.evaluate("document.activeElement.className"),
        )
        self.assertEqual(
            expected,
            self.page.evaluate("document.querySelector('.dialog-input').value"),
        )
        self.page.fill(".dialog-input", "新的名字")
        self.page.click(".dialog-ok")
        self.page.wait_for_selector(".dialog-scrim", state="detached")
        self.wait_for_title(session_id, "新的名字")


if __name__ == "__main__":
    unittest.main()
