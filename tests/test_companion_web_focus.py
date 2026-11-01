"""Focus presentation (S3): text controls delegate the ring to their container.

The global :focus-visible outline is correct for buttons and links, but inside
the rounded composer/search boxes it paints a second rectangle. The container
must carry the only visible ring, while keyboard focus on ordinary controls
keeps the global one.
"""

import contextlib
import tempfile
import unittest
from pathlib import Path

from agent_framework.companion.app import create_application_service
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
class FocusBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix="companion-focus-")
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
        self.context = self.browser.new_context(viewport={"width": 1180, "height": 860})
        self.page = self.context.new_page()
        self.page.goto(
            "http://{}/?t={}".format(self.adapter.address, self.adapter.token),
            wait_until="load",
        )
        self.page.wait_for_selector("#input")

    def tearDown(self):
        self.context.close()
        self.adapter.stop()
        self.temp.cleanup()

    def style(self, selector, prop):
        return self.page.evaluate(
            "([sel, prop]) => getComputedStyle(document.querySelector(sel)).getPropertyValue(prop)",
            [selector, prop],
        )

    def test_composer_has_one_ring_drawn_by_its_container(self):
        self.page.click("#input")
        self.assertEqual("input", self.page.evaluate("document.activeElement.id"))
        self.assertEqual("none", self.style("#input", "outline-style"))
        self.assertNotEqual("none", self.style("#composer", "box-shadow"))
        self.assertTrue(self.page.evaluate("document.querySelector('#composer').matches(':focus-within')"))

    def test_search_has_one_ring_drawn_by_its_container(self):
        self.page.click("#sessionSearch")
        self.assertEqual("sessionSearch", self.page.evaluate("document.activeElement.id"))
        self.assertEqual("none", self.style("#sessionSearch", "outline-style"))
        self.assertNotEqual("none", self.style(".rail-search", "box-shadow"))
        self.assertTrue(
            self.page.evaluate("document.querySelector('.rail-search').matches(':focus-within')")
        )

    def test_keyboard_focus_on_a_button_still_shows_the_global_ring(self):
        self.page.click("#input")
        self.page.keyboard.press("Tab")
        self.assertEqual("send", self.page.evaluate("document.activeElement.id"))
        self.assertEqual("solid", self.style("#send", "outline-style"))
        self.assertEqual("2px", self.style("#send", "outline-width"))


if __name__ == "__main__":
    unittest.main()
