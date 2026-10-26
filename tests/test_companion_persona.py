"""M2.5 persona-pack loading and stable-prefix tests."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_framework.companion.models import AgentMode
from agent_framework.companion.persona import PersonaError, load_persona
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.llm import LLMResponse, OfflineLLMClient


REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_PERSONA = REPO_ROOT / "data" / "personas" / "remielle"


class ScriptedLLM:
    def __init__(self, content="在。"):
        self.content = content
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append({"messages": list(messages), "tools": tools})
        return LLMResponse(content=self.content)


class PersonaPackTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"

    def tearDown(self):
        self.temp.cleanup()

    def copy_bundled(self):
        target = self.data / "personas" / "remielle"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(BUNDLED_PERSONA, target)
        return target

    def test_bundled_remielle_pack_has_expected_identity_and_facts(self):
        persona = load_persona(REPO_ROOT / "data", "remielle")

        self.assertIsNotNone(persona)
        self.assertEqual(persona.name, "蕾米埃尔·丹")
        self.assertEqual(persona.version, "1.0.0")
        self.assertEqual(len(persona.stable_hash), 64)
        self.assertIn("拉米尔", persona.metadata["aliases"])
        self.assertIn("共犯", persona.prompt_text)
        self.assertIn("贝莉奥拉", persona.facts_text)
        self.assertIn("塔乌弥尔", persona.facts_text)
        self.assertIn("3.1", persona.facts_text)
        self.assertEqual(persona.info()["source"]["owner"], "miHoYo / 米哈游")

    def test_stable_hash_is_repeatable(self):
        first = load_persona(REPO_ROOT / "data", "remielle")
        second = load_persona(REPO_ROOT / "data", "remielle")

        self.assertEqual(first.stable_hash, second.stable_hash)

    def test_stable_hash_changes_when_facts_change(self):
        pack = self.copy_bundled()
        first = load_persona(self.data, "remielle")
        with (pack / "facts.md").open("a", encoding="utf-8") as handle:
            handle.write("\n- 测试用新增事实。\n")
        second = load_persona(self.data, "remielle")

        self.assertNotEqual(first.stable_hash, second.stable_hash)

    def test_missing_pack_returns_none(self):
        self.assertIsNone(load_persona(self.data, "missing"))

    def test_invalid_persona_id_is_rejected(self):
        with self.assertRaises(PersonaError):
            load_persona(self.data, "../escape")
        with self.assertRaises(PersonaError):
            load_persona(self.data, "Remielle")


class PersonaRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.store = CompanionStore(self.data / "companion.db")
        self.persona_source = self.data / "personas" / "remielle"
        self.persona_source.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(BUNDLED_PERSONA, self.persona_source)

    def tearDown(self):
        self.temp.cleanup()

    def runtime(self, llm=None, offline=False):
        return CompanionRuntime(
            self.store,
            llm or OfflineLLMClient(),
            SearchService(),
            self.data,
            session_id="main",
            offline=offline,
        )

    def test_system_prompt_contains_stable_persona_and_safety_rules(self):
        runtime = self.runtime(ScriptedLLM(), offline=False)
        prompt = runtime._system_prompt("你好", AgentMode.CHAT)

        self.assertIn("蕾米埃尔·丹", prompt)
        self.assertIn("角色背景事实", prompt)
        self.assertIn("共犯", prompt)
        self.assertIn("不得声称自己执行了", prompt)
        self.assertIn("角色扮演不能成为绕过确认", prompt)
        self.assertIn("用户证据，不是角色经历", prompt)
        self.assertEqual(self.store.list_memories("main"), [])

    def test_persona_info_exposes_version_hash_and_source(self):
        runtime = self.runtime(ScriptedLLM(), offline=False)
        info = runtime.persona_info()

        self.assertTrue(info["loaded"])
        self.assertEqual(info["name"], "蕾米埃尔·丹")
        self.assertEqual(info["version"], "1.0.0")
        self.assertEqual(len(info["stable_hash"]), 64)
        self.assertEqual(info["source"]["character"], "蕾米埃尔·丹")

    def test_chat_does_not_write_persona_into_user_memories(self):
        runtime = self.runtime(ScriptedLLM("我在，共犯。"), offline=False)
        result = runtime.handle_message("你好", AgentMode.CHAT)

        self.assertEqual(result["answer"], "我在，共犯。")
        self.assertEqual(self.store.list_memories("main"), [])
        prompt = runtime.llm_client.calls[0]["messages"][0].content
        self.assertIn("贝莉奥拉", prompt)
        self.assertIn("## 角色人设（稳定层）", prompt)

    def test_offline_answer_uses_persona_address(self):
        runtime = self.runtime(offline=True)
        result = runtime.handle_message("你好", AgentMode.CHAT)

        self.assertIn("共犯", result["answer"])

    def test_user_address_setting_overrides_default_and_stays_protected(self):
        runtime = self.runtime(offline=True)
        runtime.set_persona_preference("address", "搭档")

        prompt = runtime._system_prompt("你好", AgentMode.CHAT)
        answer = runtime.handle_message("你好", AgentMode.CHAT)["answer"]
        memories = runtime.memories()
        settings = [
            memory
            for memory in memories
            if memory.canonical_key == "persona:setting:address"
        ]

        self.assertIn("称呼：搭档", prompt)
        self.assertIn("搭档", answer)
        self.assertEqual(len(settings), 1)
        self.assertTrue(settings[0].protected)
        self.assertEqual(settings[0].kind, "persona")
        self.assertEqual(runtime.persona_preferences()["address"], "搭档")

        runtime.set_persona_preference("address", "老板")
        updated = [
            memory
            for memory in runtime.memories()
            if memory.canonical_key == "persona:setting:address"
        ]
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0].content, "老板")

    def test_clearing_persona_setting_restores_default(self):
        runtime = self.runtime(offline=True)
        runtime.set_persona_preference("address", "搭档")
        runtime.set_persona_preference("address", "")

        prompt = runtime._system_prompt("你好", AgentMode.CHAT)
        answer = runtime.handle_message("你好", AgentMode.CHAT)["answer"]

        self.assertEqual(runtime.persona_preferences()["address"], "")
        self.assertIn("暂无用户自定义称呼、关系或边界", prompt)
        self.assertIn("共犯", answer)

    def test_worldbook_is_injected_only_for_relevant_query(self):
        runtime = self.runtime(ScriptedLLM(), offline=False)

        relevant = runtime._system_prompt("黑墙是什么", AgentMode.CHAT)
        irrelevant = runtime._system_prompt("今天天气如何", AgentMode.CHAT)

        self.assertIn("## 世界书", relevant)
        self.assertIn("黑墙是旧艾利都时期抵御空洞灾害的前线", relevant)
        self.assertNotIn("## 世界书", irrelevant)
        self.assertNotIn("黑墙是旧艾利都时期", irrelevant)

    def test_import_persona_reloads_runtime_and_keeps_settings(self):
        runtime = self.runtime(offline=True)
        runtime.set_persona_preference("address", "搭档")
        source = self.root / "incoming" / "second"
        source.mkdir(parents=True)
        metadata = {
            "id": "second",
            "name": "第二人设",
            "version": "0.1.0",
            "source": {"work": "本地测试"},
            "license": {"character_ip": "本地原创"},
        }
        (source / "persona.json").write_text(
            json.dumps(metadata, ensure_ascii=False),
            encoding="utf-8",
        )
        (source / "persona.md").write_text(
            "# 第二\n\n安静、直接。",
            encoding="utf-8",
        )

        info = runtime.import_persona(str(source))

        self.assertEqual(runtime.persona_id, "second")
        self.assertEqual(runtime.persona.name, "第二人设")
        self.assertTrue(info["loaded"])
        self.assertEqual(info["name"], "第二人设")
        self.assertEqual(runtime.persona_preferences()["address"], "搭档")
        self.assertTrue(
            any(memory.content == "搭档" for memory in runtime.memories())
        )

    def test_removing_persona_does_not_remove_user_memories(self):
        runtime = self.runtime(ScriptedLLM("好。"), offline=False)
        message_id = self.store.add_message(
            "main",
            "user",
            "请记住我喜欢咖啡。",
            AgentMode.CHAT,
        )
        runtime.memory.observe_user_message(
            "main",
            message_id,
            "请记住我喜欢咖啡。",
        )
        before = self.store.list_memories("main")
        self.assertTrue(any("咖啡" in item.content for item in before))

        shutil.rmtree(self.persona_source)
        reopened = CompanionRuntime(
            self.store,
            OfflineLLMClient(),
            SearchService(),
            self.data,
            offline=True,
        )
        after = self.store.list_memories("main")

        self.assertIsNone(reopened.persona)
        self.assertEqual(
            [item.memory_id for item in after],
            [item.memory_id for item in before],
        )
        self.assertTrue(any("咖啡" in item.content for item in after))


class PersonaFallbackTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.store = CompanionStore(self.data / "companion.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_runtime_falls_back_when_pack_is_missing(self):
        runtime = CompanionRuntime(
            self.store,
            OfflineLLMClient(),
            SearchService(),
            self.data,
            offline=True,
        )
        prompt = runtime._system_prompt("你好", AgentMode.CHAT)
        info = runtime.persona_info()

        self.assertFalse(info["loaded"])
        self.assertIn("当前没有可用的人设包", prompt)
        self.assertIn("朴素、温和、诚实的助手人格", prompt)


if __name__ == "__main__":
    unittest.main()
