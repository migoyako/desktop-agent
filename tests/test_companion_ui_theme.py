"""Theme, chat transcript and inspector tests for the desktop chrome."""

import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_framework.companion.ui.state import UiStateStore, clamp_position
from agent_framework.companion.ui.theme import (
    CHARCOAL_PINK,
    DEFAULT_THEME,
    FONT_SCALES,
    PEARL_WHITE,
    SPACE_TOKENS,
    THEMES,
    TYPE_TOKENS,
    avatar_palette,
    font_px,
    normalize_font_scale,
    normalize_theme,
    spacing,
    stylesheet,
    theme,
    theme_choices,
)


try:
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from agent_framework.companion.app import ApprovalPreview, ApprovalView, RunStep
    from agent_framework.companion.ui.approval_card import ApprovalCard
    from agent_framework.companion.ui.chat_view import (
        STREAM_CURSOR,
        ChatView,
        EmptyState,
        MessageBubble,
        ToolCard,
        WrapLabel,
    )
    from agent_framework.companion.ui.inspector import InspectorPanel
    from agent_framework.companion.ui.qt_app import (
        SessionRow,
        _relative_time,
        _session_group,
    )
    from agent_framework.companion.ui.run_bar import RunBar
except ImportError:
    QApplication = None
    ChatView = None
    EmptyState = None
    InspectorPanel = None
    MessageBubble = None
    ToolCard = None
    WrapLabel = None
    STREAM_CURSOR = None
    QSize = None
    QTest = None
    Qt = None
    SessionRow = None
    _relative_time = None
    _session_group = None
    RunBar = None
    ApprovalCard = None
    ApprovalPreview = None
    ApprovalView = None
    RunStep = None


class ThemeTokenTest(unittest.TestCase):
    def test_both_themes_expose_the_same_token_keys(self):
        self.assertEqual(set(CHARCOAL_PINK), set(PEARL_WHITE))
        self.assertEqual(set(THEMES), {"charcoal-pink", "pearl-white"})
        self.assertEqual(normalize_theme(None), DEFAULT_THEME)
        self.assertEqual(normalize_theme("  CHARCOAL-PINK  "), "charcoal-pink")
        self.assertEqual(normalize_theme("nope"), DEFAULT_THEME)

    def test_theme_returns_a_copy(self):
        tokens = theme()
        tokens["accent"] = "#000000"
        self.assertEqual(theme()["accent"], CHARCOAL_PINK["accent"])
        self.assertEqual(theme("bogus")["id"], DEFAULT_THEME)

    def test_theme_choices_cover_both_packs(self):
        self.assertEqual(
            [key for key, _label in theme_choices()],
            ["pearl-white", "charcoal-pink"],
        )

    def test_avatar_palette_follows_mood_and_scheme(self):
        dark = theme("charcoal-pink")
        self.assertEqual(avatar_palette(dark, "low")["accent"], dark["avatar_mood_low"])
        self.assertEqual(avatar_palette(dark, "tired_today")["accent"], dark["avatar_mood_low"])
        self.assertEqual(avatar_palette(dark, "warm")["accent"], dark["avatar_mood_warm"])
        self.assertEqual(avatar_palette(dark, "calm")["accent"], dark["accent"])
        self.assertEqual(avatar_palette(dark, "calm")["shadow"], dark["selected"])
        light = theme("pearl-white")
        self.assertEqual(avatar_palette(light, "calm")["shadow"], light["hover"])

    def test_stylesheet_emits_the_expected_selectors(self):
        css = stylesheet("charcoal-pink")
        for selector in (
            "QWidget#shell",
            "QFrame#rail",
            "QFrame#modeSwitch QPushButton#active",
            "QScrollArea#chat",
            "QWidget#chatList",
            "QFrame#bubbleUser",
            "QFrame#toolCardDone",
            "QFrame#toolCardFailed",
            "QLabel#bubbleRole",
            "QPushButton#bubbleAction",
            "QLabel#dateDivider",
            "QFrame#runBar",
            "QLabel#runStage",
            "QLabel#runDetail",
            "QLabel#runElapsed",
            "QPushButton#runStop",
            "QPushButton#jumpLatest",
            "QFrame#inspector",
            "QScrollArea#inspectorArea",
            "QWidget#inspectorList",
            "QLabel#pillActive",
            "QPushButton#railAction",
            "QLineEdit#railSearch",
            "QFrame#workspaceRow",
            "QPushButton#railMini",
            "QFrame#sessionRow",
            "QFrame#sessionActive",
            "QFrame#settingsRow",
            "QPushButton#settingsButton",
            "QFrame#settingsNav",
            "QFrame#settingsNavItem",
            "QLineEdit#settingsSearch",
            "QFrame#settingsSummaryCard",
            "QPushButton#settingsExpand",
        ):
            self.assertIn(selector + " {", css)
        self.assertIn(CHARCOAL_PINK["accent"], css)
        self.assertNotIn(PEARL_WHITE["page"], css)

    def test_stylesheet_switches_every_surface(self):
        light = stylesheet("pearl-white")
        self.assertIn(PEARL_WHITE["page"], light)
        self.assertNotIn(CHARCOAL_PINK["page"], light)

    def test_stylesheet_has_no_unresolved_properties(self):
        for name in THEMES:
            css = stylesheet(name)
            self.assertNotIn("None:", css)
            self.assertNotIn("None;", css)
            self.assertNotIn("{}", css)
            self.assertTrue(css.endswith("}"))

    def test_type_and_spacing_tokens_share_one_contract(self):
        self.assertEqual(FONT_SCALES, (100, 125, 150, 200))
        self.assertEqual(normalize_font_scale(125), 125)
        self.assertEqual(normalize_font_scale(140), 100)
        self.assertEqual(normalize_font_scale("nope"), 100)
        self.assertEqual(font_px("display", 200), "48px")
        self.assertEqual(spacing("md"), 8)
        self.assertEqual(spacing("xl"), 18)
        self.assertEqual(spacing("missing"), 8)
        for key in ("caption", "meta", "ui", "body", "large", "subtitle", "display"):
            self.assertIn(key, TYPE_TOKENS)

        sizes = {
            int(value)
            for value in re.findall(
                r"font-size:\s*(\d+)px", stylesheet("charcoal-pink")
            )
        }
        self.assertEqual(sizes, set(TYPE_TOKENS.values()))

    def test_font_scale_preset_scales_every_font_size(self):
        base = stylesheet("charcoal-pink")
        doubled = stylesheet("charcoal-pink", font_scale=200)
        self.assertIn("font-size: 14px", base)
        self.assertNotIn("font-size: 28px", base)
        self.assertIn("font-size: 28px", doubled)
        self.assertIn("font-size: 48px", doubled)
        # Qt style sheets stop font propagation, so the bubble body needs its
        # own rule or the transcript would stay at the application font.
        bubble = re.search(r"QLabel#bubbleBody\s*\{[^}]*font-size:\s*(\d+)px", base)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.group(1), str(TYPE_TOKENS["body"]))
        doubled_bubble = re.search(
            r"QLabel#bubbleBody\s*\{[^}]*font-size:\s*(\d+)px", doubled
        )
        self.assertEqual(int(doubled_bubble.group(1)), TYPE_TOKENS["body"] * 2)
        self.assertEqual(
            stylesheet("pearl-white", font_scale=999),
            stylesheet("pearl-white"),
        )


class ChatViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if ChatView is None:
            raise unittest.SkipTest("PySide6 unavailable")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = ChatView()
        self.view.setStyleSheet(stylesheet())
        self.view.resize(600, 400)
        self.view.show()
        self.app.processEvents()

    def tearDown(self):
        self.view.close()

    def blocks(self):
        return [
            {"kind": "message", "id": "u1", "role": "user", "name": "你",
             "time": "20:01", "text": "帮我整理会议纪要"},
            {"kind": "message", "id": "a1", "role": "assistant", "name": "蕾米埃尔",
             "time": "20:01", "text": "好的，我先按风险和决议两类归纳。"},
            {"kind": "tool", "id": "t1", "name": "web_search", "state": "done",
             "summary": "5 条结果"},
            {"kind": "tool", "id": "t2", "name": "write_note", "state": "running",
             "summary": ""},
        ]

    def test_renders_bubbles_and_tool_cards(self):
        self.view.render(self.blocks())
        self.app.processEvents()
        self.assertEqual(self.view.bubble_count(), 2)
        self.assertEqual(self.view.tool_card_count(), 2)
        self.assertEqual(self.view._rows[0].objectName(), "bubbleUser")
        self.assertEqual(self.view._rows[1].objectName(), "bubbleAgent")
        self.assertEqual(self.view._rows[2].objectName(), "toolCardDone")
        self.assertEqual(self.view._rows[3].objectName(), "toolCard")
        text = self.view.plain_text()
        self.assertIn("帮我整理会议纪要", text)
        self.assertIn("web_search", text)
        self.assertEqual(self.view._rows[0].role_label.text(), "你")
        self.assertEqual(self.view._rows[1].role_label.text(), "蕾米埃尔")
        self.assertEqual(self.view._rows[1].meta_label.text(), "20:01")

    def test_body_renders_the_safe_markdown_subset(self):
        self.view.render([
            {
                "kind": "message",
                "id": "a1",
                "role": "assistant",
                "text": "**重点** 用 `print(1)` 和 <img src=x>",
            },
        ])
        self.app.processEvents()
        body = self.view._rows[0].body
        self.assertIn("<b>重点</b>", body.text())
        self.assertIn("<code>print(1)</code>", body.text())
        self.assertNotIn("<img", body.text())
        self.assertIn("<img src=x>", body.source_text())
        self.assertIn("<img src=x>", self.view.plain_text())

    def test_body_embeds_the_shared_style_block_and_follows_the_font_scale(self):
        self.view.render([
            {
                "kind": "message",
                "id": "a1",
                "role": "assistant",
                "text": "# 标题\n\n| 项目 | 状态 |\n|:--|--:|\n| 周报 | 草稿 |\n\n```python\nprint(1)\n```",
            },
        ])
        self.app.processEvents()
        body = self.view._rows[0].body
        html = body.text()
        self.assertTrue(html.startswith("<style>"))
        self.assertIn("md-table", html)
        self.assertIn("md-lang", html)
        self.assertIn("font-size: 16px", html)

        self.view.setStyleSheet(stylesheet(font_scale=200))
        self.app.processEvents()
        self.assertIn("font-size: 32px", body.text())

        self.view.setStyleSheet(stylesheet(font_scale=125))
        self.app.processEvents()
        self.assertIn("font-size: 20px", body.text())

    def test_copy_button_copies_the_original_markdown(self):
        source = "**原文** 与 `代码`"
        self.view.render([
            {"kind": "message", "id": "a1", "role": "assistant", "text": source},
        ])
        self.app.processEvents()
        QApplication.clipboard().setText("")
        self.view._rows[0].copy_button.click()
        self.app.processEvents()
        self.assertEqual(QApplication.clipboard().text(), source)

    def test_date_dividers_mark_each_day_once(self):
        self.view.render([
            {"kind": "message", "id": "m1", "role": "user", "text": "早",
             "date": "2026-10-28"},
            {"kind": "message", "id": "m2", "role": "assistant", "text": "早呀",
             "date": "2026-10-28"},
            {"kind": "message", "id": "m3", "role": "user", "text": "昨天",
             "date": "2026-10-27"},
        ])
        self.app.processEvents()
        self.assertEqual(self.view.date_divider_count(), 2)
        self.assertEqual(self.view.bubble_count(), 3)

    def test_scrolling_up_stops_following_and_offers_a_jump_back(self):
        long_text = "长回复内容 " * 120
        blocks = [
            {"kind": "message", "id": "m{}".format(index), "role": "assistant",
             "text": long_text}
            for index in range(8)
        ]
        self.view.render(blocks)
        self.app.processEvents()
        self.app.processEvents()
        bar = self.view.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        bar.setValue(0)
        self.app.processEvents()
        self.assertFalse(self.view._stick)
        self.assertFalse(self.view.jump_button.isHidden())

        self.view.render(blocks, "新的片段")
        self.app.processEvents()
        self.assertTrue(self.view.jump_button.isVisible())
        self.assertIn("有新内容", self.view.jump_button.text())

        self.view.jump_button.click()
        self.app.processEvents()
        self.assertTrue(self.view._stick)
        self.assertTrue(self.view.jump_button.isHidden())

    def test_empty_transcript_shows_mode_aware_welcome(self):
        self.view.set_empty_context("chat", "")
        self.view.render([])
        self.app.processEvents()
        self.assertEqual(self.view.bubble_count(), 0)
        self.assertIn("今天想聊点什么", self.view.plain_text())
        empty = self.view.findChild(EmptyState)
        self.assertIsNotNone(empty)
        self.assertEqual(empty.property("mode"), "chat")
        self.assertFalse(empty.recent_label.isVisible())
        self.assertEqual(
            [button.text() for button in empty.action_buttons],
            ["整理今天的想法", "陪我复盘一下", "随便聊聊"],
        )
        self.assertEqual(
            empty.action_buttons[0].accessibleName(),
            "快捷开场：整理今天的想法",
        )
        prompts = []
        self.view.quick_start.connect(prompts.append)
        empty.action_buttons[1].click()
        self.assertEqual(prompts, ["陪我复盘一下"])

    def test_empty_state_follows_mode_and_recent_task(self):
        self.view.set_empty_context("work", "最近任务：写周报（进行中）")
        self.view.render([])
        self.app.processEvents()
        self.assertIn("把一件事交给我", self.view.plain_text())
        empty = self.view.findChild(EmptyState)
        self.assertEqual(empty.property("mode"), "work")
        self.assertTrue(empty.recent_label.isVisible())
        self.assertIn("最近任务", empty.recent_label.text())

        self.view.set_empty_context("chat", "")
        self.view.render([])
        self.app.processEvents()
        empty = self.view.findChild(EmptyState)
        self.assertEqual(empty.property("mode"), "chat")
        self.assertFalse(empty.recent_label.isVisible())

    def test_reduce_motion_stops_auto_follow_while_streaming(self):
        self.view.set_reduce_motion(True)
        self.view.render(self.blocks(), "正在整理", follow=True)
        self.app.processEvents()
        self.assertFalse(self.view._stick)
        self.assertTrue(self.view.jump_button.isVisible())

        self.view.set_reduce_motion(False)
        self.view.render(self.blocks(), "正在整理", follow=True)
        self.app.processEvents()
        self.assertTrue(self.view._stick)
        self.assertTrue(self.view.jump_button.isHidden())

    def test_streaming_tail_carries_the_cursor(self):
        self.view.render(self.blocks(), "正在整理")
        self.app.processEvents()
        self.assertTrue(self.view.stream_bubble.isVisible())
        self.assertIn(STREAM_CURSOR, self.view.plain_text())
        self.assertEqual(
            self.view.stream_bubble.body.source_text(),
            "正在整理" + STREAM_CURSOR,
        )

        self.view.render(self.blocks(), "")
        self.app.processEvents()
        self.assertFalse(self.view.stream_bubble.isVisible())
        self.assertNotIn(STREAM_CURSOR, self.view.plain_text())

    def test_identical_signature_reuses_the_same_rows(self):
        self.view.render(self.blocks())
        self.app.processEvents()
        first = [id(widget) for widget in self.view._rows]
        self.view.render(self.blocks())
        self.app.processEvents()
        self.assertEqual(first, [id(widget) for widget in self.view._rows])

        changed = self.blocks()
        changed[2]["state"] = "error"
        self.view.render(changed)
        self.app.processEvents()
        self.assertNotEqual(first, [id(widget) for widget in self.view._rows])
        self.assertEqual(self.view._rows[2].objectName(), "toolCardFailed")

    def test_bubble_width_tracks_content_and_caps_at_viewport(self):
        long_text = "很长的一段陪伴回复" * 30
        self.view.render([
            {"kind": "message", "id": "a1", "role": "assistant", "text": long_text},
            {"kind": "message", "id": "a2", "role": "assistant", "text": "短句。"},
        ])
        self.app.processEvents()
        limit = int(self.view.viewport().width() * 0.82)
        wide, short = self.view._rows
        self.assertLessEqual(wide.width(), limit)
        self.assertGreater(short.width(), 150)
        self.assertLess(short.width(), wide.width())

    def test_label_height_shrinks_when_text_gets_shorter(self):
        bubble = MessageBubble({"kind": "message", "role": "assistant", "text": "短"})
        bubble.setFixedWidth(300)
        bubble.show()
        self.app.processEvents()
        one_line = bubble.body.fontMetrics().lineSpacing()
        self.assertEqual(bubble.body.height(), one_line)
        bubble.set_text("很长的一段回复 " * 40)
        self.app.processEvents()
        self.assertGreater(bubble.body.height(), one_line * 3)
        bubble.set_text("短")
        self.app.processEvents()
        self.assertEqual(bubble.body.height(), one_line)

    def tool_block(self, **overrides):
        block = {
            "kind": "tool",
            "id": "t1",
            "name": "web_search",
            "state": "done",
            "summary": "2 条结果",
            "arguments": {"query": "Python Agent"},
            "sources": [
                {"title": "Example result", "url": "https://example.com/result"},
            ],
            "result_id": "toolres-1",
            "result_chars": 1200,
        }
        block.update(overrides)
        return block

    def test_tool_cards_expand_arguments_sources_and_handles(self):
        self.view.render([self.tool_block()])
        self.app.processEvents()
        card = self.view._rows[0]
        self.assertFalse(card.expanded)
        self.assertFalse(card.extra.isVisible())
        self.assertEqual(card.objectName(), "toolCardDone")
        self.assertIn("web_search", card.title.text())

        card.toggle_button.click()
        self.app.processEvents()
        self.assertTrue(card.expanded)
        self.assertTrue(card.extra.isVisible())
        self.assertIn("Python Agent", card.args_label.text())
        self.assertIn("https://example.com/result", card.sources_label.text())
        self.assertIn("toolres-1", card.handle_label.text())
        self.assertEqual(card.toggle_button.text(), "收起")

        card.toggle_button.click()
        self.app.processEvents()
        self.assertFalse(card.extra.isVisible())
        self.assertEqual(card.toggle_button.text(), "详情")

    def test_expanded_tool_cards_remember_their_state(self):
        self.view.render([self.tool_block()])
        self.app.processEvents()
        self.view._rows[0].toggle_button.click()
        self.app.processEvents()

        self.view.render([])
        self.app.processEvents()
        self.view.render([self.tool_block()])
        self.app.processEvents()
        self.assertTrue(self.view._rows[0].expanded)

    def test_failed_tool_card_shows_the_error_and_code(self):
        self.view.render([
            self.tool_block(state="error", error="网络超时", code="timeout"),
        ])
        self.app.processEvents()
        card = self.view._rows[0]
        self.assertEqual(card.objectName(), "toolCardFailed")
        self.assertIn("网络超时", card.error_label.text())
        self.assertIn("timeout", card.error_label.text())


class RunBarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if RunBar is None:
            raise unittest.SkipTest("PySide6 unavailable")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.bar = RunBar()

    def tearDown(self):
        self.bar.close()

    def test_begin_shows_thinking_with_a_stop_action(self):
        self.bar.begin("run-1")
        self.assertFalse(self.bar.isHidden())
        self.assertEqual(self.bar.stage_label.text(), "思考中")
        self.assertEqual(self.bar.stage_label.property("stage"), "thinking")
        self.assertFalse(self.bar.stop_button.isHidden())
        self.assertEqual(self.bar.elapsed_label.text(), "0 秒")

    def test_events_must_match_the_run_and_arrive_in_order(self):
        self.bar.begin("run-1")
        self.assertFalse(
            self.bar.apply_event({"run_id": "run-2", "sequence": 9,
                                  "stage": "tool"})
        )
        self.assertTrue(
            self.bar.apply_event({"run_id": "run-1", "sequence": 1,
                                  "kind": "tool", "stage": "tool",
                                  "tool_name": "web_search",
                                  "state": "running"})
        )
        self.assertEqual(self.bar.stage_label.text(), "调用工具")
        self.assertIn("web_search", self.bar.detail_label.text())
        self.assertFalse(
            self.bar.apply_event({"run_id": "run-1", "sequence": 1,
                                  "kind": "delta", "stage": "answering"})
        )
        self.assertEqual(self.bar.stage_label.text(), "调用工具")

    def test_completion_settles_and_hides_the_stop_action(self):
        self.bar.begin("run-1")
        self.assertTrue(
            self.bar.apply_event({"run_id": "run-1", "sequence": 1,
                                  "kind": "done", "stage": "completed",
                                  "result": {}})
        )
        self.assertEqual(self.bar.stage_label.text(), "已完成")
        self.assertTrue(self.bar.stop_button.isHidden())
        self.assertTrue(self.bar.hide_timer.isActive())

    def test_failure_keeps_the_message_and_stays_visible(self):
        self.bar.begin("run-1")
        self.bar.apply_event({"run_id": "run-1", "sequence": 1,
                              "kind": "error", "stage": "failed",
                              "message": "网络不可用"})
        self.assertEqual(self.bar.stage_label.text(), "失败")
        self.assertEqual(self.bar.detail_label.text(), "网络不可用")
        self.assertTrue(self.bar.stop_button.isHidden())
        self.assertFalse(self.bar.hide_timer.isActive())

    def test_the_timeline_opens_only_once_two_steps_exist(self):
        self.bar.begin("run-1")
        self.assertTrue(self.bar.steps_toggle.isHidden())
        self.bar.set_steps([
            RunStep(sequence=0, stage="thinking", at="2026-10-30T09:00:00"),
        ])
        self.assertTrue(self.bar.steps_toggle.isHidden())

        self.bar.set_steps([
            RunStep(sequence=0, stage="thinking", at="2026-10-30T09:00:00"),
            RunStep(sequence=3, stage="tool", detail="read_note", tool="read_note",
                    at="2026-10-30T09:03:00"),
        ])
        self.assertFalse(self.bar.steps_toggle.isHidden())
        self.assertEqual(self.bar.steps_toggle.text(), "步骤 2")
        self.assertTrue(self.bar.steps_scroll.isHidden())

        self.bar.steps_toggle.click()
        self.assertFalse(self.bar.steps_scroll.isHidden())
        self.assertEqual(self.bar.steps_toggle.text(), "收起步骤")
        rows = self.bar.steps_label.text().splitlines()
        self.assertEqual(len(rows), 2)
        self.assertIn("调用工具", rows[1])
        self.assertIn("read_note", rows[1])
        self.assertIn("09:03", rows[1])
        self.bar.steps_toggle.click()
        self.assertTrue(self.bar.steps_scroll.isHidden())

    def test_a_new_run_clears_the_previous_timeline(self):
        self.bar.begin("run-1")
        self.bar.set_steps([
            RunStep(sequence=0, stage="thinking", at="2026-10-30T09:00:00"),
            RunStep(sequence=1, stage="answering", at="2026-10-30T09:01:00"),
        ])
        self.bar.steps_toggle.click()
        self.assertFalse(self.bar.steps_scroll.isHidden())

        self.bar.begin("run-2")
        self.assertEqual(self.bar._steps, [])
        self.assertTrue(self.bar.steps_toggle.isHidden())
        self.assertTrue(self.bar.steps_scroll.isHidden())

        self.bar.set_steps([
            RunStep(sequence=0, stage="thinking", at="2026-10-30T09:00:00"),
            RunStep(sequence=1, stage="answering", at="2026-10-30T09:01:00"),
        ])
        self.assertFalse(self.bar.steps_toggle.isHidden())
        self.bar.reset()
        self.assertEqual(self.bar._steps, [])
        self.assertTrue(self.bar.steps_toggle.isHidden())

    def test_waiting_for_approval_is_terminal_for_this_run(self):
        self.bar.begin("run-1")
        self.bar.show_waiting("等待确认写入")
        self.assertEqual(self.bar.stage_label.text(), "等待审批")
        self.assertEqual(self.bar.detail_label.text(), "等待确认写入")
        self.assertTrue(self.bar.stop_button.isHidden())
        self.assertFalse(self.bar.isHidden())

    def test_elapsed_text_counts_minutes(self):
        self.bar.begin("run-1")
        self.bar._started -= 65
        self.assertEqual(self.bar.elapsed_text(), "1 分 05 秒")

    def test_reset_clears_the_run(self):
        self.bar.begin("run-1")
        self.bar.reset()
        self.assertEqual(self.bar.run_id, "")
        self.assertEqual(self.bar.last_sequence, -1)
        self.assertTrue(self.bar.isHidden())


