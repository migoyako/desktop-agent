"""M7 tests for SKILL.md packages, meta-tools and audit logging."""

import tempfile
import unittest
from pathlib import Path

from agent_framework.companion.models import AgentMode, RiskLevel, SearchResult
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.skills import (
    MAX_REFERENCE_CHARS,
    SkillError,
    SkillLibrary,
    SkillManifestError,
    parse_manifest,
)
from agent_framework.companion.store import CompanionStore, SCHEMA_VERSION
from agent_framework.llm import LLMResponse, ToolCall


TOOL_RISKS = {
    "current_time": RiskLevel.SAFE,
    "read_tool_result": RiskLevel.SAFE,
    "search_web": RiskLevel.NETWORK,
    "write_note": RiskLevel.FS_WRITE,
}


class CountingSearch:
    def __init__(self):
        self.calls = []

    def search(self, query, limit=5):
        self.calls.append(query)
        return [
            SearchResult(
                title="Example result",
                url="https://example.com/result",
                snippet="A short search summary.",
            )
        ]


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append({"messages": list(messages), "tools": tools})
        if not self.responses:
            raise AssertionError("unexpected LLM call")
        return self.responses.pop(0)


def skill_text(
    name,
    tools=(),
    effect="read",
    modes=None,
    references=None,
    body="# 流程\n按步骤执行。",
    description="演示技能",
):
    lines = [
        "---",
        "name: {}".format(name),
        "description: {}".format(description),
        "tools: [{}]".format(", ".join(tools)),
        "effectKind: {}".format(effect),
    ]
    if modes is not None:
        lines.append("modes: [{}]".format(", ".join(modes)))
    if references:
        lines.append("references: [{}]".format(", ".join(references)))
    lines.append("---")
    lines.append(body)
    return "\n".join(lines) + "\n"


class SkillTestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.search_backend = CountingSearch()

    def tearDown(self):
        self.temp.cleanup()

    def write_raw_skill(self, name, text, refs=None):
        directory = self.data / "skills" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "SKILL.md").write_text(text, encoding="utf-8")
        if refs:
            reference_dir = directory / "references"
            reference_dir.mkdir()
            for filename, content in refs.items():
                (reference_dir / filename).write_text(
                    content,
                    encoding="utf-8",
                )
        return directory

    def write_skill(self, name, **kwargs):
        refs = kwargs.pop("refs", None)
        return self.write_raw_skill(
            name,
            skill_text(name, **kwargs),
            refs=refs,
        )

    def runtime(self, llm=None):
        return CompanionRuntime(
            CompanionStore(self.data / "companion.db"),
            llm or ScriptedLLM([]),
            SearchService(self.search_backend),
            self.data,
            offline=False,
        )


class SkillManifestTest(SkillTestBase):
    def test_inline_and_block_lists_parse(self):
        manifest = parse_manifest(
            "demo",
            skill_text(
                "demo",
                tools=("current_time", "search_web"),
                effect="network",
                references=("guide.md",),
            ),
        )
        self.assertEqual(manifest.tools, ("current_time", "search_web"))
        self.assertEqual(manifest.references, ("guide.md",))
        self.assertIsNone(manifest.modes)
        self.assertIn("按步骤执行", manifest.body)

        block = "\n".join(
            [
                "---",
                "name: demo2",
                "description: 块列表",
                "tools:",
                "  - current_time",
                "effectKind: read",
                "modes:",
                "  - chat",
                "---",
                "正文",
            ]
        )
        parsed = parse_manifest("demo2", block)
        self.assertEqual(parsed.tools, ("current_time",))
        self.assertEqual(parsed.modes, ("chat",))

    def test_invalid_manifests_are_rejected(self):
        cases = [
            ("demo", "# no frontmatter"),
            (
                "demo",
                skill_text("other", tools=("current_time",)),
            ),
            (
                "demo",
                skill_text("demo", effect="shell"),
            ),
            (
                "demo",
                "---\nname: demo\ndescription: x\ntools: "
                "[current_time]\neffectKind: read\nunknown: 1\n---\nbody",
            ),
            (
                "demo",
                "---\nname: demo\ndescription: x\ntools: "
                "[current_time]\neffectKind: read\n---\n   ",
            ),
        ]
        for skill_id, text in cases:
            with self.subTest(text=text[:40]):
                with self.assertRaises(SkillManifestError):
                    parse_manifest(skill_id, text)

    def test_reference_names_must_be_flat_text_files(self):
        with self.assertRaises(SkillManifestError):
            parse_manifest(
                "demo",
                skill_text("demo", references=("../escape.md",)),
            )
        with self.assertRaises(SkillManifestError):
            parse_manifest(
                "demo",
                skill_text("demo", references=("image.png",)),
            )

    def test_oversized_body_is_rejected(self):
        with self.assertRaises(SkillManifestError):
            parse_manifest(
                "demo",
                skill_text("demo", body="长" * 8001),
            )


