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
OUTLINE_NONE = re.compile(r"(?m)^([^\n{}]+)\{\s*outline:\s*none;\s*\}")
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

    def test_overlay_pet_fills_height_and_bubble_stays_clear(self):
        overlay = asset("overlay.css")
        block = rule_block(overlay, ".bubble")
        self.assertIn("max-height", block)
        self.assertIn("top: var(--sp-lg)", block)
        pet = rule_block(overlay, ".pet")
        self.assertIn(
            "height: calc(100% - var(--pet-top) - var(--pet-bottom))",
            pet,
        )
        self.assertIn(
            "width: calc(100% - var(--pet-right) - var(--pet-right))",
            pet,
        )

    def test_stage_canvas_fills_the_pet_box(self):
        block = rule_block(asset("overlay.css"), ".pet canvas")
        self.assertIn("display: block", block)
        self.assertIn("width: 100%", block)
        self.assertIn("height: 100%", block)

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
        tokens = asset("tokens.css")
        self.assertIn("outline: 2px solid var(--accent)", rule_block(tokens, ":focus-visible"))
        # Only the two text controls may drop their own outline; both delegate
        # the visible ring to the rounded container that wraps them.
        allowed = {
            ".composer textarea:focus-visible",
            ".rail-search input:focus-visible",
        }
        for name in PAGES + ("overlay.css",):
            selectors = {match.group(1).strip() for match in OUTLINE_NONE.finditer(asset(name))}
            self.assertLessEqual(selectors, allowed, name)

    def test_text_controls_ring_their_container(self):
        app = asset("app.css")
        for selector in (".composer textarea:focus-visible", ".rail-search input:focus-visible"):
            self.assertIn("outline: none", rule_block(app, selector), selector)
        composer = rule_block(app, ".composer:focus-within")
        self.assertIn("var(--accent)", composer)
        self.assertIn("0 0 0 2px var(--accent-soft)", composer)
        self.assertIn(
            "0 0 0 2px var(--accent-soft)",
            rule_block(app, ".rail-search:focus-within"),
        )


class ContextGaugeTests(unittest.TestCase):
    """M12.3-a: the ring is fed by projection numbers, never by display text."""

    def pill_source(self):
        script = asset("app.js")
        start = script.index("function renderPills()")
        end = script.index("function renderStatus(")
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
        # The list is toggled through the hidden attribute, which the flex rule
        # would otherwise override and leave the collapsed steps on screen.
        self.assertIn("display: none", rule_block(css, ".run-steps[hidden]"))
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
        # and leave nothing to read with. Both rail widths live in the token
        # layer so the icon state cannot drift from the labelled one.
        tokens = asset("tokens.css")
        self.assertIn("var(--rail-w)", rule_block(asset("app.css"), ".rail"))
        self.assertIn("var(--rail-w-collapsed)", rule_block(asset("app.css"), ".rail.collapsed"))
        for name in ("--rail-w", "--rail-w-collapsed"):
            self.assertIn("var(--sp-scale)", re.search(re.escape(name) + r":\s*([^;]+);", tokens).group(1))
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
        self.assertIn("decorateCode(node)", script)

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


class KeyedTranscriptTests(unittest.TestCase):
    """M12.3 C8: the transcript is reconciled by message id, never rebuilt wholesale."""

    def test_the_transcript_is_no_longer_one_innerhtml_assignment(self):
        script = asset("app.js")
        self.assertNotIn("el.chatInner.innerHTML = parts.join", script)
        self.assertIn("function syncMessages(container, messages)", script)
        self.assertIn('node.dataset.key = "m:" + block.id', script)
        self.assertIn('data-mid="${block.id}"', script)

    def test_persistent_slots_are_created_once_and_styled_from_tokens(self):
        script = asset("app.js")
        self.assertIn("function chatSlots()", script)
        self.assertIn("if (!list) {", script)
        self.assertIn("syncMessages(slots.list, data.messages)", script)
        self.assertIn("syncApprovals(slots.approvals, approvalViews(data))", script)
        # S04f 的已执行/已拒绝卡片走同一条 reconcile，不另开一套 DOM。
        self.assertIn("data.remote_cards", script)
        css = asset("app.css")
        slots = rule_block(
            css, "#msgList,\n#approvalList,\n#toolStack,\n#streamSlot"
        )
        self.assertIn("display: flex", slots)
        self.assertIn("flex-direction: column", slots)
        self.assertIn("gap: var(--sp-md)", slots)
        self.assertIn("display: none", rule_block(css, ".empty[hidden]"))

    def test_only_the_stream_row_is_cleared_when_a_run_ends(self):
        script = asset("app.js")
        self.assertIn("slots.stream.replaceChildren()", script)
        self.assertIn("slots.tools.replaceChildren()", script)
        render = script[script.index("function renderChat()"):script.index("  function approvalHtml(")]
        self.assertNotIn("chatInner.innerHTML", render)

    def test_changed_text_updates_the_bubble_in_place(self):
        script = asset("app.js")
        update = script[script.index("function updateMessageText("):script.index("function keyedNodes(")]
        self.assertIn("body.innerHTML = markdown(text)", update)
        self.assertIn("decorateCode(body)", update)
        self.assertIn("node.dataset.text = text", update)


