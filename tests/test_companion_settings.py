"""Application settings: storage, validation, token budget and the HTTP surface."""

import base64
import json
import os
import sqlite3
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from agent_framework.companion import settings as app_settings
from agent_framework.companion.assets import MAX_AVATAR_BYTES
from agent_framework.companion import tokens as token_tools
from agent_framework.companion.app import create_application_service
from agent_framework.companion.context import select_history
from agent_framework.companion.memory import MemoryService
from agent_framework.companion.models import AgentMode
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import SCHEMA_VERSION, CompanionStore
from agent_framework.companion.web import WebAdapter, upstream
from agent_framework.config import AgentConfig, CONFIG_PATH_ENV, config_path, save_config
from agent_framework.llm import OfflineLLMClient, RemoteLLMClient


# 1x1 transparent PNG, same fixture the asset tests use.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class StubSearch:
    def search(self, query, limit=5):
        return []


def make_runtime(root, config=None, settings=None):
    store = CompanionStore(root / "companion.db")
    runtime = CompanionRuntime(
        store=store,
        llm_client=OfflineLLMClient(),
        search_service=SearchService(StubSearch()),
        data_dir=root,
        session_id="main",
        offline=True,
        settings=settings if settings is not None else app_settings.defaults(),
        config=config,
    )
    return store, runtime


class TokenEstimateTests(unittest.TestCase):
    def test_cjk_and_ascii_rates(self):
        self.assertEqual(token_tools.estimate_tokens("你好"), 2)
        self.assertEqual(token_tools.estimate_tokens("abcd"), 1)
        self.assertEqual(token_tools.estimate_tokens("abcde"), 2)
        self.assertEqual(token_tools.estimate_tokens(""), 0)
        self.assertEqual(token_tools.estimate_tokens(None), 0)

    def test_conversion_round_trip(self):
        self.assertEqual(token_tools.tokens_to_chars(32000), 64000)
        self.assertEqual(token_tools.chars_to_tokens(24000), 12000)
        self.assertEqual(token_tools.tokens_to_chars(-5), 0)


class SettingsFieldTests(unittest.TestCase):
    def test_normalize_is_lenient_and_drops_unknown(self):
        clean = app_settings.normalize({"temperature": "abc", "max_tokens": 100000, "zzz": 1})
        self.assertEqual(clean["temperature"], app_settings.defaults()["temperature"])
        self.assertEqual(clean["max_tokens"], 32768)
        self.assertNotIn("zzz", clean)

    def test_validate_update_reports_each_bad_key(self):
        merged, errors = app_settings.validate_update(
            app_settings.defaults(),
            {"temperature": 3, "model": " gpt-4o ", "nope": 1},
        )
        self.assertEqual(merged["model"], "gpt-4o")
        keys = {item["key"] for item in errors}
        self.assertEqual(keys, {"temperature", "nope"})

    def test_model_contexts_coerce_to_ints(self):
        merged, errors = app_settings.validate_update(
            app_settings.defaults(), {"model_contexts": {"gpt-4o": "128000"}}
        )
        self.assertEqual(merged["model_contexts"], {"gpt-4o": 128000})
        self.assertEqual(errors, [])

    def test_mask_never_keeps_the_middle(self):
        masked = app_settings.mask_secret("sk-abcd1234efgh5678")
        self.assertTrue(masked.startswith("sk-"))
        self.assertTrue(masked.endswith("5678"))
        self.assertNotIn("1234efgh", masked)
        self.assertEqual(app_settings.mask_secret(""), "")

    def test_every_field_has_a_group(self):
        grouped = app_settings.groups()
        self.assertEqual(
            sorted(key for keys in grouped.values() for key in keys),
            sorted(app_settings.BY_KEY),
        )

    def test_the_slice_four_keys_are_declared_where_the_panel_puts_them(self):
        self.assertEqual("memory", app_settings.BY_KEY["memory_enabled"].group)
        self.assertEqual("memory", app_settings.BY_KEY["output_format"].group)
        self.assertEqual("advanced", app_settings.BY_KEY["tool_permission"].group)
        self.assertTrue(app_settings.BY_KEY["memory_enabled"].default)
        self.assertEqual(
            ("default", "concise", "bullets", "detailed"),
            app_settings.BY_KEY["output_format"].choices,
        )
        self.assertEqual(
            ("strict", "balanced", "relaxed"),
            app_settings.BY_KEY["tool_permission"].choices,
        )

    def test_every_choice_has_the_copy_the_panel_shows(self):
        # 一个键写了句子却读不到，界面就会显示空提示；两边必须一一对应。
        self.assertEqual(
            set(app_settings.BY_KEY["output_format"].choices),
            set(app_settings.OUTPUT_FORMATS),
        )
        self.assertEqual("", app_settings.OUTPUT_FORMATS["default"])
        self.assertTrue(
            all(app_settings.OUTPUT_FORMATS[key] for key in ("concise", "bullets", "detailed"))
        )

    def test_bad_choices_are_refused_rather_than_silently_defaulted(self):
        merged, errors = app_settings.validate_update(
            app_settings.defaults(),
            {
                "memory_enabled": "yes",
                "output_format": "markdown",
                "tool_permission": "yolo",
            },
        )
        self.assertEqual(
            {"memory_enabled", "output_format", "tool_permission"},
            {item["key"] for item in errors},
        )
        self.assertTrue(merged["memory_enabled"])
        self.assertEqual("default", merged["output_format"])
        self.assertEqual("balanced", merged["tool_permission"])

    def test_lenient_read_falls_back_for_a_stored_junk_value(self):
        clean = app_settings.normalize(
            {"memory_enabled": "nope", "output_format": "", "tool_permission": None}
        )
        self.assertTrue(clean["memory_enabled"])
        self.assertEqual("default", clean["output_format"])
        self.assertEqual("balanced", clean["tool_permission"])


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_v10_migration_creates_the_table(self):
        path = self.root / "companion.db"
        connection = sqlite3.connect(str(path))
        try:
            connection.executescript(
                "CREATE TABLE sessions (id TEXT PRIMARY KEY, mode TEXT NOT NULL,"
                " created_at TEXT NOT NULL, updated_at TEXT NOT NULL);"
            )
            connection.execute("PRAGMA user_version = 9")
        finally:
            connection.commit()
            connection.close()
        store = CompanionStore(path)
        self.assertEqual(store.load_settings(), None)
        store.save_settings({"model": "gpt-4o-mini"})
        self.assertEqual(store.load_settings()["model"], "gpt-4o-mini")
        self.assertEqual(SCHEMA_VERSION, 17)

    def test_round_trip_and_corrupt_row(self):
        store = CompanionStore(self.root / "companion.db")
        saved = store.save_settings({"temperature": 0.3, "context_tokens": 8000})
        self.assertEqual(store.load_settings(), saved)
        connection = sqlite3.connect(str(store.database_path))
        try:
            connection.execute("UPDATE app_settings SET data = '{' WHERE id = 1")
        finally:
            connection.commit()
            connection.close()
        self.assertIsNone(store.load_settings())

    def test_secrets_are_not_stored(self):
        store = CompanionStore(self.root / "companion.db")
        store.save_settings({"model": "m"})
        raw = json.dumps(store.load_settings())
        self.assertNotIn("api_key", raw)


class RuntimeSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_apply_settings_moves_every_consumer(self):
        store, runtime = make_runtime(self.root)
        self.assertEqual(runtime.max_context_chars, 64000)
        runtime.apply_settings({"context_tokens": 8000, "max_tool_rounds": 6, "memory_top_k": 2})
        self.assertEqual(runtime.max_context_chars, 16000)
        self.assertEqual(runtime.max_tool_rounds, 6)
        self.assertEqual(runtime.memory_top_k, 2)
        # apply_settings 只负责推送，落库由 HTTP 层完成。
        self.assertIsNone(store.load_settings())

    def test_apply_settings_without_values_reads_the_stored_row(self):
        store, runtime = make_runtime(self.root)
        store.save_settings({"context_tokens": 8000, "memory_top_k": 3})
        runtime.apply_settings()
        self.assertEqual(runtime.memory_top_k, 3)
        self.assertEqual(runtime.max_context_chars, 16000)

    def test_config_receives_values_and_client_is_rebuilt(self):
        config = AgentConfig(api_key="", base_url="", model="old")
        store, runtime = make_runtime(self.root, config=config)
        runtime.apply_settings({"model": "gpt-4o", "temperature": 0.2, "top_p": 0.9, "max_tokens": 512})
        self.assertEqual(config.model, "gpt-4o")
        self.assertEqual(config.temperature, 0.2)
        self.assertEqual(config.top_p, 0.9)
        self.assertEqual(config.max_tokens, 512)
        self.assertIsInstance(runtime.llm_client, OfflineLLMClient)
        self.assertTrue(runtime.offline)

    def test_offline_toggle_flips_the_runtime(self):
        config = AgentConfig(api_key="sk-test", base_url="https://example.test/v1")
        store, runtime = make_runtime(self.root, config=config, settings={"offline": True})
        self.assertTrue(runtime.offline)
        runtime.apply_settings({"offline": False})
        self.assertFalse(runtime.settings["offline"])

    def test_without_settings_the_legacy_budget_stands(self):
        store = CompanionStore(self.root / "companion.db")
        runtime = CompanionRuntime(
            store=store,
            llm_client=OfflineLLMClient(),
            search_service=SearchService(StubSearch()),
            data_dir=self.root,
            offline=True,
            max_context_chars=9000,
        )
        self.assertEqual(runtime.max_context_chars, 9000)
        self.assertEqual(runtime.max_history_messages, 0)
        self.assertEqual(runtime.max_tool_rounds, 3)


