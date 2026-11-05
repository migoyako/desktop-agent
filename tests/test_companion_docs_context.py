"""M16 slice 2: the local library is a named context layer and a source.

The doc chain is kept apart from the memory chain on purpose: the scores are not
comparable, the budget is its own, the recall row carries its own scope, and an
answer that quotes the library has to be able to say which file and which chunk
it came from. These checks pin that plumbing at the runtime boundary; the
renderer side lives in the web tests.
"""

import json
import tempfile
import unittest
import urllib.request
from pathlib import Path

from agent_framework.companion.app import CompanionApplicationService
from agent_framework.companion.app.projection import build_sources
from agent_framework.companion.context import MAX_DOCS_CHARS
from agent_framework.companion.documents import (
    DocumentService,
    prompt_lines,
    source_items,
    usage_probes,
)
from agent_framework.companion.memory import memory_used_in_reply
from agent_framework.companion.models import AgentMode
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.companion.web import WebAdapter
from agent_framework.llm import LLMResponse, OfflineLLMClient

DEPLOY = (
    "# 部署手册\n"
    "## 前置检查\n"
    "部署前先确认数据库备份已经完成，再执行 migrate。\n"
    "## 回滚步骤\n"
    "回滚执行 rollback.sh，并把版本号写回上一个标签。\n"
)
QUOTED_ANSWER = "回滚执行 rollback.sh，并把版本号写回上一个标签。"


class StubSearch:
    def search(self, query, limit=5):
        return []


