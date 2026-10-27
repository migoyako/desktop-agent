"""M4 main tests: proactive scheduling, persistence and anti-repeat."""

from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path

from agent_framework.companion.models import (
    AgentMode,
    PROACTIVE_DELIVERED,
    PROACTIVE_MISSED,
)
from agent_framework.companion.proactive import (
    ActivitySnapshot,
    ProactiveEngine,
    ProactiveService,
    ProactiveSource,
    ReminderService,
    compose_message,
    content_hash,
    duplicate_score,
    is_duplicate,
    url_hash,
)
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.llm import OfflineLLMClient


class FixedProbe:
    def __init__(self, snapshot=None):
        self.value = snapshot or ActivitySnapshot(idle_seconds=0.0)

    def snapshot(self):
        return self.value


class ProactiveServiceBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name) / "data"
        self.store = CompanionStore(self.data / "companion.db")
        self.store.ensure_session("main", AgentMode.CHAT)
        self.now = datetime.now().astimezone()
        self.probe = FixedProbe()
        self.reminders = ReminderService(self.store)
        self.engine = ProactiveEngine(self.store, self.reminders)
        self.service = ProactiveService(
            self.store,
            self.engine,
            self.reminders,
            self.probe,
            session_id="main",
        )
        self.service.update_settings(
            quiet_start=0,
            quiet_end=0,
            daily_limit=10,
            min_interval_hours=0.0,
            max_unanswered=10,
        )

    def tearDown(self):
        self.temp.cleanup()

    def deliver(self, content, due=None):
        reminder = self.reminders.create(
            "main",
            content,
            due or self.now - timedelta(minutes=1),
        )
        return reminder, self.service.tick(now=self.now)


class ProactiveSettingsTest(ProactiveServiceBase):
    def test_settings_persist_across_instances(self):
        self.service.update_settings(
            daily_limit=4,
            min_interval_hours=1.5,
            max_unanswered=3,
            include_memory=True,
        )

        other = ProactiveService(
            self.store,
            self.engine,
            self.reminders,
            self.probe,
        )
        settings = other.status(now=self.now)["settings"]
        self.assertEqual(
            settings,
            {
                "quiet_start": 0,
                "quiet_end": 0,
                "daily_limit": 4,
                "min_interval_hours": 1.5,
                "max_unanswered": 3,
                "include_memory": True,
            },
        )

    def test_invalid_settings_are_rejected(self):
        for fields in (
            {"daily_limit": 21},
            {"min_interval_hours": -1},
            {"max_unanswered": 21},
            {"quiet_start": 24},
            {"quiet_end": -1},
            {"unknown_field": 1},
        ):
            with self.assertRaises(ValueError, msg=str(fields)):
                self.service.update_settings(**fields)