class StubProposal:
    def __init__(self, plan):
        self.user_visible_plan = plan


class StubTask:
    def __init__(self, title, state):
        self.card = type("Card", (), {"title": title})()
        self.state = state


class StubReceipt:
    def __init__(self, status, summary):
        self.status = status
        self.summary = summary


class StubContext:
    used_chars = 1200
    budget_chars = 6000
    compressed = True


class StubRuntime:
    last_context = StubContext()

    def desktop_status(self):
        return {"label": "正在回复", "detail": "生成中"}

    def pending_proposals(self):
        return [StubProposal("写入 notes/meeting.md")]

    def work_status(self):
        return {"state_label": "等待审批", "forward_label": "等待确认"}

    def work_tasks(self, limit=5):
        return [StubTask("发送周报", "PLAN_REVIEW")][:limit]

    def latest_receipts(self, limit=3):
        return [StubReceipt("completed", "已写入 1 个文件")][:limit]

    def proactive_status(self):
        return {
            "enabled": True,
            "activity_label": "专注中",
            "daily_count": 1,
            "daily_limit": 6,
            "unanswered": 0,
            "max_unanswered": 2,
            "next_due": {"display": "20:30", "content": "该休息了"},
            "last_event": {"created_at": "2026-10-27T19:30:00", "status_label": "已回复"},
            "unread": 0,
        }

    def skills_status(self):
        return {"available": [{"name": "会议纪要"}], "issues": []}


class InspectorPanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if InspectorPanel is None:
            raise unittest.SkipTest("PySide6 unavailable")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = InspectorPanel(StubRuntime())
        self.panel.setStyleSheet(stylesheet())
        self.panel.show()
        self.app.processEvents()

    def tearDown(self):
        self.panel.close()

    def test_sections_are_derived_from_runtime_state(self):
        titles = [title for title, _lines in self.panel.snapshot()]
        for expected in (
            "当前状态",
            "待确认提案",
            "工作任务",
            "最近回执",
            "主动陪伴",
            "技能",
            "上下文",
        ):
            self.assertIn(expected, titles)
        sections = dict(self.panel.snapshot())
        self.assertEqual(sections["当前状态"][0], "正在回复")
        self.assertIn("写入 notes/meeting.md", sections["待确认提案"][1])
        self.assertIn("发送周报", sections["工作任务"][2])
        self.assertIn("已写入 1 个文件", sections["最近回执"][0])
        self.assertIn("专注中", sections["主动陪伴"][1])
        self.assertIn("会议纪要", sections["技能"][1])
        self.assertIn("1200", sections["上下文"][0])
        self.assertIn("已压缩", sections["上下文"][1])

    def test_missing_runtime_sources_do_not_break_the_panel(self):
        class Broken:
            def desktop_status(self):
                raise RuntimeError("数据库不可用")

            def pending_proposals(self):
                raise RuntimeError("数据库不可用")

            def work_status(self):
                raise RuntimeError("数据库不可用")

            def work_tasks(self, limit=5):
                raise RuntimeError("数据库不可用")

            def latest_receipts(self, limit=3):
                raise RuntimeError("数据库不可用")

            def proactive_status(self):
                raise RuntimeError("数据库不可用")

            def skills_status(self):
                raise RuntimeError("技能目录不可用")

        panel = InspectorPanel(Broken())
        sections = dict(panel.build_sections())
        self.assertEqual(list(sections), ["待确认提案", "最近回执"])
        self.assertEqual(sections["待确认提案"], ["暂时读不到"])
        self.assertEqual(sections["最近回执"], ["暂时读不到"])

    def test_refresh_only_rebuilds_on_change(self):
        self.panel.refresh()
        self.app.processEvents()
        built = self.panel._signature
        self.panel.refresh()
        self.assertIs(built, self.panel._signature)
        self.panel.provider.last_context = None
        self.panel.refresh()
        self.assertNotIn("上下文", [title for title, _lines in self.panel.snapshot()])

    def test_work_task_state_uses_the_chinese_label(self):
        lines = dict(self.panel.snapshot())["工作任务"]
        self.assertEqual(lines[0], "等待审批")
        self.assertEqual(lines[1], "转发：等待确认")
        self.assertEqual(lines[2], "发送周报｜等待审批")

    def test_tabs_cover_the_context_drawer(self):
        labels = [
            self.panel.tabs.tabText(index)
            for index in range(self.panel.tabs.count())
        ]
        self.assertEqual(labels, ["执行", "文件", "来源", "上下文", "任务"])
        self.assertEqual(self.panel.active_tab(), "execute")
        self.assertTrue(self.panel.focus_tab("tasks"))
        self.assertEqual(self.panel.active_tab(), "tasks")
        self.assertTrue(self.panel.focus_tab("sources"))
        self.assertEqual(self.panel.active_tab(), "sources")
        self.assertFalse(self.panel.focus_tab("missing"))
        self.assertEqual(self.panel.active_tab(), "sources")

    def test_file_and_source_tabs_follow_runtime_state(self):
        runtime = StubRuntime()
        runtime.last_tool_sources = [
            {"title": "Example result", "url": "https://example.com/result"},
        ]
        panel = InspectorPanel(runtime)
        try:
            sections = dict(panel.build_sections())
            self.assertIn("文件", sections)
            self.assertIn("来源", sections)
            self.assertIn("Example result", sections["来源"][0])
            self.assertIn("https://example.com/result", sections["来源"][1])
        finally:
            panel.close()


class ApprovalCardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if ApprovalCard is None:
            raise unittest.SkipTest("PySide6 unavailable")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.card = ApprovalCard()
        self.card.setStyleSheet(stylesheet())
        self.card.show()
        self.app.processEvents()

    def tearDown(self):
        self.card.close()

    def view(self, **overrides):
        payload = dict(
            action_id="act-1",
            session_id="main",
            mode="work",
            status="awaiting_confirmation",
            risk="fs-write",
            target_summary="C:\\workspace\\notes\\demo.md",
            plan="在授权目录中写入一篇 Markdown 笔记。",
            confirmation_digest="a" * 64,
            expires_at="2026-10-29T12:00:00+00:00",
            reversible=True,
            created_at="2026-10-29T11:00:00+00:00",
            tool="write_note",
            workspace="C:\\workspace",
            expired=False,
            preview=ApprovalPreview(
                change="update",
                workspace="C:\\workspace",
                relative_path="notes/demo.md",
                diff=(("del", "旧内容"), ("add", "新内容")),
                added=1,
                removed=1,
            ),
        )
        payload.update(overrides)
        return ApprovalView(**payload)

    def test_card_shows_target_mode_and_diff_preview(self):
        self.card.show_view(self.view())
        self.app.processEvents()

        self.assertEqual(self.card.target_label.text(), "C:\\workspace\\notes\\demo.md")
        self.assertEqual(self.card.workspace_label.text(), "C:\\workspace")
        self.assertIn("写入文件", self.card.risk_label.text())
        self.assertIn("更新", self.card.summary_label.text())
        self.assertIn("+1 / -1", self.card.summary_label.text())
        kinds = [
            self.card.diff_layout.itemAt(index).widget().objectName()
            for index in range(self.card.diff_layout.count())
        ]
        self.assertIn("diffDel", kinds)
        self.assertIn("diffAdd", kinds)
        self.assertTrue(self.card.confirm_button.isEnabled())
        self.assertFalse(self.card.terminal)

    def test_expired_view_disables_confirmation(self):
        self.card.show_view(self.view(expired=True))
        self.app.processEvents()

        self.assertEqual(self.card.status_pill.text(), "已过期")
        self.assertFalse(self.card.confirm_button.isEnabled())
        self.assertIn("过期", self.card.error_label.text())
        self.assertTrue(self.card.error_label.isVisible())

    def test_execution_result_and_undo_stay_in_the_same_card(self):
        receipt = type(
            "Receipt",
            (),
            {
                "status": "completed",
                "summary": "已写入 1 个文件",
                "undoable": True,
                "undo_status": "available",
            },
        )()
        self.card.show_view(self.view())
        self.card.show_result(receipt, "已写入笔记。")
        self.app.processEvents()

        self.assertTrue(self.card.terminal)
        self.assertIs(self.card.receipt, receipt)
        self.assertFalse(self.card.confirm_button.isVisible())
        self.assertTrue(self.card.result_label.isVisible())
        self.assertTrue(self.card.undo_button.isVisible())

        self.card.show_undone("已撤销，文件恢复原样。")
        self.app.processEvents()

        self.assertEqual(self.card.status_pill.text(), "已撤销")
        self.assertFalse(self.card.undo_button.isVisible())
        self.assertIn("撤销", self.card.result_label.text())


class SessionRowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SessionRow is None:
            return
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        if SessionRow is None:
            self.skipTest("PySide6 unavailable")

    def summary(self, **overrides):
        now = datetime.now(timezone.utc).isoformat()
        data = {
            "id": "chat_a",
            "mode": "chat",
            "title": "",
            "topic": "帮我整理书架",
            "pinned_at": "",
            "unread": 0,
            "message_count": 3,
            "last_content": "好的，先按主题分组。",
            "last_at": now,
            "updated_at": now,
        }
        data.update(overrides)
        return data

    def test_relative_time_and_group_labels(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(_relative_time(now.isoformat()), "刚刚")
        self.assertEqual(
            _relative_time((now - timedelta(minutes=9)).isoformat()),
            "9 分钟前",
        )
        self.assertEqual(
            _relative_time((now - timedelta(hours=3)).isoformat()),
            "3 小时前",
        )
        self.assertEqual(
            _relative_time((now - timedelta(days=1)).isoformat()),
            "昨天",
        )
        self.assertEqual(
            _session_group((now - timedelta(days=1)).isoformat()),
            "昨天",
        )
        self.assertEqual(
            _session_group((now - timedelta(days=30)).isoformat()),
            "更早",
        )
        self.assertEqual(_session_group(""), "更早")

    def test_row_shows_custom_title_pin_unread_and_accessible_name(self):
        now = datetime.now(timezone.utc).isoformat()
        row = SessionRow(
            self.summary(
                title="书架计划",
                pinned_at=now,
                unread=1,
            ),
            active=True,
        )
        self.assertEqual(row.title.full_text(), "书架计划")
        self.assertIn("置顶", row.meta.text())
        self.assertIn("未读", row.meta.text())
        self.assertIn("刚刚", row.meta.text())
        self.assertIn("书架计划", row.accessibleName())
        self.assertIn("未读", row.accessibleName())
        self.assertIn("当前", row.accessibleName())
        self.assertIn("当前", row.meta.text())
        self.assertIn("管理会话", row.toolTip())
        self.assertTrue(row.focusPolicy() & Qt.StrongFocus)

    def test_menu_actions_and_keyboard_activation_emit_signals(self):
        row = SessionRow(self.summary())
        clicked = []
        pinned = []
        unread = []
        renamed = []
        deleted = []
        row.clicked.connect(clicked.append)
        row.pin_toggled.connect(lambda sid, value: pinned.append((sid, value)))
        row.unread_toggled.connect(
            lambda sid, value: unread.append((sid, value))
        )
        row.rename_requested.connect(renamed.append)
        row.delete_requested.connect(deleted.append)

        QTest.keyClick(row, Qt.Key_Return)
        QTest.keyClick(row, Qt.Key_Space)
        self.assertEqual(clicked, ["chat_a", "chat_a"])

        menu = row._menu()
        labels = [action.text() for action in menu.actions() if action.text()]
        self.assertEqual(
            labels,
            ["置顶会话", "重命名…", "标记未读", "删除会话…"],
        )
        for action in menu.actions():
            if action.text() == "置顶会话":
                action.trigger()
            elif action.text() == "重命名…":
                action.trigger()
            elif action.text() == "标记未读":
                action.trigger()
            elif action.text() == "删除会话…":
                action.trigger()
        self.assertEqual(pinned, [("chat_a", True)])
        self.assertEqual(unread, [("chat_a", True)])
        self.assertEqual(renamed, ["chat_a"])
        self.assertEqual(deleted, ["chat_a"])

    def test_active_pinned_row_offers_reverse_actions(self):
        now = datetime.now(timezone.utc).isoformat()
        row = SessionRow(
            self.summary(pinned_at=now, unread=1),
            active=True,
        )
        menu = row._menu()
        labels = [action.text() for action in menu.actions() if action.text()]
        self.assertEqual(
            labels,
            ["取消置顶", "重命名…", "标记已读", "删除会话…"],
        )


class UiStateChromeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = UiStateStore(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_theme_and_panel_flags_round_trip(self):
        self.assertEqual(self.store.theme(), DEFAULT_THEME)
        self.assertTrue(self.store.inspector_visible())
        self.store.set_theme("pearl-white")
        self.store.set_inspector_visible(False)
        self.store.set(rail_visible=False)
        self.assertEqual(self.store.theme(), "pearl-white")
        self.assertFalse(self.store.inspector_visible())
        self.assertFalse(self.store.load()["rail_visible"])

    def test_unknown_theme_falls_back(self):
        self.store.set(theme="vaporwave")
        self.assertEqual(self.store.theme(), DEFAULT_THEME)

    def test_active_session_round_trip(self):
        self.assertEqual(self.store.active_session(), "")
        self.store.set_active_session("chat_abc")
        self.assertEqual(self.store.active_session(), "chat_abc")
        self.store.set(active_session="   ")
        self.assertEqual(self.store.active_session(), "")
        self.store.set(active_session=42)
        self.assertEqual(self.store.active_session(), "")

    def test_window_geometry_validates_all_four_numbers(self):
        self.assertIsNone(self.store.window_geometry())
        self.store.set_window_geometry(30, 40, 1000, 700)
        self.assertEqual(self.store.window_geometry(), (30, 40, 1000, 700))
        self.store.set(window_geometry=[1, 2, 3])
        self.assertIsNone(self.store.window_geometry())
        self.store.set(window_geometry=["a", 2, 3, 4])
        self.assertIsNone(self.store.window_geometry())
        self.store.set(window_geometry=[1, 2, 100, 100])
        self.assertIsNone(self.store.window_geometry())

    def test_inspector_width_round_trips_and_clamps(self):
        self.assertEqual(self.store.inspector_width(), 268)
        self.store.set_inspector_width(312)
        self.assertEqual(self.store.inspector_width(), 312)
        self.store.set_inspector_width(9999)
        self.assertEqual(self.store.inspector_width(), 420)
        self.store.set_inspector_width(10)
        self.assertEqual(self.store.inspector_width(), 220)
        self.store.set(inspector_width="wide")
        self.assertEqual(self.store.inspector_width(), 268)

    def test_clamp_position_keeps_the_window_on_the_nearest_screen(self):
        screens = [(0, 0, 1920, 1080), (1920, 0, 1280, 1024)]
        self.assertEqual(clamp_position(50, 60, 1000, 700, screens), (50, 60))
        self.assertEqual(clamp_position(3000, 5000, 1000, 700, screens), (2200, 324))
        self.assertEqual(clamp_position(-500, -500, 1000, 700, screens), (0, 0))
        self.assertEqual(clamp_position(10, 10, 100, 100, []), (10, 10))


if __name__ == "__main__":
    unittest.main()