class PendingQueueTests(unittest.TestCase):
    """M12.3 C5: input typed during a run waits in a dock, FIFO, instead of being refused."""

    def test_the_dock_sits_between_the_run_strip_and_the_pills(self):
        index = asset("index.html")
        self.assertGreater(index.index('id="queueDock"'), index.index('id="runStrip"'))
        self.assertLess(index.index('id="queueDock"'), index.index('id="pills"'))
        self.assertIn('id="queueDock" aria-label="待发消息队列" hidden', index)
        self.assertIn('id="queueCount"', index)
        self.assertIn('id="queueList"', index)

    def test_a_busy_composer_queues_instead_of_refusing(self):
        script = asset("app.js")
        send = script[script.index("async function sendTurn("):script.index("function enqueue(")]
        self.assertIn("if (state.run || state.queue.length)", send)
        self.assertIn("enqueue(text)", send)
        self.assertNotIn("请先停止或等待完成", script)
        self.assertIn("const QUEUE_LIMIT = 5", script)
        self.assertIn("state.queue.length >= QUEUE_LIMIT", script)

    def test_the_queue_drains_one_turn_per_run_and_keeps_failed_items(self):
        script = asset("app.js")
        flush = script[script.index("async function flushQueue("):script.index("async function stopRun(")]
        self.assertIn("state.queue.shift()", flush)
        self.assertIn('post("/api/turn"', flush)
        self.assertIn("beginRun(payload.run_id", flush)
        self.assertIn("state.queue.unshift(item)", flush)
        self.assertIn("state.flushing = false", flush)
        refresh = script[script.index("async function refresh()"):script.index("let refreshTimer")]
        self.assertIn("if (!state.run && state.queue.length) flushQueue();", refresh)

    def test_items_can_be_withdrawn_and_edited(self):
        script = asset("app.js")
        edit = script[script.index("function editQueueItem("):script.index("function dropQueueItem(")]
        self.assertIn("state.queue.splice(index, 1)", edit)
        self.assertIn("el.input.value = item.text", edit)
        self.assertIn("el.input.focus()", edit)
        drop = script[script.index("function dropQueueItem("):script.index("function resetQueue(")]
        self.assertIn("state.queue.filter((item) => item.id !== id)", drop)
        self.assertIn('data-queue-edit="${item.id}"', script)
        self.assertIn('data-queue-drop="${item.id}"', script)

    def test_switching_sessions_clears_the_queue_with_a_notice(self):
        script = asset("app.js")
        switch = script[script.index("async function switchSession("):script.index("async function newSession(")]
        self.assertIn('resetQueue("已切换会话，待发队列已清空。")', switch)
        self.assertIn("if (!data) return;", switch)
        create = script[script.index("async function newSession("):script.index("function openSettings(")]
        self.assertIn('resetQueue("已新建会话，待发队列已清空。")', create)
        reset = script[script.index("function resetQueue("):script.index("async function flushQueue(")]
        self.assertIn("state.queue = []", reset)
        self.assertIn("renderQueue()", reset)
        self.assertIn("state.queueEpoch += 1", reset)
        refresh = script[script.index("async function refresh()"):script.index("let refreshTimer")]
        self.assertIn("state.queueSession !== data.session_id", refresh)

    def test_the_dock_sizes_from_tokens(self):
        css = asset("app.css")
        self.assertIn("var(--sp-", rule_block(css, ".queue-dock"))
        self.assertIn("display: none", rule_block(css, ".queue-dock[hidden]"))
        queue_list = rule_block(css, ".queue-list")
        for token in ("var(--ctl-ghost)", "var(--sp-xs)", "overflow-y: auto", "list-style: none"):
            self.assertIn(token, queue_list)
        short = css[css.index("@media (max-height: 640px)") :]
        self.assertIn(".queue-list {\n    max-height: var(--ctl-ghost);", short)
        self.assertIn(".statusbar {\n    display: none;", short)
        item = rule_block(css, ".queue-item button")
        for token in ("var(--ctl-ghost)", "var(--fs-caption)", "var(--radius-pill)"):
            self.assertIn(token, item)