class SamplingRequestTests(unittest.TestCase):
    class Fake:
        def __init__(self, top_p, max_tokens):
            self.config = AgentConfig(top_p=top_p, max_tokens=max_tokens)

        def apply(self, request):
            return RemoteLLMClient._apply_sampling(self, request)

    def test_defaults_keep_the_payload_unchanged(self):
        request = {"model": "m", "temperature": 0.7}
        self.assertEqual(self.Fake(1.0, 0).apply(dict(request)), request)

    def test_narrowed_values_are_sent(self):
        result = self.Fake(0.9, 512).apply({"model": "m"})
        self.assertEqual(result, {"model": "m", "max_tokens": 512, "top_p": 0.9})


class HistoryCapTests(unittest.TestCase):
    def messages(self, count):
        return [
            {"id": index, "role": "user" if index % 2 else "assistant", "content": "x" * 40}
            for index in range(1, count + 1)
        ]

    def test_overflow_goes_to_the_summary_not_into_the_void(self):
        items = self.messages(12)
        selection = select_history(items, 100000, max_messages=5)
        self.assertEqual(len(selection.messages), 5)
        self.assertEqual(len(selection.dropped), 7)
        self.assertTrue(selection.compressed)
        self.assertEqual(selection.dropped[0]["id"], 1)
        self.assertEqual(selection.dropped[-1]["id"], 7)
        self.assertEqual(selection.messages[0]["id"], 8)
        self.assertEqual(
            selection.used_chars, sum(len(i["content"]) + 4 for i in selection.messages)
        )

    def test_zero_cap_keeps_the_old_behaviour(self):
        items = self.messages(4)
        selection = select_history(items, 100000, max_messages=0)
        self.assertEqual(len(selection.messages), 4)
        self.assertEqual(selection.dropped, [])
        self.assertFalse(selection.compressed)


class RecallSettingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.store = CompanionStore(self.root / "companion.db")
        self.store.ensure_session("main", AgentMode.CHAT)
        self.memory = MemoryService(self.store)
        self.message = self.store.add_message("main", "user", "我喜欢蓝色", AgentMode.CHAT)

    def test_threshold_filters_low_scores(self):
        self.memory.observe_user_message("main", self.message, "我喜欢蓝色")
        confirmed = [
            item
            for item in self.memory.recall("main", "蓝色", limit=5)
        ]
        self.assertTrue(confirmed)
        floor = max(score for score, _, _ in confirmed) + 1
        self.assertEqual(self.memory.recall("main", "蓝色", limit=5, min_score=floor), [])


