"""Static contracts for the web UI token layer (M12.1 and M12.2).

These checks keep font scaling correct without a browser: control boxes must
size themselves from the font tokens, gutters must come from the spacing
tokens, and the narrow-window window-controls must stay reachable. The
typography half keeps one shared ladder on both front ends, three line-height
tiers, and a reading measure counted in characters instead of pixels.
"""

import re
import unittest
from pathlib import Path

ASSETS = (
    Path(__file__).resolve().parent.parent / "agent_framework" / "companion" / "web" / "assets"
)

PAGES = ("tokens.css", "app.css", "settings.css", "console.css")

FIXED_HEIGHT = re.compile(r"^\s*height:\s*\d+px", re.MULTILINE)
PX_SPACING = re.compile(r"^\s*(padding|margin|gap)[a-z-]*:\s*\d+px \d+px", re.MULTILINE)
PX_FONT_SIZE = re.compile(r"^\s*font-size:\s*\d+px", re.MULTILINE)
MEDIA_BLOCK = re.compile(r"@media[^{]*\{(?:[^{}]|\{[^{}]*\})*\}")


def asset(name):
    return (ASSETS / name).read_text(encoding="utf-8")


def rule_block(sheet, selector):
    pattern = re.compile(
        r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}"
    )
    match = pattern.search(sheet)
    if match is None:
        raise AssertionError("rule not found: " + selector)
    return match.group(1)


class TokenLayerTests(unittest.TestCase):
    CONTROLS = ("--ctl-pill", "--ctl-segment", "--ctl-ghost", "--ctl-field", "--ctl-primary", "--ctl-row", "--ctl-bar")

    def test_spacing_and_control_tokens_exist(self):
        tokens = asset("tokens.css")
        for name in ("--sp-xs", "--sp-sm", "--sp-md", "--sp-lg", "--sp-xl"):
            self.assertIn(name + ": calc(", tokens, name)
        for name in self.CONTROLS:
            self.assertIn(name + ": calc(", tokens, name)
        for name in ("--font-mono", "--radius-pill", "--radius-card", "--lh-body", "--fw-semibold"):
            self.assertIn(name + ":", tokens, name)

    def test_control_metrics_are_measured_from_the_font(self):
        tokens = asset("tokens.css")
        for name in self.CONTROLS:
            declaration = re.search(re.escape(name) + r":\s*([^;]+);", tokens).group(1)
            self.assertIn("--fs-", declaration, name)

    def test_page_css_declares_no_fixed_heights(self):
        for name in PAGES:
            with self.subTest(sheet=name):
                self.assertEqual([], FIXED_HEIGHT.findall(asset(name)))

    def test_page_css_uses_spacing_tokens(self):
        for name in ("app.css", "settings.css", "console.css"):
            sheet = asset(name)
            with self.subTest(sheet=name):
                self.assertEqual([], PX_SPACING.findall(sheet))
                self.assertIn("var(--sp-", sheet)

    def test_no_hardcoded_font_sizes_or_monospace_stacks(self):
        for name in PAGES:
            sheet = asset(name)
            with self.subTest(sheet=name):
                self.assertEqual([], PX_FONT_SIZE.findall(sheet))
        for name in ("app.css", "settings.css", "console.css", "overlay.css"):
            self.assertNotIn("Consolas", asset(name), name)

    def test_shared_controls_size_from_min_height(self):
        tokens = asset("tokens.css")
        pairs = (
            (".ghost", "--ctl-ghost"),
            (".pill", "--ctl-pill"),
            (".field", "--ctl-field"),
            (".primary", "--ctl-primary"),
            (".segmented button", "--ctl-segment"),
            (".table td", "--ctl-row"),
        )
        for selector, token in pairs:
            block = rule_block(tokens, selector)
            self.assertIn(token, block, selector)
            self.assertIsNone(re.search(r"(?m)^\s*height:\s*[^;]*\d+px", block), selector)