class ProactiveDeliveryTest(ProactiveServiceBase):
    def test_due_reminder_is_delivered_and_traced(self):
        reminder, result = self.deliver(
            "喝水",
            due=self.now - timedelta(minutes=10),
        )

        self.assertTrue(result["spoke"])
        self.assertEqual(result["reason"], "ok")
        self.assertIn("提醒你一下", result["message"])
        self.assertIn("喝水", result["message"])
        self.assertIn("到点", result["trigger_reason"])
        self.assertEqual(result["source"]["source_type"], "reminder")
        self.assertEqual(
            result["source"]["source_id"],
            reminder.reminder_id,
        )

        history = self.store.list_messages("main", limit=10)
        self.assertEqual(history[-1]["role"], "assistant")
        self.assertEqual(history[-1]["content"], result["message"])
        self.assertEqual(
            self.store.get_reminder(reminder.reminder_id).status,
            "done",
        )

        events = self.service.events()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.event_id, result["event"]["event_id"])
        self.assertEqual(event.status, PROACTIVE_DELIVERED)
        self.assertEqual(event.trigger_reason, result["trigger_reason"])
        self.assertEqual(event.message_id, result["message_id"])
        self.assertIsNone(event.read_at)
        self.assertTrue(event.content_hash)
        self.assertEqual(result["unread"], 1)

        state = self.store.get_proactive_state("main")
        self.assertEqual(state.daily_count, 1)
        self.assertEqual(state.daily_date, self.now.date().isoformat())
        self.assertEqual(state.unanswered_count, 1)
        self.assertEqual(state.last_event_id, event.event_id)
        self.assertEqual(state.last_spoken_at, event.created_at)
        self.assertEqual(self.service.status(now=self.now)["unread"], 1)

    def test_reminder_just_before_grace_is_delivered(self):
        _reminder, result = self.deliver(
            "站起来",
            due=self.now - timedelta(minutes=29),
        )

        self.assertTrue(result["spoke"])
        self.assertEqual(result["missed"], [])

    def test_missed_reminder_is_unread_and_never_resent(self):
        reminder = self.reminders.create(
            "main",
            "倒垃圾",
            self.now - timedelta(hours=1),
        )

        first = self.service.tick(now=self.now)

        self.assertFalse(first["spoke"])
        self.assertEqual(len(first["missed"]), 1)
        missed = first["missed"][0]
        self.assertEqual(missed["status"], PROACTIVE_MISSED)
        self.assertEqual(missed["reason"], "missed_window")
        self.assertIn("错过了送达窗口", missed["trigger_reason"])
        self.assertIsNone(missed["read_at"])
        self.assertEqual(
            self.store.get_reminder(reminder.reminder_id).status,
            "dismissed",
        )
        self.assertEqual(self.store.list_messages("main", limit=10), [])
        self.assertEqual(self.service.status(now=self.now)["unread"], 1)

        second = self.service.tick(now=self.now + timedelta(minutes=5))

        self.assertFalse(second["spoke"])
        self.assertEqual(second["missed"], [])
        self.assertEqual(len(self.service.events()), 1)
        self.assertEqual(self.service.status(now=self.now)["unread"], 1)

    def test_disabled_service_does_not_consume_reminders(self):
        self.service.update_settings(enabled=False)
        reminder = self.reminders.create(
            "main",
            "喝水",
            self.now - timedelta(minutes=1),
        )

        result = self.service.tick(now=self.now)

        self.assertFalse(result["spoke"])
        self.assertEqual(result["reason"], "disabled")
        self.assertEqual(result["missed"], [])
        self.assertEqual(
            self.store.get_reminder(reminder.reminder_id).status,
            "pending",
        )
        self.assertEqual(self.service.events(), [])
        self.assertEqual(
            self.service.preview(now=self.now)["reason"],
            "disabled",
        )

    def test_two_ticks_deliver_two_events_newest_first(self):
        first = self.reminders.create(
            "main",
            "喝水",
            self.now - timedelta(minutes=2),
        )
        second = self.reminders.create("main", "站起来", self.now)

        self.assertTrue(self.service.tick(now=self.now)["spoke"])
        self.assertTrue(
            self.service.tick(now=self.now + timedelta(minutes=1))["spoke"]
        )

        events = self.service.events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].source_id, second.reminder_id)
        self.assertEqual(events[1].source_id, first.reminder_id)
        self.assertEqual(len(self.service.unread_events()), 2)

        self.assertEqual(self.service.mark_read(events[1].event_id), 1)
        self.assertEqual(self.service.status(now=self.now)["unread"], 1)
        remaining = self.service.unread_events()
        self.assertEqual(
            [event.event_id for event in remaining],
            [events[0].event_id],
        )


