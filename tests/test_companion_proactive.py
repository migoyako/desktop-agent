"""M4.0 tests for reminders, unfinished topics, activity and gating."""

from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path

from agent_framework.companion.models import AgentMode
from agent_framework.companion.proactive import (
    ACTIVITY_ACTIVE,
    ACTIVITY_AWAY,
    ACTIVITY_FOCUS,
    ACTIVITY_GAME,
    ACTIVITY_IDLE,
    ACTIVITY_PRIVATE,
    ACTIVITY_UNKNOWN,
    ActivitySnapshot,
    GateState,
    ProactiveEngine,
    ReminderService,
    classify_activity,
    derive_unfinished_topics,
    evaluate_gate,
    parse_reminder_request,
    resolve_when,
)
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import SCHEMA_VERSION, CompanionStore
from agent_framework.llm import LLMResponse, OfflineLLMClient


NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


class CountingLLM:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        return LLMResponse(content="模型回复")


class ProactiveBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.store = CompanionStore(self.data / "companion.db")

    def tearDown(self):
        self.temp.cleanup()

    def runtime(self, session_id="main", llm=None):
        return CompanionRuntime(
            self.store,
            llm or OfflineLLMClient(),
            SearchService(),
            self.data,
            session_id=session_id,
            offline=llm is None,
        )


class ReminderServiceTest(ProactiveBase):
    def setUp(self):
        super().setUp()
        self.service = ReminderService(self.store)
        self.store.ensure_session("main", AgentMode.CHAT)

    def test_due_query_respects_time_and_status(self):
        reminder = self.service.create(
            "main",
            "喝水",
            NOW + timedelta(minutes=10),
        )

        self.assertEqual(self.service.due("main", now=NOW), [])
        self.assertEqual(
            self.service.next_due("main").reminder_id,
            reminder.reminder_id,
        )

        due = self.service.due(
            "main",
            now=NOW + timedelta(minutes=11),
        )
        self.assertEqual([item.content for item in due], ["喝水"])

        done = self.service.complete(reminder.reminder_id)
        self.assertEqual(done.status, "done")
        self.assertIsNotNone(done.acknowledged_at)
        self.assertEqual(
            self.service.due("main", now=NOW + timedelta(hours=1)),
            [],
        )
        self.assertIsNone(self.service.next_due("main"))
        self.assertIsNone(self.service.complete(reminder.reminder_id))

    def test_dismiss_hides_reminder_without_deleting_it(self):
        reminder = self.service.create("main", "倒垃圾", NOW)

        dismissed = self.service.dismiss(reminder.reminder_id)

        self.assertEqual(dismissed.status, "dismissed")
        self.assertEqual(self.service.pending("main"), [])
        self.assertEqual(
            [item.status for item in self.store.list_reminders("main")],
            ["dismissed"],
        )

    def test_parse_relative_reminders(self):
        cases = {
            "提醒我 10 分钟后喝水": ("喝水", 600),
            "2小时后提醒我开会": ("开会", 7200),
            "提醒我 三天后交材料。": ("交材料", 3 * 86400),
            "提醒我 十分钟后站起来": ("站起来", 600),
            "30秒后提醒我喝水": ("喝水", 30),
            "半小时后提醒我喝水": ("喝水", 1800),
            "十五分钟后叫我起身": ("起身", 900),
            "提醒我两小时后看烤箱": ("看烤箱", 7200),
        }
        for text, (content, seconds) in cases.items():
            draft = parse_reminder_request(text, now=NOW)
            self.assertIsNotNone(draft, text)
            self.assertEqual(draft.content, content)
            self.assertEqual(
                draft.due_at,
                NOW + timedelta(seconds=seconds),
            )

    def test_parse_clock_reminders_use_local_wall_clock(self):
        local_now = datetime(2026, 10, 27, 12, 0).astimezone()
        cases = {
            "晚上9点提醒我写日记": ("写日记", local_now.replace(hour=21)),
            "提醒我明天早上7点开周会": ("开周会", (local_now + timedelta(days=1)).replace(hour=7)),
            "下午2点15分提醒我打电话": ("打电话", local_now.replace(hour=14, minute=15)),
            "18:30叫我关火": ("关火", local_now.replace(hour=18, minute=30)),
            "上午9点提醒我买菜": ("买菜", (local_now + timedelta(days=1)).replace(hour=9)),
        }
        for text, (content, expected) in cases.items():
            draft = parse_reminder_request(text, now=local_now)
            self.assertIsNotNone(draft, text)
            self.assertEqual(draft.content, content)
            self.assertEqual(draft.due_at, expected, text)
            self.assertGreater(draft.delay_seconds, 0)

    def test_resolve_when_accepts_durations_and_clock_times(self):
        local_now = datetime(2026, 10, 27, 12, 0).astimezone()
        self.assertEqual(
            resolve_when("45秒后", now=local_now),
            local_now + timedelta(seconds=45),
        )
        self.assertEqual(
            resolve_when("10分钟", now=local_now),
            local_now + timedelta(minutes=10),
        )
        self.assertEqual(
            resolve_when("下午2点15分", now=local_now),
            local_now.replace(hour=14, minute=15),
        )
        self.assertEqual(
            resolve_when("2026-10-29T08:00", now=local_now),
            datetime(2026, 10, 29, 8, 0).astimezone(),
        )
        self.assertIsNone(resolve_when("待会儿", now=local_now))
        self.assertIsNone(resolve_when("", now=local_now))

    def test_parse_rejects_non_reminder_text(self):
        for text in (
            "提醒我开会",
            "明天提醒我交材料",
            "30秒后提醒我",
            "",
        ):
            self.assertIsNone(parse_reminder_request(text, now=NOW), text)