class MessageActionTests(unittest.TestCase):
    """M12.3 C3: bubble actions stay scoped to the rows the service allows."""

    def test_actions_follow_the_projection_ids(self):
        script = asset("app.js")
        ids = script[
            script.index("function messageActionIds("):script.index("function messageActionsHtml(")
        ]
        self.assertIn("state.data.message_actions", ids)
        self.assertIn("regenerate_id", ids)
        self.assertIn("source_id", ids)
        actions = script[
            script.index("function messageActionsHtml("):script.index("function messageRow(")
        ]
        self.assertIn('data-msg-act="regenerate"', actions)
        self.assertIn("(ids.regenerate", actions)
        self.assertIn('data-msg-act="remember"', actions)
        self.assertIn("(ids.source", actions)
        self.assertIn('data-msg-act="source"', actions)

    def test_regenerate_posts_then_joins_the_started_run(self):
        script = asset("app.js")
        body = script[
            script.index("async function regenerateMessage("):script.index("async function rememberMessage(")
        ]
        self.assertIn('post("/api/messages/regenerate", { message_id: messageId })', body)
        self.assertIn('beginRun(payload.run_id, "pending")', body)
        self.assertIn("if (state.run)", body)

    def test_remember_reuses_the_memory_console_endpoint(self):
        script = asset("app.js")
        body = script[
            script.index("async function rememberMessage("):script.index("function openInspector(tab) {")
        ]
        self.assertIn('post("/api/console/memory", { action: "remember", message_id: messageId })', body)
        self.assertIn("已保存为长期记忆", body)

    def test_source_jumps_to_the_evidence_tab(self):
        script = asset("app.js")
        opener = script[
            script.index("function openInspector(tab) {"):script.index("async function switchMode(")
        ]
        self.assertIn('state.tab = tab', opener)
        self.assertIn("state.inspectorOpen = true", opener)
        self.assertIn("layout()", opener)
        self.assertIn("renderInspector()", opener)
        self.assertIn('action.dataset.msgAct === "source") openInspector("来源")', script)

    def test_actions_rebuild_when_the_projection_changes(self):
        script = asset("app.js")
        node = script[
            script.index("function messageNode("):script.index("function updateMessageText(")
        ]
        self.assertIn("node.dataset.actions = messageActionsSignature(block)", node)
        sync = script[
            script.index("function syncMessages("):script.index("function approvalNode(")
        ]
        self.assertIn("updateMessageActions(node, block)", sync)

    def test_actions_hide_until_the_bubble_is_reached(self):
        css = asset("app.css")
        actions = rule_block(css, ".bubble .meta .msg-actions")
        self.assertIn("display: flex", actions)
        self.assertIn("flex-wrap: wrap", actions)
        self.assertIn(".bubble .meta .msg-act {\n  opacity: 0;", css)
        reveal = css[css.index(".msg:focus-within .bubble .meta .msg-act") :]
        self.assertIn("opacity: 1", reveal[:80])
        self.assertIn("@media (hover: none)", css)
        self.assertIn(".bubble .meta .msg-act {\n    opacity: 1;", css)