class ProactiveGateBlockTest(ProactiveServiceBase):
    def setUp(self):
        super().setUp()
        self.reminder = self.reminders.create(
            "main",
            "喝水",
            self.now - timedelta(minutes=1),
        )

    def assert_blocked(self, reason):
        result = self.service.tick(now=self.now)
        self.assertFalse(result["spoke"], reason)
        self.assertEqual(result["reason"], reason)
        self.assertEqual(
            self.store.get_reminder(self.reminder.reminder_id).status,
            "pending",
        )

    def reset(self):
        self.probe.value = ActivitySnapshot(idle_seconds=0.0)
        self.store.set_session_mode("main", AgentMode.CHAT)
        self.service.update_settings(
            quiet_start=0,
            quiet_end=0,
            daily_limit=10,
            min_interval_hours=0.0,
            max_unanswered=10,
        )
        self.store.update_proactive_state(
            "main",
            last_spoken_at=None,
            unanswered_count=0,
        )

    def test_privacy_and_activity_gates(self):
        self.probe.value = ActivitySnapshot(
            idle_seconds=0.0,
            foreground_title="无痕浏览 - Chrome",
        )
        self.assert_blocked("privacy")

        self.probe.value = ActivitySnapshot(
            idle_seconds=None,
            available=False,
        )
        self.assert_blocked("activity_unknown")

        self.probe.value = ActivitySnapshot(idle_seconds=400)
        self.assert_blocked("activity_idle")

        self.probe.value = ActivitySnapshot(
            idle_seconds=0.0,
            process_name="code.exe",
        )
        self.assert_blocked("activity_focus")

        self.reset()
        self.assertTrue(self.service.tick(now=self.now)["spoke"])

    def test_quiet_hours_and_daily_limit(self):
        self.service.update_settings(
            quiet_start=self.now.hour,
            quiet_end=(self.now.hour + 1) % 24,
        )
        self.assert_blocked("quiet_hours")

        self.reset()
        self.service.update_settings(daily_limit=0)
        self.assert_blocked("daily_limit")

    def test_min_interval_and_unanswered(self):
        self.store.update_proactive_state(
            "main",
            last_spoken_at=(
                self.now - timedelta(hours=1)
            ).astimezone(timezone.utc).isoformat(),
        )
        self.service.update_settings(min_interval_hours=3.0)
        self.assert_blocked("min_interval")

        self.reset()
        self.store.update_proactive_state(
            "main",
            unanswered_count=2,
        )
        self.service.update_settings(max_unanswered=2)
        self.assert_blocked("unanswered")

    def test_quiet_mode_blocks(self):
        self.store.set_session_mode("main", AgentMode.QUIET)
        self.assert_blocked("quiet_mode")


class ProactiveDuplicateTest(ProactiveServiceBase):
    def add_event(self, content, hours_ago=1.0, status=PROACTIVE_DELIVERED):
        created = (
            self.now - timedelta(hours=hours_ago)
        ).astimezone(timezone.utc).isoformat()
        return self.store.add_proactive_event(
            session_id="main",
            source_type="unfinished_topic",
            source_id="msg_old",
            trigger_reason="上次提问还没有回复",
            content=content,
            status=status,
            reason="ok",
            content_hash=content_hash(content),
            url_hash=url_hash(content),
            created_at=created,
        )

    def test_filter_skips_duplicate_and_keeps_next_source(self):
        duplicate = ProactiveSource(
            source_type="unfinished_topic",
            source_id="msg_1",
            content="明天天气怎么样？",
            trigger_reason="上次提问还没有回复",
        )
        fresh = ProactiveSource(
            source_type="unfinished_topic",
            source_id="msg_2",
            content="帮我收尾一下这个方案。",
            trigger_reason="上次提问还没有回复",
        )
        self.add_event(duplicate.content)

        kept = self.engine.filter_duplicates(
            [duplicate, fresh],
            "main",
            self.now,
        )

        self.assertEqual([item.source_id for item in kept], ["msg_2"])

    def test_preview_reports_duplicate_when_every_source_repeats(self):
        content = "明天天气怎么样？"
        self.store.add_message("main", "user", content, AgentMode.CHAT)
        self.add_event(content)

        preview = self.service.preview(now=self.now)

        self.assertFalse(preview["speak"])
        self.assertEqual(preview["reason"], "duplicate")
        self.assertIsNone(preview["source"])
        self.assertEqual(preview["sources"], [])

    def test_missed_events_also_suppress_repeats(self):
        content = "明天天气怎么样？"
        self.store.add_message("main", "user", content, AgentMode.CHAT)
        self.add_event(content, status=PROACTIVE_MISSED)

        preview = self.service.preview(now=self.now)

        self.assertFalse(preview["speak"])
        self.assertEqual(preview["reason"], "duplicate")

    def test_reminder_text_may_repeat_on_request(self):
        _first, result = self.deliver("喝水")
        self.assertTrue(result["spoke"])

        self.reminders.create(
            "main",
            "喝水",
            self.now - timedelta(minutes=1),
        )
        second = self.service.tick(now=self.now + timedelta(minutes=1))

        self.assertTrue(second["spoke"])
        self.assertEqual(len(self.service.events()), 2)


