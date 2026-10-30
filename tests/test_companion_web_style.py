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
        self.assertIn("syncApprovals(slots.approvals, data.approvals)", script)
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
            script.index("async function rememberMessage("):script.index("function openSources(")
        ]
        self.assertIn('post("/api/console/memory", { action: "remember", message_id: messageId })', body)
        self.assertIn("已保存为长期记忆", body)

    def test_source_jumps_to_the_evidence_tab(self):
        script = asset("app.js")
        body = script[
            script.index("function openSources("):script.index("async function switchMode(")
        ]
        self.assertIn('state.tab = "来源"', body)
        self.assertIn("state.inspectorOpen = true", body)
        self.assertIn("layout()", body)
        self.assertIn("renderInspector()", body)

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

    def test_avatar_art_lives_in_one_module(self):
        script = asset("avatar.js")
        self.assertIn("global.CompanionAvatar", script)
        for name in ("group: group", "mount: mount", "svg: svg"):
            self.assertIn(name, script)
        for name in ("app.js", "overlay.js"):
            with self.subTest(script=name):
                page = asset(name)
                self.assertNotIn("function avatarSvg", page)
                self.assertIn("CompanionAvatar.mount", page)
        for name in ("index.html", "overlay.html"):
            markup = asset(name)
            scripts = re.findall(r'<script src="([^"]+)"', markup)
            self.assertIn("/assets/avatar.js", scripts)
            page_script = "/assets/app.js" if name == "index.html" else "/assets/overlay.js"
            self.assertLess(scripts.index("/assets/avatar.js"), scripts.index(page_script))

    def test_avatar_moods_match_the_affect_labels(self):
        script = asset("avatar.js")
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
        self.assertIn("--pet-size: 56px", rule_block(css, "body.collapsed .overlay"))
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


if __name__ == "__main__":
    unittest.main()