class OverlayCapabilityTests(unittest.TestCase):
    """M12.4: one avatar mount point, a real menu, and pushed updates."""

    def test_stage_module_is_the_only_figure(self):
        self.assertFalse((ASSETS / "avatar.js").exists())
        overlay = asset("overlay.js")
        self.assertNotIn("avatarSvg", overlay)
        self.assertNotIn("CompanionAvatar", overlay)
        self.assertIn("CompanionStage", overlay)
        self.assertIn("CompanionMeshStage", overlay)
        for name in ("app.js", "settings.js", "console.js"):
            with self.subTest(script=name):
                page = asset(name)
                self.assertNotIn("avatarSvg", page)
                self.assertNotIn("CompanionAvatar", page)

    def test_only_the_overlay_loads_the_live2d_stage(self):
        scripts = re.findall(
            r'<script src="([^"]+)"', asset("overlay.html")
        )
        self.assertIn("/assets/stage.js", scripts)
        self.assertIn("/assets/mesh-stage.js", scripts)
        for vendor in (
            "/assets/vendor/pixi.min.js",
            "/assets/vendor/live2dcubismcore.min.js",
            "/assets/vendor/pixi-live2d-display-cubism4.min.js",
            "/assets/vendor/mesh-avatar.min.js",
        ):
            self.assertIn(vendor, scripts)
        for stage in ("/assets/stage.js", "/assets/mesh-stage.js"):
            self.assertLess(scripts.index(stage), scripts.index("/assets/overlay.js"))
        self.assertNotIn("/assets/avatar.js", scripts)
        index = re.findall(r'<script src="([^"]+)"', asset("index.html"))
        self.assertNotIn("/assets/stage.js", index)
        self.assertNotIn("/assets/mesh-stage.js", index)
        self.assertNotIn("/assets/avatar.js", index)

    def test_expression_groups_match_the_affect_labels(self):
        script = asset("stage.js")
        warm = re.search(r"const WARM = \[([^\]]+)\]", script).group(1)
        low = re.search(r"const LOW = \[([^\]]+)\]", script).group(1)
        self.assertIn('"warm"', warm)
        self.assertIn('"positive"', warm)
        for label in ("low", "tired", "concerned"):
            self.assertIn('"' + label + '"', low)
        self.assertNotIn("happy", script)
        affect = (ASSETS.parent.parent.parent / "companion" / "affect.py").read_text(encoding="utf-8")
        for label in ("warm", "positive", "low", "tired", "concerned"):
            self.assertIn('"' + label + '"', affect)
        mesh = asset("mesh-stage.js")
        for label in ("warm", "positive", "low", "tired", "concerned"):
            self.assertIn('"' + label + '"', mesh)

    def test_context_menu_offers_the_four_actions(self):
        markup = asset("overlay.html")
        self.assertIn('role="menu"', markup)
        for action in ("chat", "toggle-main", "dnd", "hide"):
            self.assertIn('data-menu="{}"'.format(action), markup)
        script = asset("overlay.js")
        self.assertIn("openMenu(event.clientX, event.clientY)", script)
        self.assertIn('state.host.toggleMain()', script)
        self.assertIn('{ action: "settings", enabled: !enabled }', script)
        self.assertIn('action: "read", event_id: item.id', script)

    def test_hidden_attributes_win_over_author_display(self):
        css = asset("overlay.css")
        self.assertIn("[hidden] {\n  display: none !important;", css)
        quick = rule_block(css, ".quick")
        self.assertIn("pointer-events: none", quick)
        reveal = css[css.index(".overlay:focus-within .quick") :]
        self.assertIn("pointer-events: auto", reveal[:120])

    def test_collapsed_state_is_visual_and_persisted(self):
        css = asset("overlay.css")
        self.assertIn("body.collapsed .overlay", css)
        self.assertIn("--ball-size: 56px", rule_block(css, "body.collapsed .overlay"))
        script = asset("overlay.js")
        body = script[
            script.index("function applyCollapsed("):script.index("function apply(")
        ]
        self.assertIn('document.body.classList.toggle("collapsed"', body)
        self.assertIn("overlay_collapsed: state.collapsed", body)
        self.assertIn('api("/api/ui"', body)

    def test_overlay_subscribes_to_events_and_polls_only_as_fallback(self):
        script = asset("overlay.js")
        self.assertIn('new EventSource("/api/events?t="', script)
        self.assertIn('payload.type === "refresh" || payload.type === "ui"', script)
        self.assertIn("if (!state.live) refresh();", script)
        self.assertNotIn("setInterval(refresh, 8000)", script)

    def test_proactive_bubbles_are_queued_one_at_a_time(self):
        script = asset("overlay.js")
        self.assertIn("const BUBBLE_MS = 9000;", script)
        self.assertIn("const QUEUE_LIMIT = 3;", script)
        self.assertIn("state.timer = setTimeout(acknowledge, BUBBLE_MS);", script)
        self.assertIn("if (state.kind === \"proactive\") acknowledge();", script)
        self.assertIn('state.seen.add(event.id)', script)