class UnfinishedTopicTest(unittest.TestCase):
    def message(self, message_id, role, content, minutes_ago=5):
        return {
            "id": message_id,
            "role": role,
            "content": content,
            "created_at": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
        }

    def test_tail_question_is_unfinished(self):
        topics = derive_unfinished_topics(
            [self.message(1, "user", "今天要做什么？")],
            now=NOW,
        )

        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0].source_message_id, 1)
        self.assertEqual(topics[0].reason, "unanswered")

    def test_answered_question_is_not_reported(self):
        topics = derive_unfinished_topics(
            [
                self.message(1, "user", "今天要做什么？", 10),
                self.message(2, "assistant", "先整理笔记。", 9),
            ],
            now=NOW,
        )

        self.assertEqual(topics, [])

    def test_failure_reply_keeps_topic_open(self):
        topics = derive_unfinished_topics(
            [
                self.message(1, "user", "帮我查一下明天的天气？", 10),
                self.message(
                    2,
                    "assistant",
                    "工具调用轮次达到上限，我先停在这里。",
                    9,
                ),
            ],
            now=NOW,
        )

        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0].reason, "failed_reply")

    def test_stale_question_is_ignored(self):
        topics = derive_unfinished_topics(
            [self.message(1, "user", "在吗？", minutes_ago=60 * 25)],
            now=NOW,
        )

        self.assertEqual(topics, [])