class ProactiveStateTest(ProactiveServiceBase):
    def test_user_message_resets_unanswered_and_marks_read(self):
        _reminder, result = self.deliver("喝水")
        self.assertTrue(result["spoke"])
        self.assertEqual(
            self.store.get_proactive_state("main").unanswered_count,
            1,
        )
        self.assertEqual(self.service.status(now=self.now)["unread"], 1)

        self.assertEqual(self.service.on_user_message(), 1)

        state = self.store.get_proactive_state("main")
        self.assertEqual(state.unanswered_count, 0)
        self.assertEqual(self.service.status(now=self.now)["unread"], 0)
        self.assertIsNotNone(self.service.events()[0].read_at)

    def test_daily_count_resets_when_the_date_changes(self):
        self.store.update_proactive_state(
            "main",
            daily_count=2,
            daily_date="2000-01-01",
        )

        self.assertEqual(
            self.service.status(now=self.now)["daily_count"],
            0,
        )

        _reminder, result = self.deliver("喝水")
        self.assertTrue(result["spoke"])
        state = self.store.get_proactive_state("main")
        self.assertEqual(state.daily_count, 1)
        self.assertEqual(state.daily_date, self.now.date().isoformat())

    def test_memory_source_is_opt_in(self):
        memory_id = self.store.add_memory(
            session_id="main",
            kind="preference",
            content="我喜欢黑咖啡",
            source_message_id=None,
            confidence=0.9,
            importance=0.7,
            status="confirmed",
        )
        recall_id = self.store.log_recall(
            "main",
            "test-query",
            [memory_id],
            [0.9],
            3.0,
            source="test",
        )
        self.store.mark_recall_used(recall_id, True)

        disabled = self.service.preview(now=self.now)
        self.assertFalse(disabled["speak"])
        self.assertEqual(disabled["reason"], "no_source")

        self.service.update_settings(include_memory=True)
        enabled = self.service.preview(now=self.now)
        self.assertTrue(enabled["speak"])
        self.assertEqual(enabled["source"]["source_type"], "memory")
        self.assertIn("黑咖啡", enabled["source"]["content"])

        result = self.service.tick(now=self.now)
        self.assertTrue(result["spoke"])
        self.assertIn("我想到你之前说过", result["message"])
        event = self.service.events()[0]
        self.assertEqual(event.source_type, "memory")
        self.assertEqual(event.source_id, memory_id)


class RepeatUnitTest(unittest.TestCase):
    def event(self, source_type, content, hours_ago, status="delivered"):
        created = (
            datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        ).isoformat()
        return {
            "source_type": source_type,
            "status": status,
            "content": content,
            "content_hash": content_hash(content),
            "url_hash": url_hash(content),
            "created_at": created,
        }

    def test_content_hash_normalizes_case_and_punctuation(self):
        self.assertEqual(
            content_hash(" Hello, World! "),
            content_hash("helloworld"),
        )
        self.assertNotEqual(content_hash("喝水"), content_hash("站起来"))

    def test_url_hash_matches_the_same_link_inside_different_sentences(self):
        first = url_hash("看这个 https://example.com/a?b=1。")
        second = url_hash("https://example.com/a?b=1")
        other = url_hash("https://example.com/b")

        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertEqual(url_hash("没有链接"), "")

    def test_reminders_are_exempt_from_anti_repeat(self):
        events = [self.event("reminder", "提醒：喝水", 0)]

        self.assertEqual(
            duplicate_score("reminder", "提醒：喝水", events),
            0.0,
        )
        self.assertFalse(
            is_duplicate(
                ProactiveSource(
                    "reminder",
                    "rem_1",
                    "提醒：喝水",
                    "提醒到点",
                ),
                events,
            )
        )

    def test_topic_and_memory_half_lives(self):
        topic = self.event("unfinished_topic", "明天天气怎么样？", 11)
        stale_topic = self.event("unfinished_topic", "明天天气怎么样？", 25)
        memory = self.event("memory", "我喜欢黑咖啡", 60)
        stale_memory = self.event("memory", "我喜欢黑咖啡", 100)

        self.assertGreater(
            duplicate_score("unfinished_topic", "明天天气怎么样？", [topic]),
            0.5,
        )
        self.assertLess(
            duplicate_score(
                "unfinished_topic",
                "明天天气怎么样？",
                [stale_topic],
            ),
            0.5,
        )
        self.assertGreater(
            duplicate_score("memory", "我喜欢黑咖啡", [memory]),
            0.5,
        )
        self.assertLess(
            duplicate_score("memory", "我喜欢黑咖啡", [stale_memory]),
            0.5,
        )

    def test_url_match_suppresses_the_same_link(self):
        events = [
            self.event(
                "unfinished_topic",
                "https://example.com/article?id=1",
                1,
            )
        ]

        self.assertTrue(
            is_duplicate(
                ProactiveSource(
                    "unfinished_topic",
                    "msg_1",
                    "帮我看看 https://example.com/article?id=1 这篇文章",
                    "上次提问还没有回复",
                ),
                events,
            )
        )