class OverflowAndReachabilityTests(unittest.TestCase):
    def test_tab_strips_scroll_instead_of_clipping(self):
        for sheet, selector in (
            (asset("app.css"), ".inspector-tabs"),
            (asset("console.css"), ".tabs"),
        ):
            self.assertIn("overflow-x: auto", rule_block(sheet, selector), selector)
            self.assertIn(
                "flex: none", rule_block(sheet, selector + " button"), selector + " button"
            )

    def test_window_controls_survive_narrow_windows(self):
        app = asset("app.css")
        index = asset("index.html")
        for block in MEDIA_BLOCK.findall(app):
            self.assertNotIn("[data-cmd]", block, block.strip().splitlines()[0])
        for cmd in ("minimize", "hide"):
            self.assertIn('data-cmd="{}"'.format(cmd), index)
        self.assertIn("--ctl-ghost", rule_block(app, ".menu-btn"))
        rule_block(app, ".win-actions .ico")
        rule_block(app, ".win-actions .lbl")

    def test_table_cards_scroll(self):
        self.assertIn("overflow-x: auto", rule_block(asset("console.css"), ".table-card"))

    def test_overlay_bubble_stays_above_the_avatar(self):
        overlay = asset("overlay.css")
        block = rule_block(overlay, ".bubble")
        self.assertIn("var(--pet-size)", block)
        self.assertIn("max-height", block)
        self.assertIn("var(--pet-size)", rule_block(overlay, ".pet"))

    def test_avatar_box_holds_its_own_graphic(self):
        block = rule_block(asset("overlay.css"), ".pet svg")
        self.assertIn("display: block", block)
        self.assertIn("var(--pet-size)", block)

    def test_responsive_breakpoints_follow_the_font_scale(self):
        script = asset("app.js")
        self.assertIn("function responsiveWidth()", script)
        for raw in ("window.innerWidth < 860", "window.innerWidth < 940", "window.innerWidth < 780"):
            self.assertNotIn(raw, script)


class AccessibilityMarkupTests(unittest.TestCase):
    def test_main_page_landmarks(self):
        index = asset("index.html")
        self.assertIn('role="log"', index)
        self.assertIn('role="tablist"', index)
        self.assertIn('role="tab"', index)
        self.assertIn('role="status"', index)
        self.assertIn('aria-label="输入消息"', index)

    def test_companion_pages_are_labelled(self):
        self.assertIn('role="tablist"', asset("console.html"))
        self.assertIn('id="toast" role="status"', asset("console.html"))
        self.assertIn('aria-label="搜索设置"', asset("settings.html"))
        self.assertIn('aria-label="设置分类"', asset("settings.html"))
        self.assertIn('aria-label="收起浮窗"', asset("overlay.html"))

    def test_interactive_rows_are_real_buttons(self):
        script = asset("app.js")
        self.assertIn('<button class="session', script)
        self.assertIn('aria-current="true"', script)
        self.assertIn('aria-selected', script)
        self.assertIn('aria-pressed', script)
        self.assertIn('aria-live="polite"', script)

    def test_console_rows_are_keyboard_reachable(self):
        script = asset("console.js")
        self.assertIn('tabindex="0"', script)
        self.assertIn("onkeydown", script)

    def test_focus_ring_is_visible(self):
        self.assertIn(":focus-visible", asset("tokens.css"))
        for name in PAGES + ("overlay.css",):
            self.assertNotIn("outline: none", asset(name), name)


class ContextGaugeTests(unittest.TestCase):
    """M12.3-a: the ring is fed by projection numbers, never by display text."""

    def pill_source(self):
        script = asset("app.js")
        start = script.index("function renderPills()")
        end = script.index("function shortPath(")
        return script[start:end]

    def test_the_pills_stop_mining_display_text(self):
        self.assertNotIn("contextPercent", asset("app.js"))
        source = self.pill_source()
        self.assertNotIn(r"\d", source)
        for field in ("context_used", "context_limit", "compressed"):
            self.assertIn(field, source, field)

    def test_the_gauge_button_stays_labelled_and_opens_the_context_tab(self):
        source = self.pill_source()
        script = asset("app.js")
        self.assertIn('aria-hidden="true"', source)
        self.assertIn("aria-label", source)
        self.assertIn('querySelector("[data-pill=context]")', script)
        self.assertIn('pill.dataset.pill === "context"', script)

    def test_the_ring_sizes_and_colors_from_tokens(self):
        css = asset("app.css")
        ring = rule_block(css, ".gauge-ring")
        self.assertIn("var(--fs-", ring)
        self.assertIn("var(--percent)", ring)
        self.assertIn("var(--gauge)", ring)
        self.assertIsNone(re.search(r"\d+px", ring))
        self.assertIn("var(--surface)", rule_block(css, ".gauge-ring::after"))
        self.assertIn("var(--warning)", rule_block(css, ".pills-row button.gauge.warning"))
        self.assertIn("var(--danger)", rule_block(css, ".pills-row button.gauge.danger"))