class Live2dRuntimeContractTests(unittest.TestCase):
    """M13.3: Live2D is the only figure; unavailable means no pet, not a stand-in."""

    def test_vendored_runtime_and_stage_are_served_locally(self):
        for name in (
            "vendor/pixi.min.js",
            "vendor/live2dcubismcore.min.js",
            "vendor/pixi-live2d-display-cubism4.min.js",
            "vendor/process-env.js",
            "vendor/mesh-avatar.min.js",
            "stage.js",
            "mesh-stage.js",
        ):
            with self.subTest(name=name):
                self.assertTrue((ASSETS / name).is_file(), name)

    def test_mesh_stage_mounts_the_archive_without_a_fallback(self):
        script = asset("mesh-stage.js")
        for needle in (
            "MeshAvatarRuntime.loadMeshAvatar",
            "X-Companion-Token",
            "manual: true",
            "setExpression",
            "advance(0)",
            "setAutoIdle(false)",
            "setSwayGain(0)",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, script)
        self.assertNotIn("<img", script)
        self.assertNotIn("avatarSvg", script)

    def test_hit_region_is_what_keeps_clicks_on_the_pet(self):
        script = asset("overlay.js")
        self.assertIn("window.__companionHit", script)
        self.assertIn("document.elementFromPoint(x, y)", script)
        for selector in ("#pet, #ball, #bubble, #quick, #menu, #close"):
            self.assertIn(selector, script)

    def test_gestures_come_from_the_manifest_and_the_hands_stay_out(self):
        stage = asset("stage.js")
        for needle in (
            'const REST_POSE = [["Movement2", 1]];',
            "const IDLE_GROUP = /idle/i;",
            "function gesturePool(motions, groups)",
            "groups.idle_pool",
            'internal.on("beforeModelUpdate"',
            "getLoopDuration()",
            "controller.focus(clamp(x), clamp(y));",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, stage)
        # The pack's IDLE clip is a six second crying performance; looping it is
        # what painted tears and sweat over a calm face.
        self.assertNotIn("startIdle", stage)
        self.assertNotIn('startMotion("idle"', stage)
        overlay = asset("overlay.js")
        for needle in (
            "window.__companionCursor",
            "state.stage.setFocus(",
            "applyMotions(data.motion_events || [])",
            'api("/api/overlay/motion"',
            "state.motionSeen.has(event.id)",
            "motions: pet.motions || [],",
            "motionGroups: pet.motion_groups || {},",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, overlay)
        mesh = asset("mesh-stage.js")
        self.assertIn("setMotion: () => {}", mesh)
        self.assertIn("setFocus: () => {}", mesh)
        # One JavaScript round trip per 120ms tick: the tick that finds the cursor
        # outside the card sends the gaze-only script instead of the hit test.
        shell = (ASSETS.parent / "shell.py").read_text(encoding="utf-8")
        self.assertIn("window.__companionCursor(x,y)", shell)
        self.assertIn("window.__companionHit(x,y)", shell)
        self.assertIn("def _hit_script(x, y):", shell)
        self.assertIn("def _cursor_script(x, y):", shell)
        self.assertIn("self._feed_cursor(local)", shell)

    def test_mask_textures_scale_for_complex_live2d_models(self):
        stage = asset("stage.js")
        for needle in (
            "const MASKS_PER_TEXTURE = 32;",
            "const SINGLE_TEXTURE_MASK_LIMIT = 36;",
            "manager.getClippingMaskCount()",
            "renderer.initialize(core, textures)",
            "ensureMaskCapacity(model);",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, stage)

    def test_reduce_motion_and_full_height_stay_on_the_stage_container(self):
        stage = asset("stage.js")
        self.assertIn("autoStart: !options.reduceMotion", stage)
        self.assertIn("setReduceMotion", stage)
        overlay = asset("overlay.css")
        self.assertIn(
            "height: calc(100% - var(--pet-top) - var(--pet-bottom))",
            rule_block(overlay, ".pet"),
        )
        script = asset("overlay.js")
        self.assertIn(".mount(el.pet, options)", script)
        self.assertIn("reduceMotion", script)
        self.assertIn("ResizeObserver(resizeStage).observe(el.pet)", script)
        self.assertIn("if (!state.stage || el.pet.hidden) return;", script)
        self.assertIn("if (!state.collapsed) resizeStage();", script)

    def test_settings_explain_why_the_pet_is_disabled(self):
        script = asset("settings.js")
        self.assertIn("disabled aria-disabled", script)
        self.assertIn("桌面浮窗不可用", script)
        self.assertIn("不会显示替代形象", script)
        self.assertIn("pet.available", script)
        self.assertIn("avatar.mavatar", script)

    def test_main_window_keeps_proactive_messages_reachable(self):
        script = asset("app.js")
        self.assertIn("function proactiveCard()", script)
        self.assertIn("state.data.proactive_events", script)
        self.assertIn("data-proactive-read", script)
        self.assertIn("data-proactive-read-all", script)


