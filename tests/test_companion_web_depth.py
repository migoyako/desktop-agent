"""Depth and density contracts for the web front end (M12.5).

Static half: the token layer must own three shadow tiers plus a glow, glass and
blur strength per theme, a transparent scrim seam that M13.2 can assign, one
content measure, asymmetric bubbles and an icon-only rail state. The browser
half proves the collapsed rail keeps its sessions reachable, the bubble tails
stay inside their rows, and the glass strength actually follows the theme.
"""

import contextlib
import json
import re
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from agent_framework.companion.web.layout_audit import (
    CONTROLS,
    PROBE,
    _adapter,
    _sample_png,
    _scratch_temp,
    _seed,
    _settle,
    _url,
)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

ASSETS = (
    Path(__file__).resolve().parent.parent / "agent_framework" / "companion" / "web" / "assets"
)
THEMES = {
    "charcoal": ':root,\n:root[data-theme="charcoal-pink"]',
    "pearl": ':root[data-theme="pearl-white"]',
}
DEPTH_TOKENS = (
    "--shadow-1",
    "--shadow-2",
    "--shadow-3",
    "--glow",
    "--surface-glass",
    "--blur",
    "--rail-a",
    "--rail-b",
    "--ambience",
    "--pet-shadow",
    "--scrim",
)


def asset(name):
    return (ASSETS / name).read_text(encoding="utf-8")


def rule_block(sheet, selector):
    pattern = re.compile(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}")
    match = pattern.search(sheet)
    if match is None:
        raise AssertionError("rule not found: " + selector)
    return match.group(1)


def declaration(block, name):
    match = re.search(re.escape(name) + r":\s*([^;]+);", block)
    if match is None:
        raise AssertionError("token not found: " + name)
    return match.group(1).strip()


class DepthTokenTests(unittest.TestCase):
    def theme(self, name):
        return rule_block(asset("tokens.css"), THEMES[name])

    def test_each_theme_owns_three_depth_tiers_and_a_glow(self):
        for name in THEMES:
            block = self.theme(name)
            for token in DEPTH_TOKENS:
                with self.subTest(theme=name, token=token):
                    self.assertIn(token + ":", block)
            self.assertRegex(block, r"--blur:\s*\d+px", name)
            # Glass has to keep alpha: an opaque surface has nothing to blur.
            self.assertRegex(block, r"--surface-glass:\s*rgba\([^)]*0?\.\d+\)", name)

    def test_the_two_themes_do_not_share_their_depth_ink(self):
        charcoal = self.theme("charcoal")
        pearl = self.theme("pearl")
        for token in ("--shadow-1", "--shadow-2", "--shadow-3", "--glow", "--blur",
                      "--surface-glass", "--rail-a", "--rail-b", "--ambience",
                      "--pet-shadow", "--scrim"):
            with self.subTest(token=token):
                self.assertNotEqual(declaration(charcoal, token), declaration(pearl, token))

    def test_pearl_white_does_not_reuse_the_dark_shadow(self):
        block = self.theme("pearl")
        for token in ("--shadow-1", "--shadow-2", "--shadow-3"):
            with self.subTest(token=token):
                value = declaration(block, token)
                self.assertNotIn("rgba(0, 0, 0", value)
                self.assertNotIn("0.28", value)
                self.assertIn("rgba(28, 28, 34", value)

    def test_depth_only_has_two_shadow_levels_left_in_the_alias(self):
        tokens = asset("tokens.css")
        self.assertIn("--shadow: var(--shadow-2);", tokens)
        self.assertEqual(1, tokens.count("--shadow:"))
        for token in ("--shadow-1", "--shadow-2", "--shadow-3", "--glow"):
            references = asset("app.css").count("var({})".format(token))
            references += asset("overlay.css").count("var({})".format(token))
            with self.subTest(token=token):
                self.assertGreaterEqual(references, 1)

    def test_the_scrim_seam_is_transparent_until_a_background_is_chosen(self):
        tokens = asset("tokens.css")
        self.assertEqual(2, tokens.count("--bg-scrim:"))
        self.assertIn("--bg-scrim: rgba(0, 0, 0, 0);", tokens)
        # M13.2 only swaps the value under data-bg; the layers that show it exist
        # already, so the wash cannot be wired twice.
        self.assertIn(
            "--bg-scrim: var(--bg-scrim-image);",
            rule_block(tokens, 'html[data-bg="on"]'),
        )
        shell = rule_block(asset("app.css"), ".shell")
        self.assertIn(
            "linear-gradient(var(--bg-scrim), var(--bg-scrim))", shell
        )
        self.assertIn(
            "linear-gradient(var(--bg-scrim), var(--bg-scrim))",
            rule_block(tokens, ".win"),
        )