class ComposeUnitTest(unittest.TestCase):
    def source(self, source_type, content):
        return ProactiveSource(source_type, "src", content, "reason")

    def test_reminder_template_strips_the_prefix(self):
        self.assertEqual(
            compose_message(self.source("reminder", "提醒：喝水")),
            "提醒你一下：喝水。",
        )
        self.assertEqual(
            compose_message(self.source("reminder", "提醒：喝水。")),
            "提醒你一下：喝水。",
        )

    def test_topic_and_memory_templates(self):
        topic = compose_message(
            self.source("unfinished_topic", "明天天气怎么样？")
        )
        self.assertIn("「明天天气怎么样？」", topic)
        self.assertIn("需要我继续吗", topic)

        memory = compose_message(self.source("memory", "我喜欢黑咖啡"))
        self.assertIn("我想到你之前说过", memory)
        self.assertIn("黑咖啡", memory)

    def test_fallback_truncates_and_handles_empty_input(self):
        fallback = compose_message(self.source("other", "测试"))
        self.assertTrue(fallback.startswith("想和你说一句"))

        long_message = compose_message(
            self.source("reminder", "啊" * 500)
        )
        self.assertLessEqual(len(long_message), 300)
        self.assertTrue(long_message.endswith("…"))

        self.assertEqual(
            compose_message(self.source("reminder", "提醒：   ")),
            "",
        )


class RuntimeProactiveApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name) / "data"
        self.store = CompanionStore(self.data / "companion.db")
        self.runtime = CompanionRuntime(
            self.store,
            OfflineLLMClient(),
            SearchService(),
            self.data,
            offline=True,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_runtime_round_trip(self):
        status = self.runtime.proactive_status()
        self.assertIn("settings", status)
        self.assertEqual(status["settings"]["daily_limit"], 2)

        paused = self.runtime.set_proactive_enabled(False)
        self.assertFalse(paused["enabled"])

        updated = self.runtime.update_proactive_settings(
            enabled=True,
            quiet_start=0,
            quiet_end=0,
            daily_limit=5,
        )
        self.assertTrue(updated["enabled"])
        self.assertEqual(updated["settings"]["daily_limit"], 5)
        self.assertEqual(updated["settings"]["quiet_start"], 0)

        self.assertIsNone(self.runtime.next_reminder())
        reminder = self.runtime.reminder_service.create(
            "main",
            "喝水",
            datetime.now().astimezone() + timedelta(minutes=5),
        )
        self.assertEqual(
            self.runtime.next_reminder().reminder_id,
            reminder.reminder_id,
        )

        result = self.runtime.proactive_tick()
        self.assertIn("spoke", result)
        self.assertIn("missed", result)
        self.assertIsInstance(self.runtime.proactive_events(), list)
        self.assertIsInstance(self.runtime.unread_proactive_events(), list)
        self.assertEqual(self.runtime.mark_proactive_read(), 0)
        self.assertIn("reason", self.runtime.proactive_preview())

    def test_handle_message_resets_unanswered_and_reads_events(self):
        self.runtime.update_proactive_settings(quiet_start=0, quiet_end=0)
        self.store.update_proactive_state("main", unanswered_count=2)
        self.store.add_proactive_event(
            session_id="main",
            source_type="reminder",
            source_id="rem_old",
            trigger_reason="提醒到点",
            content="提醒：喝水",
            status=PROACTIVE_MISSED,
            reason="missed_window",
        )

        self.runtime.handle_message("你好", AgentMode.CHAT)

        state = self.store.get_proactive_state("main")
        self.assertEqual(state.unanswered_count, 0)
        self.assertEqual(
            self.store.count_unread_proactive_events("main"),
            0,
        )


if __name__ == "__main__":
    unittest.main()