class ScriptedLLM:
    """Answers from a queue so one turn can be replayed without a live model."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append({"messages": list(messages), "tools": tools})
        return LLMResponse(content=self.answers.pop(0) if self.answers else "没有内容。")


class DocContextTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.store = CompanionStore(self.data / "companion.db")
        self.documents = DocumentService(self.store, self.data)

    def tearDown(self):
        self.temp.cleanup()

    def runtime(self, answers=(), offline=False):
        llm = OfflineLLMClient() if offline else ScriptedLLM(answers)
        runtime = CompanionRuntime(
            store=self.store,
            llm_client=llm,
            search_service=SearchService(StubSearch()),
            data_dir=self.data,
            session_id="main",
            offline=offline,
            max_context_chars=6000,
        )
        runtime.select_workspace(str(self.workspace))
        return runtime

    def doc_rows(self):
        return [row for row in self.store.list_recall_logs("main") if row["scope"] == "docs"]

    # -- the layer itself ------------------------------------------------

    def test_doc_hits_are_a_layer_of_their_own_and_say_where_they_came_from(self):
        self.documents.ingest("deploy.md", DEPLOY.encode("utf-8"), persona_id="")
        runtime = self.runtime([QUOTED_ANSWER])

        runtime.handle_message("回滚 版本号 标签", AgentMode.CHAT)

        layers = runtime.context_info()["layers"]
        prompt = runtime.last_context.system
        self.assertGreater(layers["docs"], 0)
        self.assertIn("## 文档检索", prompt)
        self.assertIn("deploy.md · 第 3 块", prompt)
        self.assertIn("部署手册 > 回滚步骤", prompt)
        self.assertLessEqual(layers["docs"], MAX_DOCS_CHARS + 120)

    def test_the_docs_layer_has_its_own_budget_and_drops_from_the_tail(self):
        body = "\n".join(
            "## 第{}节\n回滚 版本号 标签 {}\n".format(index, "记录" * 380)
            for index in range(1, 15)
        )
        self.documents.ingest("long.md", ("# 长手册\n" + body).encode("utf-8"), persona_id="")
        runtime = self.runtime(["收到。"])
        query = "回滚 版本号 标签"

        lines = runtime._recall_docs(query)
        untrimmed = prompt_lines(
            self.documents.search(query, limit=runtime.memory_top_k, persona_id=None)
        )

        self.assertGreater(sum(len(line) + 1 for line in untrimmed), MAX_DOCS_CHARS)
        self.assertLess(len(lines), len(untrimmed))
        self.assertLessEqual(sum(len(line) + 1 for line in lines), MAX_DOCS_CHARS)
        self.assertEqual(lines[0], untrimmed[0])

    def test_an_empty_library_charges_the_docs_layer_nothing(self):
        runtime = self.runtime(["收到。"])

        runtime.handle_message("回滚 版本号 标签", AgentMode.CHAT)

        self.assertEqual([], runtime._recall_docs("回滚 版本号 标签"))
        self.assertEqual(0, runtime.context_info()["layers"]["docs"])
        self.assertEqual([], self.doc_rows())
        self.assertEqual([], runtime.last_doc_sources)

    def test_a_broken_document_chain_costs_only_its_own_layer(self):
        self.documents.ingest("deploy.md", DEPLOY.encode("utf-8"), persona_id="")
        seed = "请记住我们的回滚要点是先备份数据库。"
        message_id = self.store.add_message("main", "user", seed, AgentMode.CHAT)
        runtime = self.runtime(["好的，记下了。"])
        runtime.memory.observe_user_message("main", message_id, seed)

        def explode(*args, **kwargs):
            raise RuntimeError("索引坏了")

        runtime.documents.search = explode

        answer = runtime.handle_message("回滚 备份", AgentMode.CHAT)

        self.assertEqual("好的，记下了。", answer["answer"])
        layers = runtime.context_info()["layers"]
        self.assertEqual(0, layers["docs"])
        self.assertGreater(layers["memory"], 0)
        self.assertIn("相关记忆（用户证据", runtime.last_context.system)
        self.assertEqual([], self.doc_rows())

    def test_offline_turns_assemble_no_prompt_so_no_layer_is_charged(self):
        self.documents.ingest("deploy.md", DEPLOY.encode("utf-8"), persona_id="")
        runtime = self.runtime(offline=True)

        answer = runtime.handle_message("回滚 版本号 标签", AgentMode.CHAT)

        self.assertIn("离线模式", answer["answer"])
        self.assertEqual([], self.doc_rows())
        self.assertEqual([], runtime.last_doc_sources)
        self.assertIsNone(runtime.last_context)

    # -- recall_log ------------------------------------------------------

    def test_the_two_chains_log_one_recall_row_each(self):
        self.documents.ingest("deploy.md", DEPLOY.encode("utf-8"), persona_id="")
        seed = "请记住我们的回滚要点是把版本号写回上一个标签。"
        message_id = self.store.add_message("main", "user", seed, AgentMode.CHAT)
        runtime = self.runtime([QUOTED_ANSWER])
        runtime.memory.observe_user_message("main", message_id, seed)

        runtime.handle_message("回滚 版本号 标签", AgentMode.CHAT)

        scopes = {row["scope"] for row in self.store.list_recall_logs("main")}
        self.assertEqual({"memory", "docs"}, scopes)
        row = self.doc_rows()[0]
        self.assertTrue(row["fact_ids"].startswith('["dck_'))
        self.assertEqual(1, row["used_in_reply"])

    def test_an_answer_that_quotes_nothing_marks_the_docs_row_unused(self):
        self.documents.ingest("deploy.md", DEPLOY.encode("utf-8"), persona_id="")
        runtime = self.runtime(["今天天气不错，适合出去走走。"])

        runtime.handle_message("回滚 版本号 标签", AgentMode.CHAT)

        self.assertEqual(0, self.doc_rows()[0]["used_in_reply"])

    # -- the sources channel --------------------------------------------

    def test_a_document_source_names_the_file_the_chunk_and_the_original_path(self):
        self.documents.ingest("deploy.md", DEPLOY.encode("utf-8"), persona_id="")
        runtime = self.runtime([QUOTED_ANSWER])

        runtime.handle_message("回滚 版本号 标签", AgentMode.CHAT)
        sources = build_sources(runtime)

        # The 目录块 is a real hit and stays in the channel; the section that
        # carries the quoted sentence is the one this test pins.
        self.assertEqual(["document"] * len(sources), [s.source_kind for s in sources])
        self.assertEqual(
            {"deploy.md · 第 3 块", "deploy.md · 第 1 块"},
            {s.title for s in sources},
        )
        item = [s for s in sources if s.heading == "部署手册 > 回滚步骤"][0]
        self.assertEqual("deploy.md · 第 3 块", item.title)
        self.assertEqual(2, item.ordinal)
        self.assertEqual("", item.url)
        self.assertEqual("部署手册 > 回滚步骤 " + QUOTED_ANSWER, item.snippet)
        self.assertTrue(item.chunk_id and item.document_id)
        # The path is relative to the data dir and has to walk back to the text
        # the chunk was cut from — that is what 定位 means here.
        original = self.data / item.path
        self.assertTrue(original.is_file(), item.path)
        self.assertIn("rollback.sh", original.read_text(encoding="utf-8"))

    def test_documents_come_first_and_web_hits_keep_their_url(self):
        runtime = self.runtime(offline=True)
        runtime.last_doc_sources = [
            {
                "title": "deploy.md · 第 3 块",
                "heading": "部署手册 > 回滚步骤",
                "snippet": "回滚执行 rollback.sh",
                "path": "docs/doc_x/deploy.md",
                "document_id": "doc_x",
                "chunk_id": "dck_x",
                "ordinal": 2,
            }
        ]
        runtime.last_tool_sources = [
            {"title": "", "url": "https://example.com/a", "snippet": "摘录"},
            {"title": "只有标题", "url": "", "snippet": ""},
            {"title": "", "url": "", "snippet": ""},
        ]

        sources = build_sources(runtime)

        self.assertEqual(["document", "web", "web"], [s.source_kind for s in sources])
        self.assertEqual("deploy.md · 第 3 块", sources[0].title)
        self.assertEqual("https://example.com/a", sources[1].title)
        self.assertEqual("https://example.com/a", sources[1].url)
        self.assertEqual("只有标题", sources[2].title)
        self.assertEqual("", sources[2].path)

    def test_the_jump_to_source_action_survives_a_document_only_turn(self):
        self.documents.ingest("deploy.md", DEPLOY.encode("utf-8"), persona_id="")
        runtime = self.runtime([QUOTED_ANSWER])
        service = CompanionApplicationService(runtime)

        self.assertEqual(0, service.message_actions().source_id)

        runtime.handle_message("回滚 版本号 标签", AgentMode.CHAT)
        actions = service.message_actions()

        self.assertNotEqual(0, actions.source_id)

    def test_the_usage_probe_is_short_enough_to_be_compared_at_all(self):
        self.documents.ingest("deploy.md", DEPLOY.encode("utf-8"), persona_id="")
        hits = self.documents.search("回滚 版本号 标签", limit=5, persona_id=None)

        probes = usage_probes(hits)

        self.assertEqual(2, len(hits))
        self.assertTrue(all(len(probe) < 200 for probe in probes))
        self.assertTrue(memory_used_in_reply(probes[0], QUOTED_ANSWER))
        toc_probe = [probe for probe in probes if probe.startswith("本文档目录")]
        self.assertTrue(toc_probe)
        self.assertFalse(memory_used_in_reply(toc_probe[0], QUOTED_ANSWER))


class DocSourceTransportTests(unittest.TestCase):
    """The sources channel reaches the page as data, not as display text."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        store = CompanionStore(self.root / "companion.db")
        self.runtime = CompanionRuntime(
            store=store,
            llm_client=OfflineLLMClient(),
            search_service=SearchService(StubSearch()),
            data_dir=self.root,
            session_id="main",
            offline=True,
        )
        self.runtime.select_workspace(str(self.root))
        self.service = CompanionApplicationService(self.runtime)
        self.adapter = WebAdapter(self.service, data_dir=self.root).start()

    def tearDown(self):
        self.adapter.stop()
        self.temp.cleanup()

    def bootstrap(self):
        request = urllib.request.Request(
            "http://{}/api/bootstrap".format(self.adapter.address),
            headers={"X-Companion-Token": self.adapter.token},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_the_bootstrap_payload_carries_the_document_pointer(self):
        self.runtime.documents.ingest("deploy.md", DEPLOY.encode("utf-8"), persona_id="")
        hits = self.runtime.documents.search(
            "回滚 版本号 标签", limit=5, persona_id=None
        )
        self.runtime.last_doc_sources = source_items(hits)
        self.runtime.last_tool_sources = [
            {"title": "测试来源", "url": "https://example.com", "snippet": "网页摘录"}
        ]

        sources = self.bootstrap()["sources"]

        self.assertEqual(["document", "document", "web"], [s["source_kind"] for s in sources])
        item = [s for s in sources if s["heading"] == "部署手册 > 回滚步骤"][0]
        self.assertEqual("deploy.md · 第 3 块", item["title"])
        self.assertEqual(2, item["ordinal"])
        self.assertTrue(item["chunk_id"].startswith("dck_"))
        self.assertTrue((self.root / item["path"]).is_file(), item["path"])
        self.assertIn("rollback.sh", item["snippet"])
        self.assertEqual("https://example.com", sources[-1]["url"])

    def test_a_sourceless_turn_sends_an_empty_channel_not_a_missing_key(self):
        self.assertEqual([], self.bootstrap()["sources"])


if __name__ == "__main__":
    unittest.main()