class MemorySwitchAndAnswerFormatTests(unittest.TestCase):
    """「对话记忆与交互」区里两个真会改变行为的开关。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_a_fresh_runtime_starts_on_the_shipped_side(self):
        store, runtime = make_runtime(self.root)
        self.assertTrue(runtime.memory_enabled)
        self.assertEqual("default", runtime.output_format)
        self.assertEqual("balanced", runtime.tool_permission)

    def test_the_switch_stops_recalling_history(self):
        store, runtime = make_runtime(self.root)
        store.add_memory(
            "main", "preference", "喜欢喝乌龙茶", None, 0.9, 0.9, status="confirmed"
        )

        def prompt():
            return runtime._compose_system(
                "乌龙茶", AgentMode.CHAT, None, runtime._recall_lines("乌龙茶")
            )[0]

        self.assertIn("喜欢喝乌龙茶", prompt())

        runtime.apply_settings({"memory_enabled": False})

        self.assertEqual([], runtime._recall_lines("乌龙茶"))
        self.assertIn("暂无相关长期记忆。", prompt())

        runtime.apply_settings({"memory_enabled": True})
        self.assertIn("喜欢喝乌龙茶", prompt())

    def test_the_switch_stops_observing_new_messages(self):
        store, runtime = make_runtime(self.root)
        runtime.apply_settings({"memory_enabled": False})

        runtime.handle_message("请记住我喜欢简洁回答。", AgentMode.CHAT)

        self.assertEqual([], store.list_memories("main"))
        self.assertGreaterEqual(len(store.list_messages("main")), 2)

        runtime.apply_settings({"memory_enabled": True})
        runtime.handle_message("请记住我喜欢简洁回答。", AgentMode.CHAT)
        self.assertTrue(store.list_memories("main"))

    def test_the_answer_format_is_written_into_the_prompt(self):
        store, runtime = make_runtime(self.root)
        system, layers = runtime._compose_system("你好", AgentMode.CHAT, None, [])
        self.assertNotIn("回答方式", system)

        runtime.apply_settings({"output_format": "bullets"})
        bulleted, bulleted_layers = runtime._compose_system(
            "你好", AgentMode.CHAT, None, []
        )

        self.assertIn("## 回答方式（用户设定，优先于角色语气）", bulleted)
        self.assertIn(app_settings.OUTPUT_FORMATS["bullets"], bulleted)
        self.assertGreater(bulleted_layers["session"], layers["session"])

    def test_an_unknown_format_falls_back_to_adding_nothing(self):
        store, runtime = make_runtime(self.root)
        runtime.apply_settings({"output_format": "nonsense"})
        self.assertEqual("default", runtime.output_format)
        system, _ = runtime._compose_system("你好", AgentMode.CHAT, None, [])
        self.assertNotIn("回答方式", system)

    def test_the_tier_reaches_the_tool_registry(self):
        store, runtime = make_runtime(self.root)
        runtime.select_workspace(str(self.root))
        runtime.apply_settings({"tool_permission": "strict"})
        self.assertEqual("strict", runtime._core_tool_registry(AgentMode.WORK, None).policy_tier)
        runtime.apply_settings({"tool_permission": "信任"})
        # 中文档位名不是存储值：回落到 balanced，而不是把工具清单整个关掉。
        self.assertEqual("balanced", runtime.tool_permission)


class WebSettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        store, runtime = make_runtime(self.root)
        runtime.select_workspace(str(self.root))
        self.runtime = runtime
        self.store = store
        self.service = create_application_service(runtime)
        self.adapter = WebAdapter(self.service, data_dir=self.root).start()
        self.addCleanup(self.adapter.stop)
        self.base = "http://" + self.adapter.address

    def request(self, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={
                "X-Companion-Token": self.adapter.token,
                "Content-Type": "application/json",
            },
            method="POST" if body is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8") or "{}")

    def test_get_reports_masked_key_only(self):
        status, payload = self.request("/api/settings")
        self.assertEqual(status, 200)
        self.assertEqual(payload["settings"]["context_tokens"], 32000)
        self.assertIn("masked", payload["api_key"])
        self.assertNotIn("OPENAI_API_KEY", json.dumps(payload))

    def test_post_persists_and_applies(self):
        status, payload = self.request("/api/settings", {"temperature": 0.25, "memory_top_k": 9})
        self.assertEqual(status, 200)
        self.assertEqual(payload["errors"], [])
        self.assertEqual(self.store.load_settings()["temperature"], 0.25)
        self.assertEqual(self.runtime.memory_top_k, 9)
        again = self.request("/api/settings")[1]
        self.assertEqual(again["settings"]["memory_top_k"], 9)

    def test_bad_values_are_rejected_without_overwriting(self):
        self.request("/api/settings", {"temperature": 0.4})
        status, payload = self.request("/api/settings", {"temperature": 9, "nope": 1})
        self.assertEqual(status, 200)
        self.assertEqual({item["key"] for item in payload["errors"]}, {"temperature", "nope"})
        self.assertEqual(payload["settings"]["temperature"], 0.4)

    def test_writes_are_refused_mid_run(self):
        self.request("/api/settings", {"temperature": 0.9})
        self.service.active_run = lambda session_id=None: object()
        status, payload = self.request("/api/settings", {"temperature": 0.1})
        self.assertEqual(status, 409)
        self.assertIn("还在进行", payload["error"])
        self.assertEqual(self.store.load_settings()["temperature"], 0.9)

    def test_change_is_broadcast(self):
        subscriber = self.adapter.subscribe()
        self.addCleanup(self.adapter.unsubscribe, subscriber)
        self.request("/api/settings", {"top_p": 0.5})
        deadline = time.time() + 5
        seen = []
        while time.time() < deadline:
            try:
                seen.append(json.loads(subscriber.get(timeout=1)).get("type"))
            except Exception:
                continue
            if "settings" in seen:
                break
        self.assertIn("settings", seen)


class WebSettingsZoneTests(unittest.TestCase):
    """切片 4 的五个区：服务器给出选项与事实，页面只负责照着说。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        store, runtime = make_runtime(self.root)
        runtime.select_workspace(str(self.root))
        self.runtime = runtime
        self.store = store
        self.service = create_application_service(runtime)
        self.adapter = WebAdapter(self.service, data_dir=self.root).start()
        self.addCleanup(self.adapter.stop)
        self.base = "http://" + self.adapter.address

    request = WebSettingsApiTests.request

    def test_the_payload_carries_tiers_formats_and_diagnostics(self):
        status, payload = self.request("/api/settings")

        self.assertEqual(status, 200)
        tiers = {item["value"]: item for item in payload["permissions"]["tiers"]}
        self.assertEqual({"strict", "balanced", "relaxed"}, set(tiers))
        self.assertEqual("保守", tiers["strict"]["label"])
        self.assertIn("禁用", tiers["strict"]["hint"])
        self.assertEqual(payload["permissions"]["current"], "balanced")

        formats = {item["value"]: item for item in payload["output_formats"]}
        self.assertEqual(
            {"default", "concise", "bullets", "detailed"}, set(formats)
        )
        self.assertIn("不额外加要求", formats["default"]["hint"])
        self.assertIn(
            app_settings.OUTPUT_FORMATS["bullets"], formats["bullets"]["hint"]
        )

        diag = payload["diagnostics"]
        self.assertEqual(str(self.root.resolve()), diag["data_dir"])
        self.assertEqual(SCHEMA_VERSION, diag["schema_version"])
        self.assertEqual(SCHEMA_VERSION, diag["expected_schema_version"])
        self.assertIn("companion.db", diag["database"])
        self.assertEqual(0, diag["backups"])

    def test_the_tier_and_the_memory_switch_round_trip(self):
        status, payload = self.request(
            "/api/settings",
            {"tool_permission": "relaxed", "memory_enabled": False, "output_format": "concise"},
        )
        self.assertEqual(status, 200)
        self.assertEqual([], payload["errors"])
        self.assertEqual("relaxed", self.runtime.tool_permission)
        self.assertFalse(self.runtime.memory_enabled)
        self.assertEqual("concise", self.runtime.output_format)
        self.assertEqual("relaxed", self.store.load_settings()["tool_permission"])
        self.assertEqual("relaxed", self.request("/api/settings")[1]["permissions"]["current"])

    def test_a_made_up_tier_is_refused_instead_of_resolved(self):
        status, payload = self.request("/api/settings", {"tool_permission": "whatever"})

        self.assertEqual(status, 200)
        self.assertEqual({"tool_permission"}, {item["key"] for item in payload["errors"]})
        self.assertEqual("balanced", self.runtime.tool_permission)
        self.assertEqual("balanced", self.store.load_settings()["tool_permission"])

    def test_the_avatar_upload_lands_in_the_persona_slot(self):
        subscriber = self.adapter.subscribe()
        self.addCleanup(self.adapter.unsubscribe, subscriber)
        png = base64.b64encode(PNG).decode("ascii")

        status, payload = self.request(
            "/api/asset/avatar", {"filename": "照片.png", "data": "data:image/png;base64," + png}
        )

        self.assertEqual(status, 200)
        slot = self.root / "assets" / self.runtime.persona_id / "avatar.png"
        self.assertEqual(PNG, slot.read_bytes())
        self.assertEqual("png", payload["assets"]["avatar"]["kind"])
        self.assertEqual([], payload["removed"])
        self.assertEqual(self.runtime.persona_id, payload["persona_id"])
        self.assertIn(
            "refresh",
            [json.loads(subscriber.get(timeout=5)).get("type")],
        )

    def test_the_avatar_upload_refuses_junk_without_touching_the_slot(self):
        cases = [
            ({"filename": "a.txt", "data": base64.b64encode(PNG).decode()}, "只支持"),
            ({"filename": "a.png", "data": ""}, "没有收到"),
            ({"filename": "a.png", "data": "not base64 at all!"}, "有效的图片"),
            ({"filename": "a.png", "data": "A" * (MAX_AVATAR_BYTES * 2 + 8)}, "8 MiB"),
            ({"filename": "a.png", "data": base64.b64encode(b"plain text").decode()}, "扩展名不符"),
        ]
        for body, needle in cases:
            with self.subTest(filename=body["filename"], data=body["data"][:12]):
                status, payload = self.request("/api/asset/avatar", body)
                self.assertEqual(status, 400)
                self.assertIn(needle, payload["error"])
        self.assertFalse((self.root / "assets").exists())

    def test_purge_all_reports_what_it_kept(self):
        self.store.add_memory("main", "fact", "会走掉", None, 0.8, 0.6, status="confirmed")
        kept = self.store.add_memory(
            "main", "preference", "要留着", None, 0.9, 0.9,
            status="confirmed", protected=True,
        )

        status, payload = self.request("/api/console/memory", {"action": "purge_all"})

        self.assertEqual(status, 200)
        self.assertEqual(1, payload["deleted"])
        self.assertEqual(1, payload["protected"])
        self.assertEqual([kept], [row["memory_id"] for row in payload["rows"]])

    def test_purge_all_on_an_empty_store_is_not_an_error(self):
        status, payload = self.request("/api/console/memory", {"action": "purge_all"})

        self.assertEqual(status, 200)
        self.assertEqual(0, payload["deleted"])
        self.assertEqual(0, payload["remaining"])