class GlassSurfaceTests(unittest.TestCase):
    def test_floating_surfaces_share_one_glass_recipe(self):
        app = asset("app.css")
        for selector in (".topbar", ".composer"):
            with self.subTest(selector=selector):
                block = rule_block(app, selector)
                self.assertIn("var(--surface-glass)", block)
                self.assertIn("backdrop-filter: blur(var(--blur))", block)
        bubble = rule_block(asset("overlay.css"), ".bubble")
        self.assertIn("var(--surface-glass)", bubble)
        self.assertIn("backdrop-filter: blur(var(--blur))", bubble)

    def test_the_rail_and_empty_state_use_their_ambience_layers(self):
        app = asset("app.css")
        self.assertIn(
            "linear-gradient(170deg, var(--rail-a), var(--rail-b))",
            rule_block(app, ".rail"),
        )
        empty = rule_block(app, ".empty::before")
        self.assertIn("var(--ambience)", empty)
        self.assertIn("pointer-events: none", empty)
        self.assertIn("z-index: -1", empty)

    def test_without_blur_support_the_glass_falls_back_to_a_surface(self):
        tokens = asset("tokens.css")
        start = tokens.index("@supports not (backdrop-filter: blur(1px))")
        fallback = tokens[start : start + 400]
        for selector in (":root,", ':root[data-theme="charcoal-pink"]',
                         ':root[data-theme="pearl-white"]'):
            with self.subTest(selector=selector):
                self.assertIn(selector, fallback)
        self.assertIn("--surface-glass: var(--surface);", fallback)


class BubbleShapeTests(unittest.TestCase):
    def test_one_square_corner_mirrors_by_role(self):
        css = asset("app.css")
        agent = re.search(r"border-radius:\s*([^;]+);", rule_block(css, ".bubble")).group(1)
        user = re.search(
            r"border-radius:\s*([^;]+);", rule_block(css, ".msg.me .bubble")
        ).group(1)
        self.assertEqual(
            ["var(--radius-lg)", "var(--radius-lg)", "var(--radius-lg)", "var(--radius-sm)"],
            agent.split(),
        )
        self.assertEqual(
            ["var(--radius-lg)", "var(--radius-lg)", "var(--radius-sm)", "var(--radius-lg)"],
            user.split(),
        )

    def test_the_tail_is_a_mirrored_sibling_inside_the_row_padding(self):
        css = asset("app.css")
        self.assertIn("padding-bottom: var(--bubble-tail)", rule_block(css, ".msg"))
        agent = rule_block(css, ".msg .tail")
        self.assertIn("left: var(--sp-md)", agent)
        self.assertIn("border-right: 1px solid var(--bubble-line)", agent)
        self.assertIn("border-bottom: 1px solid var(--bubble-line)", agent)
        self.assertIn("transform: rotate(45deg)", agent)
        user = rule_block(css, ".msg.me .tail")
        self.assertIn("right: var(--sp-md)", user)
        self.assertIn("border-left: 1px solid var(--bubble-line)", user)
        self.assertIn("transform: rotate(-45deg)", user)
        self.assertNotIn("border-right: 1px", user)
        self.assertIn(
            "--bubble-tail: calc(6px * var(--sp-scale));", asset("tokens.css")
        )

    def test_copy_waits_for_hover_or_focus_without_dropping_keyboard_reach(self):
        css = asset("app.css")
        copy = rule_block(css, ".bubble .meta .copy")
        self.assertIn("opacity: 0", copy)
        self.assertIn("transition: opacity 0.15s ease", copy)
        self.assertIn(
            "opacity: 1",
            rule_block(
                css,
                ".msg:hover .bubble .meta .copy,\n.msg:focus-within .bubble .meta .copy",
            ),
        )
        touch = css[css.index("@media (hover: none)") :]
        self.assertIn(".bubble .meta .copy {\n    opacity: 1;", touch)
        script = asset("app.js")
        self.assertIn('<button class="copy" data-copy=', script)
        # The action only exists in the DOM as a button, so Tab reaches it while
        # it is still transparent.
        self.assertIn('data-msg-act="remember"', script)