class CharacterAssetSlotTests(unittest.TestCase):
    """M13.1: slots show the user's avatar when present and stay empty otherwise."""

    def test_header_renders_the_avatar_asset(self):
        script = asset("app.js")
        self.assertIn("function renderAvatar()", script)
        self.assertIn("(state.data.assets || {}).avatar", script)
        self.assertIn("avatar.url", script)
        self.assertIn("avatar.stamp", script)
        self.assertIn("el.avatarSlot.innerHTML = src", script)
        self.assertIn('alt=""', script)

    def test_empty_slot_does_not_hold_space(self):
        css = asset("app.css")
        self.assertIn("display: none", rule_block(css, "#avatarSlot:empty"))
        block = rule_block(css, ".avatar")
        self.assertIn("var(--fs-display)", block)
        self.assertIn("object-fit: cover", block)

    def test_settings_and_console_share_the_same_slot(self):
        for name, sheet in (("settings", "settings.css"), ("console", "console.css")):
            with self.subTest(page=name):
                script = asset(name + ".js")
                self.assertIn('class="persona-avatar"', script)
                self.assertIn("avatar.url", script)
                self.assertIn("avatar.stamp", script)
                css = asset(sheet)
                self.assertIn("var(--fs-display)", rule_block(css, ".persona-avatar"))
                self.assertIn("object-fit: cover", rule_block(css, ".persona-avatar"))
                self.assertIn("display: flex", rule_block(css, ".persona-head"))

    def test_asset_route_is_token_gated_and_reads_only_the_slot(self):
        server = (ASSETS.parent / "server.py").read_text(encoding="utf-8")
        self.assertIn('re.match(r"^/api/asset/([a-z]+)$", path)', server)
        self.assertIn("avatar_asset()", server)
        self.assertIn("background_asset()", server)
        self.assertNotIn('"/assets/" + slot', server)
        start = server.index("def _character_asset(")
        block = server[start : server.index("def _authorized(", start)]
        self.assertIn("read_bytes()", block)
        self.assertIn("Content-Length", block)

    def test_bootstrap_publishes_the_asset_state(self):
        server = (ASSETS.parent / "server.py").read_text(encoding="utf-8")
        self.assertIn('"assets": self.assets(),', server)
        self.assertIn('"/api/asset/{}".format(slot)', server)

    def test_doctor_reports_character_assets(self):
        doctor = (ASSETS.parents[3] / "doctor.py").read_text(encoding="utf-8")
        self.assertIn("character_assets_check", doctor)
        self.assertIn('check("Character assets", False', doctor)
        self.assertIn('"Character assets"', doctor)


class BackgroundLayerTests(unittest.TestCase):
    """M13.2: one photo switch for the three windows, never the overlay."""

    def test_the_module_is_shared_by_chrome_windows_only(self):
        module = asset("background.js")
        self.assertIn("CompanionBackground", module)
        self.assertIn('root.dataset.bg = "on"', module)
        self.assertIn('style.removeProperty("--bg-image")', module)
        for page in ("index.html", "settings.html", "console.html"):
            with self.subTest(page=page):
                self.assertIn("/assets/background.js", asset(page))
        self.assertNotIn("/assets/background.js", asset("overlay.html"))

    def test_each_page_applies_the_switch(self):
        for script in ("app.js", "settings.js"):
            with self.subTest(script=script):
                self.assertIn("CompanionBackground.apply(", asset(script))
        self.assertIn(
            "CompanionBackground.apply(bootstrap.ui, bootstrap.assets, token)",
            asset("console.js"),
        )

    def test_the_scrim_seam_is_assigned_only_for_a_background(self):
        tokens = asset("tokens.css")
        self.assertEqual(2, tokens.count("--bg-scrim:"))
        self.assertIn('html[data-bg="on"]', tokens)
        self.assertIn(
            "--bg-scrim: var(--bg-scrim-image);",
            rule_block(tokens, 'html[data-bg="on"]'),
        )
        for theme in (
            ':root,\n:root[data-theme="charcoal-pink"]',
            ':root[data-theme="pearl-white"]',
        ):
            with self.subTest(theme=theme):
                self.assertIn("--bg-scrim-image:", rule_block(tokens, theme))
        self.assertIn("var(--bg-image) center / cover no-repeat", rule_block(asset("app.css"), ".shell"))
        self.assertIn("var(--bg-image) center / cover no-repeat", rule_block(tokens, ".win"))

    def test_settings_offer_use_and_clear_without_deleting_the_file(self):
        script = asset("settings.js")
        self.assertIn("function panelBackground()", script)
        self.assertIn("background_enabled", script)
        self.assertIn('data-bg-use', script)
        self.assertIn('data-bg-clear', script)
        self.assertIn("不会复制、重命名或删除你的文件", script)
        css = asset("settings.css")
        self.assertIn("object-fit: cover", rule_block(css, ".bg-preview"))
        self.assertIn("height: calc(var(--fs-display) * 8)", rule_block(css, ".bg-preview"))