class SkillLibraryTest(SkillTestBase):
    def scan(self):
        return SkillLibrary(self.data / "skills").scan(TOOL_RISKS)

    def test_risk_filters_available_modes(self):
        self.write_skill("time-tool", tools=("current_time",))
        self.write_skill("web-tool", tools=("search_web",), effect="network")
        self.write_skill(
            "writer",
            tools=("write_note",),
            effect="write",
        )

        skills, issues = self.scan()
        self.assertEqual(issues, [])
        by_name = {skill.name: skill for skill in skills}
        self.assertEqual(
            by_name["time-tool"].modes,
            (
                AgentMode.CHAT,
                AgentMode.WORK,
                AgentMode.EXECUTE,
                AgentMode.QUIET,
            ),
        )
        self.assertEqual(
            by_name["web-tool"].modes,
            (AgentMode.WORK, AgentMode.EXECUTE),
        )
        self.assertEqual(
            by_name["writer"].modes,
            (AgentMode.WORK, AgentMode.EXECUTE),
        )

    def test_unauthorized_tool_rejects_skill(self):
        self.write_skill(
            "unknown",
            tools=("send_email",),
            effect="external",
        )
        self.write_skill(
            "too-risky",
            tools=("search_web",),
            effect="read",
        )
        skills, issues = self.scan()

        self.assertEqual(skills, [])
        reasons = {issue.skill_id: issue.reason for issue in issues}
        self.assertIn("未授权工具", reasons["unknown"])
        self.assertIn("风险高于", reasons["too-risky"])

    def test_explicit_mode_denied_by_risk_rejects_skill(self):
        self.write_skill(
            "chat-writer",
            tools=("write_note",),
            effect="write",
            modes=("chat",),
        )
        skills, issues = self.scan()
        self.assertEqual(skills, [])
        self.assertIn("声明模式", issues[0].reason)

    def test_missing_reference_rejects_skill(self):
        self.write_skill("demo", references=("missing.md",))
        skills, issues = self.scan()
        self.assertEqual(skills, [])
        self.assertIn("不存在", issues[0].reason)

    def test_reference_read_is_bounded(self):
        content = "甲" * 20 + "乙" * 20
        self.write_skill(
            "demo",
            tools=("current_time",),
            references=("guide.md",),
            refs={"guide.md": content},
        )
        skills, issues = self.scan()
        self.assertEqual(issues, [])
        skill = skills[0]
        library = SkillLibrary(self.data / "skills")

        first = library.read_reference(
            skill,
            "guide.md",
            offset=0,
            limit=10,
        )
        self.assertIn("字符 0-10 / 40", first)
        self.assertIn("甲" * 10, first)
        self.assertIn("继续用 read_skill_reference", first)
        self.assertNotIn("甲" * 11, first)

        tail = library.read_reference(
            skill,
            "references/guide.md",
            offset=35,
            limit=MAX_REFERENCE_CHARS,
        )
        self.assertIn("乙" * 5, tail)
        with self.assertRaises(SkillError):
            library.read_reference(skill, "guide.md", offset=99)
        with self.assertRaises(SkillError):
            library.read_reference(skill, "../SKILL.md")

    def test_overlong_reference_rejected_at_load(self):
        self.write_skill(
            "demo",
            references=("big.md",),
            refs={"big.md": "x" * (256 * 1024 + 1)},
        )
        skills, issues = self.scan()
        self.assertEqual(skills, [])
        self.assertIn("超过", issues[0].reason)