class SingleSourceTests(unittest.TestCase):
    def test_mode_and_workspace_have_exactly_one_control(self):
        script = asset("app.js")
        self.assertNotIn('data-pill="mode"', script)
        self.assertNotIn('data-pill="workspace"', script)
        self.assertNotIn("function shortPath(", script)
        index = asset("index.html")
        self.assertEqual(1, index.count('id="modeSwitch"'))
        self.assertEqual(2, index.count("data-mode="))
        pills = script[script.index("function renderPills(") : script.index("function contextView(")]
        self.assertIn('data-pill="context"', pills)
        self.assertIn('data-pill="approval"', pills)
        self.assertIn("el.pills.hidden = !pills.some(Boolean)", pills)

    def test_one_content_measure_serves_every_card(self):
        css = asset("app.css")
        tokens = asset("tokens.css")
        self.assertEqual(1, tokens.count("--content-measure:"))
        for selector in (".bubble", ".tool-card", ".approval"):
            with self.subTest(selector=selector):
                self.assertIn("var(--content-measure)", rule_block(css, selector))
        self.assertNotIn("600px", css)


class RailCollapseTests(unittest.TestCase):
    def test_the_rail_has_one_icon_state_between_two_token_widths(self):
        app = asset("app.css")
        tokens = asset("tokens.css")
        rail = rule_block(app, ".rail")
        self.assertIn("width: var(--rail-w)", rail)
        self.assertIn("transition: width 0.26s ease", rail)
        self.assertIn("width: var(--rail-w-collapsed)", rule_block(app, ".rail.collapsed"))
        for name in ("--rail-w", "--rail-w-collapsed"):
            self.assertIn("var(--sp-scale)", declaration(tokens, name))

    def test_collapsed_labels_step_aside_for_a_glyph(self):
        css = asset("app.css")
        collapsed = css[css.index(".rail.collapsed .rail-label,") :]
        self.assertIn("display: none", collapsed[:400])
        self.assertIn("display: none", rule_block(css, ".session .glyph"))
        self.assertIn("display: flex", rule_block(css, ".rail.collapsed .session .glyph"))
        self.assertIn(
            "background: transparent", rule_block(css, ".rail.collapsed .session,\n.rail.collapsed .session.on")
        )

    def test_the_toggle_is_a_labelled_button(self):
        index = asset("index.html")
        self.assertIn('id="railToggle" title="收起会话栏" aria-expanded="true"', index)
        script = asset("app.js")
        self.assertIn("state.railCollapsed = !state.railCollapsed", script)
        self.assertIn('classList.toggle("collapsed", collapsed)', script)
        self.assertIn('setAttribute("aria-expanded"', script)
        self.assertIn("el.railToggle.hidden = railAuto", script)
        # Both keyboard search and the workspace glyph bring the labels back.
        self.assertIn('if (el.rail.classList.contains("collapsed"))', script)
        self.assertIn("state.railCollapsed = false", script)

    def test_reduce_motion_turns_the_width_animation_off(self):
        tokens = asset("tokens.css")
        start = tokens.index("@media (prefers-reduced-motion: reduce)")
        block = tokens[start : start + 200]
        self.assertIn("transition: none !important", block)
        self.assertIn("animation: none !important", block)