class SettingsZoneContractTests(unittest.TestCase):
    """M15 slice 4: the five remaining zones, and what the page may not fake."""

    ZONES = {
        "persona": "panelPersona",
        "rag": "panelRag",
        "mcp": "panelMcp",
        "memory": "panelMemory",
        "advanced": "panelAdvanced",
    }

    @staticmethod
    def body(script, header):
        start = script.index(header)
        return script[start : script.index("\n  }", start)]

    def test_every_zone_is_a_panel_backed_by_the_settings_view(self):
        script = asset("settings.js")
        registry = script[script.index("const PANELS = {") :]
        registry = registry[: registry.index("\n  };")]
        for zone, fn in self.ZONES.items():
            with self.subTest(zone=zone):
                self.assertIn("{}: {},".format(zone, fn), registry)
                self.assertIn("function {}() {{".format(fn), script)
        opener = script[
            script.index('document.addEventListener("click", async (event) => {') :
        ]
        opener = opener[: opener.index("\n    });")]
        for zone in ("model", "rag", "memory", "advanced"):
            self.assertIn('"{}"'.format(zone), opener, zone)
        self.assertIn('if (state.panel === "background" || state.panel === "persona") await refresh();', opener)

    def test_an_unwired_capability_shows_a_disabled_box_with_a_reason(self):
        script = asset("settings.js")
        block = self.body(script, "function unwired(label, note) {")
        self.assertIn('<div class="unwired" aria-disabled="true">', block)
        self.assertIn('<span class="pill">未接入</span>', block)
        for control in ("<input", "<select", "<button"):
            self.assertNotIn(control, block)

    def test_rag_tunes_real_recall_and_marks_the_doc_library_absent(self):
        script = asset("settings.js")
        panel = self.body(script, "function panelRag() {")
        self.assertIn('numberRow("memory_top_k"', panel)
        self.assertIn('data-setting="memory_score_threshold"', panel)
        self.assertIn("budgetBlock()", panel)
        self.assertIn('unwired("上传文档"', panel)
        self.assertIn('unwired("文档列表"', panel)
        self.assertNotIn("<table", panel)

    def test_the_mcp_zone_is_a_real_server_list_with_only_masked_secrets(self):
        # 切片 4 去灰显：面板的每一块都必须是「有后端」的形状，且密钥只以掩码出现（Q2）。
        script = asset("settings.js")
        panel = self.body(script, "function panelMcp() {")
        self.assertIn("mcpServerCard(row)", panel)
        self.assertIn("data-mcp-add", panel)
        self.assertIn("data-mcp-reload", panel)
        self.assertNotIn("unwired(", panel)
        card = self.body(script, "function mcpServerCard(row) {")
        self.assertIn("data-mcp-enable=", card)
        self.assertIn("连接并获取工具", card)
        self.assertIn("错误文案不会回显令牌原文", card)
        tools = self.body(script, "function mcpToolList(row) {")
        self.assertIn("data-mcp-tool=", tools)
        self.assertIn("未开启的工具不进模型可见清单", tools)
        sheet = self.body(script, "function mcpSheetMarkup(row) {")
        self.assertIn("config/mcp_keys.json", sheet)
        opener = self.body(script, "async function openMcpSheet(existingId) {")
        self.assertIn("delete record._secrets;", opener)
        self.assertIn('action: "credentials"', opener)
        removal = self.body(script, "async function removeMcpServer(serverId) {")
        self.assertIn("CompanionDialog.confirm(", removal)
        self.assertIn("danger: true", removal)

    def test_the_remote_call_card_keeps_the_three_board_states_and_no_real_undo(self):
        # S04f：待确认、已执行回执、被档位拒绝共用一张卡；「撤销」只是解释（K3）。
        script = asset("app.js")
        self.assertIn('external: "外部调用"', script)
        card = self.body(script, "function approvalHtml(view) {")
        self.assertIn("待确认外部调用", card)
        self.assertIn("允许这次调用", card)
        self.assertIn('["服务器", remoteServer(preview)]', card)
        self.assertIn("argumentLines(preview.arguments)", card)
        self.assertIn(
            'remote ? "导出时不含凭据。" : "结果以执行器回执为准。"',
            card,
        )
        self.assertIn("外部调用不提供撤销，点击只会说明原因。", card)
        self.assertIn('data-undo="${escapeHtml(undoId)}" aria-disabled="true"', card)
        self.assertIn("view.warning", card)
        # 已执行/已拒绝走同一条 reconcile，不另开一套 DOM。
        self.assertIn("function approvalViews(data) {", script)
        undo = self.body(script, "async function undoReceipt(receiptId) {")
        self.assertIn('post("/api/receipts/undo", { receipt_id: receiptId })', undo)
        css = asset("app.css")
        self.assertIn("border-color: var(--border)", rule_block(css, ".approval.settled"))
        self.assertIn(
            "border-color: var(--border-strong)", rule_block(css, ".approval.denied")
        )

    def test_clearing_memory_is_the_one_destructive_button_and_asks_first(self):
        script = asset("settings.js")
        panel = self.body(script, "function panelMemory() {")
        self.assertIn('switchRow(\n        "memory_enabled"', panel)
        self.assertIn('class="danger-zone"', panel)
        self.assertIn('data-purge-memories ${count ? "" : "disabled"}', panel)
        purge = self.body(script, "async function purgeMemories() {")
        self.assertIn("CompanionDialog.confirm(", purge)
        self.assertIn("danger: true", purge)
        self.assertIn('post("/api/console/memory", { action: "purge_all" })', purge)
        self.assertIn("await refresh();", purge)

    def test_avatar_import_is_a_file_picker_that_only_touches_the_slot(self):
        script = asset("settings.js")
        panel = self.body(script, "function panelPersona() {")
        self.assertIn('type="file" id="avatarFile" accept=".png,.jpg,.jpeg,.webp"', panel)
        self.assertIn("data-avatar-import", panel)
        self.assertIn("你选的原始图片不动", script)
        importer = self.body(script, "async function importAvatarFile() {")
        self.assertIn("if (file.size > 8 * 1024 * 1024)", importer)
        self.assertIn("reader.readAsDataURL(file);", importer)
        self.assertIn('post("/api/asset/avatar", { filename: file.name, data: dataUrl })', importer)
        self.assertIn('input.value = "";', importer)
        self.assertNotIn("background", importer)

    def test_the_advanced_zone_shows_options_diagnoses_and_no_invented_log(self):
        script = asset("settings.js")
        panel = self.body(script, "function panelAdvanced() {")
        self.assertIn("(permissions.tiers || [])", panel)
        self.assertIn('selectRow("tool_permission"', panel)
        for field in ("diag.data_dir", "diag.schema_version", "diag.locked_by", "diag.backups"):
            self.assertIn(field, panel)
        self.assertIn("python doctor.py", panel)
        self.assertIn("程序目前没有写日志文件", panel)
        self.assertNotIn("日志文件", panel.replace("程序目前没有写日志文件", ""))

    def test_the_server_owns_the_labels_for_formats_and_tiers(self):
        script = asset("settings.js")
        for helper in ("function outputFormats() {", "function permissionTiers() {"):
            with self.subTest(helper=helper):
                body = self.body(script, helper)
                self.assertIn("|| []", body)
                self.assertNotIn("保守", body)
                self.assertNotIn("信任", body)

    def test_the_new_zone_blocks_size_themselves_from_tokens(self):
        css = asset("settings.css")
        self.assertIn("border: 1px dashed var(--border-strong)", rule_block(css, ".unwired"))
        self.assertIn("gap: var(--sp-md)", rule_block(css, ".unwired-head"))
        self.assertIn("border: 1px solid var(--danger)", rule_block(css, ".danger-zone"))
        self.assertIn("color: var(--danger)", rule_block(css, ".danger-zone .ghost"))
        self.assertIn(
            "min-height: var(--ctl-segment)",
            rule_block(css, ".field.file::file-selector-button"),
        )
        self.assertIn("grid-template-columns: auto 1fr", rule_block(css, ".diag-grid"))
        self.assertIn("overflow-wrap: anywhere", rule_block(css, ".diag-path"))
        self.assertIn("overflow-wrap: anywhere", rule_block(css, ".panel .hint"))
        self.assertIn("font-size: var(--fs-body)", rule_block(css, ".group > summary"))