class SkillRuntimeTest(SkillTestBase):
    def test_skill_is_picked_up_without_code_change(self):
        runtime = self.runtime()
        self.assertNotIn(
            "invoke_skill",
            runtime.tool_names(AgentMode.WORK),
        )

        self.write_skill("time-tool", tools=("current_time",))

        self.assertIn("invoke_skill", runtime.tool_names(AgentMode.WORK))
        self.assertIn(
            "read_skill_reference",
            runtime.tool_names(AgentMode.WORK),
        )
        status = runtime.skills_status(AgentMode.WORK)
        self.assertEqual(status["available"][0]["name"], "time-tool")
        self.assertEqual(status["issues"], [])

    def test_write_skill_is_hidden_from_chat_mode(self):
        self.write_skill(
            "writer",
            tools=("write_note",),
            effect="write",
        )
        runtime = self.runtime()

        self.assertNotIn("invoke_skill", runtime.tool_names(AgentMode.CHAT))
        self.assertIn("invoke_skill", runtime.tool_names(AgentMode.WORK))
        self.assertEqual(
            runtime.skills_status(AgentMode.CHAT)["available"],
            [],
        )

    def test_invoke_returns_body_and_audits(self):
        self.write_skill(
            "time-tool",
            tools=("current_time",),
            references=("guide.md",),
            refs={"guide.md": "参考内容"},
        )
        runtime = self.runtime()
        registry = runtime._build_tool_registry(AgentMode.WORK, None)

        result = registry.execute(
            "invoke_skill",
            {"skill": "time-tool"},
            AgentMode.WORK,
        )

        self.assertTrue(result.ok)
        self.assertIn("[skill:time-tool]", result.content)
        self.assertIn("按步骤执行", result.content)
        self.assertIn("guide.md", result.content)
        self.assertEqual(result.data["tools"], ["current_time"])
        audit = runtime.skill_audits()
        self.assertEqual(audit[0]["action"], "invoke")
        self.assertEqual(audit[0]["outcome"], "ok")

    def test_unknown_skill_is_denied_and_audited(self):
        self.write_skill("time-tool", tools=("current_time",))
        runtime = self.runtime()
        registry = runtime._build_tool_registry(AgentMode.WORK, None)

        result = registry.execute(
            "invoke_skill",
            {"skill": "not-installed"},
            AgentMode.WORK,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "skill_not_available")
        audit = runtime.skill_audits()
        self.assertEqual(audit[0]["skill_name"], "not-installed")
        self.assertEqual(audit[0]["outcome"], "denied")

    def test_reference_meta_tool_reads_declared_file(self):
        self.write_skill(
            "time-tool",
            tools=("current_time",),
            references=("guide.md",),
            refs={"guide.md": "一二三四五六七八九十"},
        )
        runtime = self.runtime()
        registry = runtime._build_tool_registry(AgentMode.WORK, None)
        registry.execute(
            "invoke_skill",
            {"skill": "time-tool"},
            AgentMode.WORK,
        )

        result = registry.execute(
            "read_skill_reference",
            {"skill": "time-tool", "reference": "guide.md", "limit": 4},
            AgentMode.WORK,
        )
        self.assertTrue(result.ok)
        self.assertIn("一二三四", result.content)
        self.assertNotIn("五六", result.content)

        denied = registry.execute(
            "read_skill_reference",
            {"skill": "time-tool", "reference": "other.md"},
            AgentMode.WORK,
        )
        self.assertFalse(denied.ok)
        self.assertEqual(denied.code, "reference_not_declared")

        audit = runtime.skill_audits()
        actions = [(row["action"], row["outcome"]) for row in audit]
        self.assertIn(("reference", "ok"), actions)
        self.assertIn(("reference", "denied"), actions)

    def test_invalid_skill_never_registers_meta_tools(self):
        self.write_skill(
            "too-risky",
            tools=("search_web",),
            effect="read",
        )
        runtime = self.runtime()

        self.assertNotIn(
            "invoke_skill",
            runtime.tool_names(AgentMode.WORK),
        )
        status = runtime.skills_status(AgentMode.WORK)
        self.assertEqual(status["available"], [])
        self.assertEqual(status["all"], [])
        self.assertEqual(len(status["issues"]), 1)
        self.assertIn("风险高于", status["issues"][0]["reason"])

    def test_invoked_skill_limits_tools_for_the_turn(self):
        self.write_skill("time-tool", tools=("current_time",))
        llm = ScriptedLLM(
            [
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            name="invoke_skill",
                            arguments={"skill": "time-tool"},
                        )
                    ]
                ),
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            name="search_web",
                            arguments={"query": "越权搜索"},
                        )
                    ]
                ),
                LLMResponse(content="技能流程已结束。"),
            ]
        )
        runtime = self.runtime(llm)

        result = runtime.handle_message("按技能流程处理", AgentMode.WORK)

        self.assertIn("技能流程已结束", result["answer"])
        self.assertEqual(self.search_backend.calls, [])
        tool_messages = [
            message
            for message in llm.calls[-1]["messages"]
            if message.role == "tool"
        ]
        self.assertTrue(
            any("只允许调用" in message.content for message in tool_messages),
            [message.content for message in tool_messages],
        )
        audit = runtime.skill_audits()
        denied = [
            row
            for row in audit
            if row["action"] == "tool" and row["outcome"] == "denied"
        ]
        self.assertEqual(denied[0]["detail"], "search_web")

    def test_turn_scope_does_not_leak_to_next_turn(self):
        self.write_skill("time-tool", tools=("current_time",))
        llm = ScriptedLLM(
            [
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            name="invoke_skill",
                            arguments={"skill": "time-tool"},
                        )
                    ]
                ),
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            name="search_web",
                            arguments={"query": "第一轮越权"},
                        )
                    ]
                ),
                LLMResponse(content="第一轮结束。"),
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            name="search_web",
                            arguments={"query": "第二轮正常"},
                        )
                    ]
                ),
                LLMResponse(content="第二轮结束。"),
            ]
        )
        runtime = self.runtime(llm)

        runtime.handle_message("第一轮", AgentMode.WORK)
        second = runtime.handle_message("第二轮", AgentMode.WORK)

        self.assertIn("第二轮结束", second["answer"])
        self.assertEqual(self.search_backend.calls, ["第二轮正常"])

    def test_audit_persists_across_store_reopen(self):
        self.write_skill("time-tool", tools=("current_time",))
        runtime = self.runtime()
        registry = runtime._build_tool_registry(AgentMode.WORK, None)
        registry.execute(
            "invoke_skill",
            {"skill": "time-tool"},
            AgentMode.WORK,
        )

        reopened = CompanionStore(self.data / "companion.db")
        audits = reopened.list_skill_audits("main")
        self.assertEqual(audits[0]["skill_name"], "time-tool")

    def test_migration_v7_creates_skill_audit(self):
        database = self.data / "legacy.db"
        store = CompanionStore(database)
        with store._connection() as connection:
            connection.execute("DROP TABLE skill_audit")
            connection.execute("PRAGMA user_version = 7")

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
        self.assertIn("skill_audit", tables)
        reopened.ensure_session("main", AgentMode.CHAT)
        reopened.log_skill_call(
            "main",
            "demo",
            "invoke",
            AgentMode.CHAT,
            "ok",
        )
        self.assertEqual(len(reopened.list_skill_audits("main")), 1)


if __name__ == "__main__":
    unittest.main()