class FromConfigSeedTests(unittest.TestCase):
    def test_first_launch_seeds_settings_from_the_file_config(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            config_file = root / "keys.json"
            config_file.write_text(
                json.dumps(
                    {
                        "OPENAI_API_KEY": "sk-test-value",
                        "OPENAI_BASE_URL": "https://example.test/v1",
                        "MODEL_NAME": "seeded-model",
                    }
                ),
                encoding="utf-8",
            )
            original = AgentConfig.from_env
            AgentConfig.from_env = lambda path=None: original(str(config_file))
            try:
                runtime = CompanionRuntime.from_config(data_dir=root, session_id="main")
            finally:
                AgentConfig.from_env = original
            stored = runtime.store.load_settings()
            self.assertEqual(stored["model"], "seeded-model")
            self.assertEqual(stored["base_url"], "https://example.test/v1")
            self.assertEqual(runtime.settings["model"], "seeded-model")
            self.assertFalse(runtime.offline)

    def test_cli_offline_overrides_without_rewriting_the_row(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            config_file = root / "keys.json"
            config_file.write_text(
                json.dumps(
                    {
                        "OPENAI_API_KEY": "sk-test-value",
                        "OPENAI_BASE_URL": "https://example.test/v1",
                    }
                ),
                encoding="utf-8",
            )
            original = AgentConfig.from_env
            AgentConfig.from_env = lambda path=None: original(str(config_file))
            try:
                online = CompanionRuntime.from_config(data_dir=root)
                offline = CompanionRuntime.from_config(data_dir=root, offline=True)
            finally:
                AgentConfig.from_env = original
            self.assertTrue(offline.offline)
            self.assertFalse(online.store.load_settings()["offline"])


class CatalogWindowTests(unittest.TestCase):
    def test_exact_id_wins(self):
        self.assertEqual(app_settings.catalog_window("qwen-plus"), 131072)
        self.assertEqual(app_settings.catalog_window("gpt-4o-mini"), 131072)
        self.assertEqual(app_settings.catalog_window("moonshot-v1-8k"), 8192)

    def test_versioned_id_inherits_its_family(self):
        self.assertEqual(app_settings.catalog_window("gpt-4o-2024-08-06"), 131072)
        self.assertEqual(app_settings.catalog_window("qwen-plus.latest"), 131072)

    def test_a_family_only_matches_on_a_separator(self):
        # "qwen-plus" must not shadow "qwen-plus-plus"; the next character has
        # to be a separator, not another letter of a longer name.
        self.assertEqual(app_settings.catalog_window("qwen-plusmax"), 0)

    def test_unknown_is_zero_not_a_guess(self):
        self.assertEqual(app_settings.catalog_window("some-private-model"), 0)
        self.assertEqual(app_settings.catalog_window(""), 0)
        self.assertEqual(app_settings.catalog_window(None), 0)


class ConfigFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.keys = self.root / "config" / "keys.json"

    def test_merge_keeps_untouched_keys_and_drops_empty_ones(self):
        self.keys.parent.mkdir(parents=True)
        self.keys.write_text(
            json.dumps({"OPENAI_API_KEY": "sk-old", "HAND_WRITTEN": "keep"}),
            encoding="utf-8",
        )
        saved = save_config(
            {"OPENAI_API_KEY": "sk-new", "MODEL_NAME": "qwen-max", "HAND_WRITTEN": ""},
            path=str(self.keys),
        )
        stored = json.loads(self.keys.read_text(encoding="utf-8"))
        self.assertEqual(saved, ["MODEL_NAME", "OPENAI_API_KEY"])
        self.assertEqual(stored["OPENAI_API_KEY"], "sk-new")
        self.assertNotIn("HAND_WRITTEN", stored)

    def test_write_is_atomic_and_creates_the_folder(self):
        save_config({"OPENAI_BASE_URL": "https://example.test/v1"}, path=str(self.keys))
        self.assertTrue(self.keys.exists())
        self.assertFalse(Path(str(self.keys) + ".tmp").exists())

    def test_a_broken_file_is_replaced_not_rethrown(self):
        self.keys.parent.mkdir(parents=True)
        self.keys.write_text("{ not json", encoding="utf-8")
        self.assertEqual(save_config({"MODEL_NAME": "m"}, path=str(self.keys)), ["MODEL_NAME"])

    def test_environment_selects_the_credential_file(self):
        silence_ambient_credentials(self)
        self.keys.parent.mkdir(parents=True, exist_ok=True)
        self.keys.write_text(json.dumps({"OPENAI_API_KEY": "sk-env"}), encoding="utf-8")
        os.environ[CONFIG_PATH_ENV] = str(self.keys)
        self.addCleanup(os.environ.pop, CONFIG_PATH_ENV, None)
        self.assertEqual(config_path(), str(self.keys))
        self.assertEqual(AgentConfig.from_env().api_key, "sk-env")


class _FakeResponse:
    def __init__(self, body, status=200):
        self.status = status
        self.body = body if isinstance(body, bytes) else body.encode("utf-8")

    def read(self, limit=-1):
        return self.body if limit is None or limit < 0 else self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *error):
        return False


AMBIENT_KEYS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "DATABASE_API_KEY",
    "DATABASE_BASE_URL",
    "MODEL_NAME",
    "AGENT_MODEL",
)