class RunTimelineTests(unittest.TestCase):
    """M12.3-b: the run bar expands projection steps instead of inventing its own trail."""

    def steps_source(self):
        script = asset("app.js")
        start = script.index("function renderRunSteps(")
        end = script.index("function handleEvent(")
        return script[start:end]

    def handle_source(self):
        script = asset("app.js")
        start = script.index("function handleEvent(")
        end = script.index("function summarizeArguments(")
        return script[start:end]

    def test_the_steps_are_escaped_and_labelled_from_the_shared_stage_map(self):
        source = self.steps_source()
        self.assertIn("STAGE_LABELS[step.stage]", source)
        self.assertIn("escapeHtml(step.detail || step.tool", source)
        self.assertIn("clock(step.at)", source)
        for fragment in ("innerHTML", "class=", "aria-expanded"):
            self.assertIn(fragment, source, fragment)

    def test_the_front_end_stops_inventing_terminal_labels(self):
        self.assertNotIn("已完成", self.handle_source())
        self.assertIn("event.stage !== state.runStage", self.handle_source())
        self.assertIn("scheduleRefresh()", self.handle_source())

    def test_the_step_list_stays_out_of_the_status_live_region(self):
        index = asset("index.html")
        status = index.index('id="runbar" role="status"')
        steps = index.index('id="runSteps"')
        self.assertGreater(steps, status)
        self.assertIn('aria-controls="runSteps"', index)
        self.assertIn('aria-expanded="false"', index)
        self.assertIn('aria-label="本轮执行步骤"', index)

    def test_the_toggle_repaints_from_the_rendered_rows(self):
        script = asset("app.js")
        start = script.index("el.runStepsToggle.onclick")
        wiring = script[start : start + 160]
        self.assertIn("state.stepsOpen = !state.stepsOpen", wiring)
        self.assertIn("renderRunSteps(state.steps)", wiring)
        self.assertNotIn("state.data.run", wiring)
        self.assertIn("state.steps = rows", script)

    def test_the_timeline_sizes_from_tokens(self):
        css = asset("app.css")
        steps = rule_block(css, ".run-steps")
        self.assertIn("var(--ctl-bar)", steps)
        self.assertIn("var(--fs-meta)", steps)
        self.assertIn("var(--lh-meta)", steps)
        self.assertIn("overflow-y: auto", steps)
        for declaration in ("max-height", "font-size", "line-height", "gap"):
            self.assertIsNone(
                re.search(declaration + r":\s*[\d.]+(px)?\s*[;}]", steps),
                declaration,
            )
        self.assertIn("var(--muted)", rule_block(css, ".runbar .steps-toggle"))
        self.assertIn("var(--sp-", rule_block(css, ".run-strip"))


SHEETS = PAGES + ("overlay.css",)
LITERAL_LINE_HEIGHT = re.compile(r"^\s*line-height:\s*[\d.]+\s*;", re.MULTILINE)
PIXEL_MAX_WIDTH = re.compile(r"^\s*max-width:\s*calc\(\d+px", re.MULTILINE)