class ActivityTest(unittest.TestCase):
    def snapshot(self, **kwargs):
        defaults = {
            "idle_seconds": 0.0,
            "foreground_title": "",
            "process_name": "",
            "fullscreen": False,
        }
        defaults.update(kwargs)
        return ActivitySnapshot(**defaults)

    def test_activity_thresholds_and_signals(self):
        self.assertEqual(
            classify_activity(self.snapshot()),
            ACTIVITY_ACTIVE,
        )
        self.assertEqual(
            classify_activity(self.snapshot(idle_seconds=400)),
            ACTIVITY_IDLE,
        )
        self.assertEqual(
            classify_activity(self.snapshot(idle_seconds=1000)),
            ACTIVITY_AWAY,
        )
        self.assertEqual(
            classify_activity(self.snapshot(locked=True)),
            ACTIVITY_AWAY,
        )
        self.assertEqual(
            classify_activity(
                self.snapshot(foreground_title="无痕浏览 - Chrome")
            ),
            ACTIVITY_PRIVATE,
        )
        self.assertEqual(
            classify_activity(self.snapshot(process_name="cs2.exe")),
            ACTIVITY_GAME,
        )
        self.assertEqual(
            classify_activity(self.snapshot(process_name="code.exe")),
            ACTIVITY_FOCUS,
        )
        self.assertEqual(
            classify_activity(self.snapshot(fullscreen=True)),
            ACTIVITY_FOCUS,
        )
        self.assertEqual(
            classify_activity(
                ActivitySnapshot(idle_seconds=0.0, available=False)
            ),
            ACTIVITY_UNKNOWN,
        )


class GateTest(unittest.TestCase):
    def state(self, **kwargs):
        defaults = {"mode": AgentMode.CHAT, "now": NOW}
        defaults.update(kwargs)
        return GateState(**defaults)

    def test_default_state_allows(self):
        self.assertTrue(evaluate_gate(self.state()).allowed)

    def test_quiet_mode_and_busy_block(self):
        self.assertEqual(
            evaluate_gate(self.state(mode=AgentMode.QUIET)).reason,
            "quiet_mode",
        )
        self.assertEqual(evaluate_gate(self.state(busy=True)).reason, "busy")

    def test_quiet_hours_wrap_midnight_and_can_be_disabled(self):
        late = datetime(2026, 10, 27, 23, 30, tzinfo=timezone.utc)
        early = datetime(2026, 10, 27, 7, 30, tzinfo=timezone.utc)
        noon = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)

        self.assertEqual(
            evaluate_gate(self.state(now=late)).reason,
            "quiet_hours",
        )
        self.assertEqual(
            evaluate_gate(self.state(now=early)).reason,
            "quiet_hours",
        )
        self.assertTrue(evaluate_gate(self.state(now=noon)).allowed)
        self.assertTrue(
            evaluate_gate(self.state(now=late, quiet_hours=None)).allowed
        )

    def test_daily_limit_and_interval(self):
        self.assertEqual(
            evaluate_gate(self.state(daily_count=2)).reason,
            "daily_limit",
        )
        self.assertEqual(
            evaluate_gate(
                self.state(last_spoken_at=NOW - timedelta(hours=1))
            ).reason,
            "min_interval",
        )
        self.assertTrue(
            evaluate_gate(
                self.state(last_spoken_at=NOW - timedelta(hours=4))
            ).allowed
        )

    def test_unanswered_blocks_after_two_silent_messages(self):
        self.assertTrue(evaluate_gate(self.state(unanswered=1)).allowed)
        self.assertEqual(
            evaluate_gate(self.state(unanswered=2)).reason,
            "unanswered",
        )

    def test_activity_and_privacy_gate(self):
        self.assertEqual(
            evaluate_gate(self.state(activity=ACTIVITY_PRIVATE)).reason,
            "privacy",
        )
        self.assertEqual(
            evaluate_gate(self.state(activity=ACTIVITY_IDLE)).reason,
            "activity_idle",
        )
        self.assertEqual(
            evaluate_gate(self.state(activity=ACTIVITY_UNKNOWN)).reason,
            "activity_unknown",
        )