class DesktopCapabilityContractTests(unittest.TestCase):
    def test_settings_exposes_all_desktop_capability_tabs(self):
        script = asset("settings.js")
        self.assertIn('{ id: "desktop", name: "桌面", desc:', script)
        self.assertIn('desktop: { cat: "desktop", name: "桌面能力"', script)
        for tab in ("directories", "files", "apps", "chains"):
            self.assertIn('["{}",'.format(tab), script)
        for action in (
            "grant-directory",
            "search-files",
            "delete-file",
            "move-file",
            "register-app",
            "save-chain",
            "run-chain",
        ):
            self.assertIn('"{}"'.format(action), script)

    def test_desktop_cards_use_the_shared_approval_language(self):
        script = asset("app.js")
        self.assertIn('"app-launch": "启动应用"', script)
        self.assertIn('"launch-chain": "启动链"', script)
        self.assertIn("data.desktop_cards", script)
        self.assertIn("允许这次删除", script)
        self.assertIn("允许启动链", script)

    def test_desktop_layout_wraps_instead_of_forcing_fixed_heights(self):
        css = asset("settings.css")
        item = rule_block(css, ".desk-item")
        self.assertIn("display: flex", item)
        self.assertIn("gap: var(--sp-lg)", item)
        self.assertIn("flex-wrap: wrap", rule_block(css, ".desk-actions"))
        self.assertIn("flex-wrap: wrap", rule_block(css, ".desk-tabs"))
        self.assertIn("min-height: var(--ctl-segment)", rule_block(css, ".desk-tabs button"))


if __name__ == "__main__":
    unittest.main()