class TypographyTests(unittest.TestCase):
    """M12.2: one ladder on both front ends, and no rung without a user."""

    def ladder(self):
        found = dict(
            (name, int(px))
            for name, px in re.findall(
                r"--fs-([a-z]+): calc\((\d+)px \* var\(--scale\)\)", asset("tokens.css")
            )
        )
        self.assertTrue(found, "no type tokens parsed")
        return found

    def test_the_web_ladder_is_the_qt_ladder(self):
        from agent_framework.companion.ui import theme

        self.assertEqual(self.ladder(), theme.TYPE_TOKENS)

    def test_the_ladder_climbs_and_the_body_is_readable(self):
        ladder = self.ladder()
        ordered = sorted(ladder.values())
        self.assertEqual(sorted(ladder.values()), ordered)
        self.assertGreaterEqual(ladder["body"], 14)
        self.assertGreaterEqual(ladder["caption"], 11)
        self.assertGreaterEqual(ladder["display"], ladder["body"] * 1.5)

    def test_every_rung_is_actually_used(self):
        for rung in self.ladder():
            references = sum(asset(name).count("var(--fs-{})".format(rung)) for name in SHEETS)
            with self.subTest(rung=rung):
                self.assertGreaterEqual(references, 1)

    def test_line_height_is_three_semantic_tiers(self):
        declared = re.findall(r"^\s*(--lh-[a-z]+):", asset("tokens.css"), re.MULTILINE)
        self.assertEqual({"--lh-control", "--lh-meta", "--lh-body"}, set(declared))
        for name in SHEETS:
            with self.subTest(sheet=name):
                self.assertEqual([], LITERAL_LINE_HEIGHT.findall(asset(name)))

    def test_the_reading_measure_follows_the_body_size(self):
        tokens = asset("tokens.css")
        self.assertRegex(
            tokens, r"--read-measure: calc\(var\(--fs-body\) \* (\d{2})\)",
            "the measure has to be counted in characters"
        )
        chars = int(re.search(r"--read-measure: calc\(var\(--fs-body\) \* (\d{2})\)", tokens).group(1))
        self.assertGreaterEqual(chars, 35)
        self.assertLessEqual(chars, 45)
        for selector in (".chat-inner", ".composer-wrap"):
            with self.subTest(selector=selector):
                self.assertIn("--col-measure", rule_block(asset("app.css"), selector))
        for name in SHEETS:
            with self.subTest(sheet=name):
                self.assertEqual([], PIXEL_MAX_WIDTH.findall(asset(name)))

    def test_geometric_widths_follow_spacing_not_type(self):
        # The rail and the scrollbar are furniture: at 200% they may not double
        # and leave nothing to read with.
        self.assertIn("var(--sp-scale)", rule_block(asset("app.css"), ".rail"))
        self.assertIn("var(--sp-scale)", rule_block(asset("tokens.css"), "::-webkit-scrollbar"))

    def test_control_boxes_never_measure_from_the_display_rung(self):
        tokens = asset("tokens.css")
        for name in TokenLayerTests.CONTROLS:
            declaration = re.search(re.escape(name) + r":\s*([^;]+);", tokens).group(1)
            with self.subTest(metric=name):
                self.assertNotIn("--fs-display", declaration)
                self.assertNotIn("--fs-subtitle", declaration)


class MarkdownSubsetTests(unittest.TestCase):
    """M12.3 C1: one grammar in markdown.js, styled with tokens on the Web."""

    def test_the_shared_renderer_loads_before_the_app(self):
        index = asset("index.html")
        script = index.index('src="/assets/markdown.js"')
        self.assertLess(script, index.index('src="/assets/app.js"'))
        self.assertIn("CompanionMarkdown", asset("markdown.js"))

    def test_the_app_delegates_instead_of_keeping_a_second_grammar(self):
        script = asset("app.js")
        self.assertIn("CompanionMarkdown.render", script)
        self.assertIn("CompanionMarkdown.escapeHtml", script)
        for duplicate in ("function markdown(", "function inline(", "function escapeHtml("):
            self.assertNotIn(duplicate, script, duplicate)

    def test_the_renderer_escapes_before_it_renders(self):
        script = asset("markdown.js")
        self.assertIn("/[&<>\"']/g", script)
        for entity in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
            self.assertIn(entity, script)
        self.assertNotIn("insertAdjacentHTML", script)

    def test_code_blocks_get_a_language_label_and_a_copy_button(self):
        css = asset("app.css")
        for selector in (
            ".bubble .body .md-lang",
            ".bubble .code-block",
            ".bubble .code-block .code-copy",
        ):
            self.assertIn(selector, css)
        script = asset("app.js")
        self.assertIn('pre.md-code', script)
        self.assertIn("button.dataset.copy = pre.textContent", script)
        self.assertIn("decorateCode(el.chatInner)", script)

    def test_tables_quotes_and_nested_lists_are_styled_from_tokens(self):
        css = asset("app.css")
        table = rule_block(css, ".bubble .body th,\n.bubble .body td")
        self.assertIn("var(--border)", table)
        self.assertIn("var(--sp-", table)
        self.assertIn("var(--muted)", rule_block(css, ".bubble .body blockquote.md-quote"))
        self.assertIn("var(--fs-large)", rule_block(css, ".bubble .body .md-h"))
        self.assertIn(
            "var(--sp-",
            rule_block(css, ".bubble .body li > ul,\n.bubble .body li > ol"),
        )


if __name__ == "__main__":
    unittest.main()