def silence_ambient_credentials(case):
    """Keep whatever the developer's shell exports out of the assertion path."""
    for name in AMBIENT_KEYS:
        previous = os.environ.get(name)
        case.addCleanup(
            os.environ.pop if previous is None else os.environ.__setitem__,
            name,
            previous if previous is not None else None,
        )
        os.environ[name] = ""


class UpstreamProxyTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.original = upstream.urlopen
        self.addCleanup(setattr, upstream, "urlopen", self.original)

    def fake(self, response):
        def open_url(request, timeout=None):
            self.calls.append(request)
            if isinstance(response, Exception):
                raise response
            return _FakeResponse(response)

        upstream.urlopen = open_url

    def test_models_url_normalises_the_base(self):
        self.assertEqual(
            upstream.models_url("https://example.test/v1/"), "https://example.test/v1/models"
        )
        self.assertEqual(
            upstream.models_url("https://example.test/v1/models"),
            "https://example.test/v1/models",
        )

    def test_models_url_rejects_a_useless_base(self):
        for bad in ("", "   ", "example.com/v1", "ftp://example.test/v1"):
            with self.assertRaises(ValueError):
                upstream.models_url(bad)

    def test_fetch_sends_the_bearer_key_and_de_dups(self):
        self.fake(json.dumps({"data": [{"id": "a-model"}, {"id": "a-model"}, {"id": " b "}, {}]}))
        models = upstream.fetch_models("https://example.test/v1", "sk-abc")
        self.assertEqual([item["id"] for item in models], ["a-model", "b"])
        self.assertEqual(
            self.calls[0].get_header("Authorization"), "Bearer sk-abc"
        )
        self.assertTrue(self.calls[0].full_url.endswith("/models"))

    def test_fetch_translates_failures_into_readable_errors(self):
        self.fake(urllib.error.HTTPError("https://x.test/models", 401, "denied", {}, None))
        with self.assertRaises(ValueError) as caught:
            upstream.fetch_models("https://x.test/v1", "sk")
        self.assertIn("HTTP 401", str(caught.exception))

        self.fake(urllib.error.URLError("no route"))
        with self.assertRaises(ValueError) as caught:
            upstream.fetch_models("https://x.test/v1", "sk")
        self.assertIn("连不上上游", str(caught.exception))

    def test_fetch_rejects_junk_and_oversize(self):
        self.fake(b"<html>login</html>")
        with self.assertRaises(ValueError) as caught:
            upstream.fetch_models("https://x.test/v1", "sk")
        self.assertIn("不是 JSON", str(caught.exception))

        original = upstream.MAX_RESPONSE_BYTES
        upstream.MAX_RESPONSE_BYTES = 64
        self.addCleanup(setattr, upstream, "MAX_RESPONSE_BYTES", original)
        self.fake(json.dumps({"data": [{"id": "m"}]}) + " " * 120)
        with self.assertRaises(ValueError) as caught:
            upstream.fetch_models("https://x.test/v1", "sk")
        self.assertIn("过大", str(caught.exception))

        self.fake(json.dumps({"object": "list"}))
        with self.assertRaises(ValueError) as caught:
            upstream.fetch_models("https://x.test/v1", "sk")
        self.assertIn("没有给出模型列表", str(caught.exception))

    def test_fetch_caps_the_list(self):
        original = upstream.MAX_MODELS
        upstream.MAX_MODELS = 2
        self.addCleanup(setattr, upstream, "MAX_MODELS", original)
        self.fake(json.dumps({"data": [{"id": "m%d" % i} for i in range(10)]}))
        self.assertEqual(len(upstream.fetch_models("https://x.test/v1", "sk")), 2)


class WebModelServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        silence_ambient_credentials(self)
        self.keys = self.root / "keys.json"
        self.keys.write_text(
            json.dumps(
                {
                    "OPENAI_API_KEY": "sk-file-secret-1234",
                    "OPENAI_BASE_URL": "https://stored.test/v1",
                    "MODEL_NAME": "qwen-plus",
                }
            ),
            encoding="utf-8",
        )
        os.environ[CONFIG_PATH_ENV] = str(self.keys)
        self.addCleanup(os.environ.pop, CONFIG_PATH_ENV, None)
        store, runtime = make_runtime(self.root)
        runtime.select_workspace(str(self.root))
        self.runtime = runtime
        self.store = store
        self.service = create_application_service(runtime)
        self.adapter = WebAdapter(self.service, data_dir=self.root).start()
        self.addCleanup(self.adapter.stop)
        self.base = "http://" + self.adapter.address
        # Stay on the offline client: a test run must not build a remote client
        # just because the credential file happens to hold a usable key.
        self.request("/api/settings", {"offline": True})
        self.fetched = []
        self.original_fetch = upstream.fetch_models
        self.addCleanup(setattr, upstream, "fetch_models", self.original_fetch)

    def request(self, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={
                "X-Companion-Token": self.adapter.token,
                "Content-Type": "application/json",
            },
            method="POST" if body is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8") or "{}")

    def serve_models(self, *ids):
        def fake(base_url, api_key, timeout=8.0):
            self.fetched.append((base_url, api_key))
            return [{"id": name} for name in ids]

        upstream.fetch_models = fake

    def test_settings_advertises_the_window_catalog(self):
        payload = self.request("/api/settings")[1]
        service = payload["model_service"]
        self.assertEqual(service["base_url"], "https://stored.test/v1")
        self.assertEqual(service["catalog"]["qwen-plus"], 131072)
        self.assertEqual(service["current"]["catalog_window"], 131072)

    def test_fetch_labels_each_window_by_where_it_came_from(self):
        self.request("/api/settings", {"model_contexts": {"private-model": 9000}})
        self.serve_models("qwen-plus", "gpt-4o-mini", "private-model", "mystery")
        status, payload = self.request(
            "/api/models/fetch", {"base_url": "https://other.test/v1", "api_key": "sk-other"}
        )
        self.assertEqual(status, 200)
        sources = {item["id"]: item["source"] for item in payload["models"]}
        self.assertEqual(
            sources,
            {"qwen-plus": "catalog", "gpt-4o-mini": "catalog",
             "private-model": "stored", "mystery": "manual"},
        )
        windows = {item["id"]: item["context_tokens"] for item in payload["models"]}
        self.assertEqual(windows["private-model"], 9000)
        self.assertEqual(windows["mystery"], 0)
        self.assertEqual(self.fetched, [("https://other.test/v1", "sk-other")])

    def test_the_stored_key_only_serves_its_own_endpoint(self):
        self.serve_models("qwen-plus")
        status, payload = self.request(
            "/api/models/fetch", {"base_url": "https://other.test/v1"}
        )
        self.assertEqual(status, 409)
        self.assertIn("没有对应的密钥", payload["error"])
        self.assertEqual(self.fetched, [])

        self.request("/api/models/fetch", {"base_url": "https://stored.test/v1/"})
        self.assertEqual(self.fetched[-1], ("https://stored.test/v1/", "sk-file-secret-1234"))

    def test_fetch_without_any_key_says_so(self):
        self.keys.write_text(json.dumps({"OPENAI_BASE_URL": "https://stored.test/v1"}))
        self.serve_models("qwen-plus")
        status, payload = self.request("/api/models/fetch", {})
        self.assertEqual(status, 409)
        self.assertIn("还没有填写 API Key", payload["error"])

    def test_save_writes_the_credential_file_and_the_settings_row(self):
        status, payload = self.request(
            "/api/model_service",
            {
                "base_url": "https://new.test/v1",
                "model": "gpt-4o-mini",
                "api_key": "sk-brand-new-9999",
                "model_contexts": {"gpt-4o-mini": 131072},
                "context_tokens": 16000,
            },
        )
        self.assertEqual(status, 200)
        stored = json.loads(self.keys.read_text(encoding="utf-8"))
        self.assertEqual(stored["OPENAI_API_KEY"], "sk-brand-new-9999")
        self.assertEqual(stored["OPENAI_BASE_URL"], "https://new.test/v1")
        self.assertEqual(stored["MODEL_NAME"], "gpt-4o-mini")
        self.assertEqual(payload["api_key"]["masked"], app_settings.mask_secret("sk-brand-new-9999"))
        self.assertNotIn("sk-brand-new-9999", json.dumps(payload))
        # The secret stays in the file: the database row holds only settings.
        self.assertNotIn("sk-brand-new-9999", json.dumps(self.store.load_settings()))
        self.assertEqual(self.store.load_settings()["model"], "gpt-4o-mini")
        self.assertEqual(self.runtime.max_context_chars, token_tools.tokens_to_chars(16000))
        self.assertEqual(self.runtime.config.base_url, "https://new.test/v1")

    def test_only_touched_rows_reach_the_window_table(self):
        self.request("/api/settings", {"model_contexts": {"old-model": 12000}})
        self.request(
            "/api/model_service",
            {
                "base_url": "https://stored.test/v1",
                "model": "qwen-plus",
                "model_contexts": {"qwen-plus": 131072},
            },
        )
        self.assertEqual(
            self.store.load_settings()["model_contexts"],
            {"old-model": 12000, "qwen-plus": 131072},
        )

    def test_nothing_selected_leaves_the_model_alone(self):
        self.request("/api/settings", {"model": "gpt-4o-mini"})
        status, payload = self.request(
            "/api/model_service", {"base_url": "https://stored.test/v1"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.store.load_settings()["model"], "gpt-4o-mini")
        stored = json.loads(self.keys.read_text(encoding="utf-8"))
        self.assertEqual(stored["MODEL_NAME"], "qwen-plus")
        self.assertEqual(stored["OPENAI_API_KEY"], "sk-file-secret-1234")

    def test_a_blank_key_leaves_the_secret_on_disk(self):
        self.request("/api/model_service", {"base_url": "https://stored.test/v1", "model": "qwen-max"})
        stored = json.loads(self.keys.read_text(encoding="utf-8"))
        self.assertEqual(stored["OPENAI_API_KEY"], "sk-file-secret-1234")
        self.assertEqual(self.store.load_settings()["model"], "qwen-max")

    def test_save_is_refused_mid_run(self):
        self.service.active_run = lambda session_id=None: object()
        status, payload = self.request("/api/model_service", {"base_url": "https://late.test/v1"})
        self.assertEqual(status, 409)
        self.assertIn("还在进行", payload["error"])
        self.assertNotIn("late.test", self.keys.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