def _ui(adapter, **body):
    request = urllib.request.Request(
        _url(adapter, "/api/ui"),
        data=json.dumps(body).encode("utf-8"),
        headers={"X-Companion-Token": adapter.token, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status


@unittest.skipIf(sync_playwright is None, "未安装 playwright（见 requirements-optional.txt）")
class DepthBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix="companion-depth-")
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
        self.temp = tempfile.TemporaryDirectory(prefix="companion-depth-run-")
        self.adapter = _adapter(Path(self.temp.name))
        _seed(self.adapter)
        self.context = self.browser.new_context(viewport={"width": 1000, "height": 700})

    def tearDown(self):
        self.context.close()
        self.adapter.stop()
        self.temp.cleanup()

    def open_main(self, width=1000, height=700):
        page = self.context.new_page()
        page.set_viewport_size({"width": width, "height": height})
        page.goto(_url(self.adapter, "/"), wait_until="load")
        page.wait_for_selector(".msg", timeout=15000)
        _settle(page, "1")
        page.wait_for_timeout(150)
        return page

    def install_background(self):
        folder = Path(self.temp.name) / "assets" / "remielle"
        folder.mkdir(parents=True)
        (folder / "background.png").write_bytes(_sample_png())
        self.assertEqual(200, _ui(self.adapter, background_enabled=True))

    def background_state(self, page, selector):
        return page.evaluate(
            """(selector) => ({
              on: document.documentElement.dataset.bg || "",
              scrim: getComputedStyle(document.documentElement)
                .getPropertyValue('--bg-scrim').trim(),
              image: getComputedStyle(document.querySelector(selector)).backgroundImage
            })""",
            selector,
        )

    def test_the_background_paints_the_three_windows_with_the_theme_wash(self):
        self.install_background()
        page = self.open_main()
        state = self.background_state(page, ".shell")
        self.assertEqual("on", state["on"])
        self.assertEqual("rgba(8, 9, 12, 0.72)", state["scrim"])
        self.assertIn("/api/asset/background", state["image"])

        self.assertEqual(200, _ui(self.adapter, theme="pearl-white"))
        page.reload(wait_until="load")
        page.wait_for_selector(".msg", timeout=15000)
        _settle(page, "1")
        page.wait_for_timeout(150)
        pearl = self.background_state(page, ".shell")
        self.assertEqual("rgba(247, 246, 244, 0.84)", pearl["scrim"])
        self.assertIn("/api/asset/background", pearl["image"])

        for path, ready in (("/settings", ".nav button"), ("/console", ".tabs button")):
            with self.subTest(page=path):
                page.goto(_url(self.adapter, path), wait_until="load")
                page.wait_for_selector(ready, timeout=15000)
                page.wait_for_timeout(250)
                state = self.background_state(page, ".win")
                self.assertEqual("on", state["on"])
                self.assertIn("/api/asset/background", state["image"])

    def test_clearing_restores_the_solid_page_and_the_overlay_stays_clear(self):
        self.install_background()
        overlay = self.context.new_page()
        overlay.set_viewport_size({"width": 300, "height": 250})
        overlay.goto(_url(self.adapter, "/overlay"), wait_until="load")
        overlay.wait_for_selector(".pet", timeout=15000)
        overlay.wait_for_timeout(300)
        state = overlay.evaluate(
            """() => ({
              on: document.documentElement.dataset.bg || "",
              scrim: getComputedStyle(document.documentElement)
                .getPropertyValue('--bg-scrim').trim(),
              body: getComputedStyle(document.body).backgroundImage
            })"""
        )
        self.assertEqual("", state["on"])
        self.assertEqual("rgba(0, 0, 0, 0)", state["scrim"])
        self.assertEqual("none", state["body"])

        self.assertEqual(200, _ui(self.adapter, background_enabled=False))
        page = self.open_main()
        cleared = self.background_state(page, ".shell")
        self.assertEqual("", cleared["on"])
        self.assertEqual("rgba(0, 0, 0, 0)", cleared["scrim"])
        self.assertNotIn("url(", cleared["image"])

    def test_the_rail_collapses_to_glyphs_and_expands_back(self):
        page = self.open_main()
        before = page.evaluate("document.querySelector('#rail').getBoundingClientRect().width")
        page.click("#railToggle")
        page.wait_for_timeout(500)
        state = page.evaluate(
            """() => {
              const rail = document.querySelector('#rail');
              const toggle = document.querySelector('#railToggle');
              const session = document.querySelector('#sessionList .session');
              const glyph = session && session.querySelector('.glyph');
              return {
                collapsed: rail.classList.contains('collapsed'),
                width: rail.getBoundingClientRect().width,
                label: getComputedStyle(rail.querySelector('.rail-label')).display,
                glyph: glyph ? getComputedStyle(glyph).display : 'missing',
                pressed: toggle.getAttribute('aria-expanded'),
                icon: toggle.querySelector('.ico').textContent,
                session: session ? {
                  w: session.getBoundingClientRect().width,
                  h: session.getBoundingClientRect().height,
                  title: session.getAttribute('title')
                } : null
              };
            }"""
        )
        self.assertTrue(state["collapsed"])
        self.assertLess(state["width"], before - 100)
        self.assertLessEqual(state["width"], 80.0)
        self.assertEqual("none", state["label"])
        self.assertEqual("flex", state["glyph"])
        self.assertEqual("false", state["pressed"])
        self.assertEqual("»", state["icon"])
        self.assertIsNotNone(state["session"])
        self.assertGreater(state["session"]["w"], 0)
        self.assertGreaterEqual(state["session"]["h"], 16)
        self.assertTrue(state["session"]["title"])
        problems = page.evaluate(PROBE, CONTROLS["main"])
        self.assertEqual(
            [],
            ["{} {} +{}".format(item["axis"], item["node"], item["delta"])
             for item in problems["problems"]],
        )
        page.click("#railToggle")
        page.wait_for_timeout(500)
        restored = page.evaluate("document.querySelector('#rail').getBoundingClientRect().width")
        self.assertAlmostEqual(before, restored, delta=1.0)

    def test_the_tails_sit_inside_their_rows_and_mirror_the_role(self):
        page = self.open_main()
        rows = page.evaluate(
            """() => [...document.querySelectorAll('.msg')].map((msg) => {
              const bubble = msg.querySelector('.bubble');
              const tail = msg.querySelector('.tail');
              if (!bubble || !tail) return { missing: true };
              const m = msg.getBoundingClientRect();
              const b = bubble.getBoundingClientRect();
              const t = tail.getBoundingClientRect();
              return {
                me: msg.classList.contains('me'),
                inside: t.top >= m.top - 0.6 && t.bottom <= m.bottom + 0.6 &&
                  t.left >= m.left - 0.6 && t.right <= m.right + 0.6,
                attached: t.top < b.bottom,
                leftSide: t.left + t.width / 2 < b.left + b.width / 2,
                overflow: msg.scrollHeight - msg.clientHeight,
                width: t.width
              };
            })"""
        )
        self.assertTrue(rows)
        for index, row in enumerate(rows):
            with self.subTest(row=index):
                self.assertNotIn("missing", row)
                self.assertTrue(row["inside"])
                self.assertTrue(row["attached"])
                self.assertEqual(row["me"], not row["leftSide"])
                self.assertLessEqual(row["overflow"], 1)
                self.assertGreater(row["width"], 0)

    def test_glass_strength_and_the_scrim_follow_the_theme(self):
        page = self.open_main()

        def recipes():
            return page.evaluate(
                """() => ({
                  bar: getComputedStyle(document.querySelector('.topbar')).backdropFilter,
                  barFill: getComputedStyle(document.querySelector('.topbar')).backgroundColor,
                  composerFill: getComputedStyle(document.querySelector('.composer')).backgroundColor,
                  scrim: getComputedStyle(document.documentElement)
                    .getPropertyValue('--bg-scrim').trim()
                })"""
            )

        charcoal = recipes()
        self.assertIn("blur(14px)", charcoal["bar"])
        self.assertTrue(charcoal["barFill"].startswith("rgba(37, 37, 37,"))
        self.assertTrue(charcoal["composerFill"].startswith("rgba(37, 37, 37,"))
        self.assertEqual("rgba(0, 0, 0, 0)", charcoal["scrim"])

        self.assertEqual(200, _ui(self.adapter, theme="pearl-white"))
        page.reload(wait_until="load")
        page.wait_for_selector(".msg", timeout=15000)
        _settle(page, "1")
        page.wait_for_timeout(150)
        pearl = recipes()
        self.assertIn("blur(18px)", pearl["bar"])
        self.assertTrue(pearl["barFill"].startswith("rgba(255, 255, 255,"))
        self.assertTrue(pearl["composerFill"].startswith("rgba(255, 255, 255,"))
        self.assertEqual("rgba(0, 0, 0, 0)", pearl["scrim"])


if __name__ == "__main__":
    unittest.main()