class ProactiveEngineTest(ProactiveBase):
    def test_no_source_never_speaks(self):
        runtime = self.runtime()
        result = runtime.proactive_preview()

        self.assertFalse(result["speak"])
        self.assertEqual(result["reason"], "no_source")
        self.assertIsNone(result["source"])

    def test_due_reminder_becomes_source_when_gate_allows(self):
        self.store.ensure_session("main", AgentMode.CHAT)
        service = ReminderService(self.store)
        service.create("main", "喝水", NOW - timedelta(minutes=1))
        engine = ProactiveEngine(self.store, service)

        preview = engine.preview(
            "main",
            now=NOW,
            gate_state=GateState(
                mode=AgentMode.CHAT,
                now=NOW,
            ),
        )

        self.assertTrue(preview.speak)
        self.assertEqual(preview.source.source_type, "reminder")
        self.assertIn("喝水", preview.source.content)
        self.assertIn("到点", preview.source.trigger_reason)

    def test_gate_denial_keeps_sources_but_does_not_speak(self):
        self.store.ensure_session("main", AgentMode.CHAT)
        service = ReminderService(self.store)
        service.create("main", "喝水", NOW - timedelta(minutes=1))
        engine = ProactiveEngine(self.store, service)

        preview = engine.preview(
            "main",
            now=NOW,
            gate_state=GateState(
                mode=AgentMode.CHAT,
                now=NOW,
                daily_count=2,
            ),
        )

        self.assertFalse(preview.speak)
        self.assertEqual(preview.reason, "daily_limit")
        self.assertIsNone(preview.source)
        self.assertEqual(len(preview.sources), 1)

    def test_unfinished_topic_is_traceable(self):
        self.store.ensure_session("main", AgentMode.CHAT)
        message_id = self.store.add_message(
            "main",
            "user",
            "能帮我总结一下这个方案吗？",
            AgentMode.CHAT,
        )
        engine = ProactiveEngine(self.store)

        preview = engine.preview(
            "main",
            now=datetime.now(timezone.utc),
            gate_state=GateState(
                mode=AgentMode.CHAT,
                now=datetime.now(timezone.utc),
                quiet_hours=None,
            ),
        )

        self.assertTrue(preview.speak)
        self.assertEqual(
            preview.source.source_type,
            "unfinished_topic",
        )
        self.assertEqual(preview.source.source_message_id, message_id)


class RuntimeProactiveTest(ProactiveBase):
    def test_handle_message_creates_reminder_without_llm(self):
        llm = CountingLLM()
        runtime = self.runtime(llm=llm)

        result = runtime.handle_message(
            "提醒我 10 分钟后喝水",
            AgentMode.CHAT,
        )

        self.assertEqual(llm.calls, 0)
        self.assertIn("喝水", result["answer"])
        self.assertIn("提醒", result["answer"])
        pending = runtime.list_reminders()
        self.assertEqual([item.content for item in pending], ["喝水"])

    def test_runtime_exposes_activity_and_completion(self):
        runtime = self.runtime()

        activity = runtime.activity_state()
        self.assertIn(
            activity["state"],
            {
                ACTIVITY_ACTIVE,
                ACTIVITY_IDLE,
                ACTIVITY_AWAY,
                ACTIVITY_PRIVATE,
                ACTIVITY_FOCUS,
                ACTIVITY_GAME,
                ACTIVITY_UNKNOWN,
            },
        )

        runtime.handle_message("提醒我 10 分钟后喝水", AgentMode.CHAT)
        reminder = runtime.list_reminders()[0]
        completed = runtime.complete_reminder(reminder.reminder_id)
        self.assertEqual(completed.status, "done")


class ProactiveMigrationTest(ProactiveBase):
    def test_v4_database_gains_proactive_tables(self):
        database = self.root / "legacy.db"
        store = CompanionStore(database)
        with store._connection() as connection:
            connection.execute("DROP TABLE reminders")
            connection.execute("DROP TABLE proactive_state")
            connection.execute("DROP TABLE proactive_events")
            connection.execute("DROP TABLE work_receipts")
            connection.execute("DROP TABLE work_tasks")
            connection.execute("PRAGMA user_version = 4")

        reopened = CompanionStore(database)

        with reopened._connection() as connection:
            version = connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertEqual(SCHEMA_VERSION, 9)
        self.assertEqual(version, SCHEMA_VERSION)
        self.assertIn("reminders", tables)
        self.assertIn("proactive_state", tables)
        self.assertIn("proactive_events", tables)
        self.assertIn("work_tasks", tables)
        self.assertIn("work_receipts", tables)
