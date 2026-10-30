"""End-to-end workflow tests using a deterministic search backend."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_framework.companion.models import AgentMode, SearchResult
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService, WebSearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.llm import OfflineLLMClient


class StubSearch:
    def search(self, query, limit=5):
        self.last_query = query
        return [
            SearchResult(
                title="Example result",
                url="https://example.com/result",
                snippet="A short search summary.",
            )
        ]


class RuntimeWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.data = self.root / "data"
        self.store = CompanionStore(self.data / "companion.db")
        self.runtime = CompanionRuntime(
            store=self.store,
            llm_client=OfflineLLMClient(),
            search_service=SearchService(StubSearch()),
            data_dir=self.data,
            session_id="main",
            offline=True,
        )
        self.runtime.select_workspace(str(self.workspace))

    def tearDown(self):
        self.temp.cleanup()

    def test_switch_session_moves_history_and_services(self):
        self.store.add_message("main", "user", "主会话的问题", AgentMode.CHAT)
        self.store.add_message("other", "user", "另一个会话", AgentMode.WORK)
        self.assertTrue(self.runtime.switch_session("other"))
        self.assertEqual(self.runtime.session_id, "other")
        self.assertEqual(self.runtime.proactive_service.session_id, "other")
        self.assertEqual(self.runtime.work.session_id, "other")
        self.assertEqual(
            self.runtime.history()[-1]["content"],
            "另一个会话",
        )
        self.assertEqual(self.runtime.current_mode(), AgentMode.WORK)
        self.assertFalse(self.runtime.switch_session("missing"))
        self.assertEqual(self.runtime.session_id, "other")
        self.assertFalse(self.runtime.switch_session("other"))

    def test_switching_clears_the_target_unread_flag(self):
        self.store.add_message("other", "user", "另一个会话", AgentMode.CHAT)
        self.store.set_session_unread("other", True)

        self.assertTrue(self.runtime.switch_session("other"))

        rows = {
            row["id"]: row for row in self.store.list_session_summaries()
        }
        self.assertEqual(rows["other"]["unread"], 0)

    def test_rename_pin_and_draft_are_session_scoped(self):
        self.store.add_message("other", "user", "另一个会话", AgentMode.CHAT)
        self.assertTrue(self.runtime.rename_session("other", "新标题"))
        self.assertTrue(self.runtime.set_session_pinned("other", True))
        self.assertTrue(self.runtime.save_draft("other", "会话草稿"))

        rows = {
            row["id"]: row for row in self.runtime.session_summaries()
        }
        self.assertEqual(rows["other"]["title"], "新标题")
        self.assertTrue(rows["other"]["pinned_at"])
        self.assertEqual(self.runtime.draft("other"), "会话草稿")
        self.assertEqual(self.runtime.draft("main"), "")
        self.assertFalse(self.runtime.rename_session("other", "   "))

    def test_deleting_the_active_session_switches_to_the_newest_remaining(self):
        self.store.add_message("other", "user", "保留这个会话", AgentMode.WORK)
        self.assertTrue(self.runtime.delete_session("main"))

        self.assertEqual(self.runtime.session_id, "other")
        self.assertEqual(self.runtime.work.session_id, "other")
        self.assertIsNone(self.store.get_session_mode("main"))
        self.assertFalse(self.runtime.delete_session("main"))

    def test_deleting_the_last_session_creates_a_replacement(self):
        self.assertTrue(self.runtime.delete_session("main"))

        self.assertNotEqual(self.runtime.session_id, "main")
        self.assertIsNotNone(
            self.store.get_session_mode(self.runtime.session_id)
        )

    def test_chat_refuses_execution_tools(self):
        result = self.runtime.handle_message("搜索 Python Agent 并保存为笔记", AgentMode.CHAT)
        self.assertIsNone(result["proposal"])
        self.assertIn("工作", result["answer"])
        self.assertEqual(list(self.workspace.rglob("*.md")), [])

    def test_search_note_requires_confirmation_then_records_experience(self):
        result = self.runtime.handle_message(
            "搜索 Python Agent 并保存为笔记",
            AgentMode.WORK,
        )
        proposal = result["proposal"]
        self.assertIsNotNone(proposal)
        self.assertEqual(self.runtime.pending_proposals()[0].action_id, proposal.action_id)
        self.assertEqual(list(self.workspace.rglob("*.md")), [])
        confirmed = self.runtime.confirm_proposal(proposal.action_id)
        self.assertEqual(confirmed["receipt"].status, "completed")
        files = list(self.workspace.rglob("*.md"))
        self.assertEqual(len(files), 1)
        self.assertIn("Example result", files[0].read_text(encoding="utf-8"))
        self.assertEqual(len(self.runtime.experiences()), 1)
        self.assertTrue(
            any(memory.kind == "shared_experience" for memory in self.runtime.memories())
        )

    def test_cancel_never_writes(self):
        result = self.runtime.handle_message(
            "搜索 Python 并保存为笔记",
            AgentMode.WORK,
        )
        self.runtime.cancel_proposal(result["proposal"].action_id)
        self.assertEqual(list(self.workspace.rglob("*.md")), [])

    def test_tampered_content_is_rejected(self):
        result = self.runtime.handle_message(
            "搜索 Python 并保存为笔记",
            AgentMode.WORK,
        )
        proposal = result["proposal"]
        proposal.normalized_arguments = dict(proposal.normalized_arguments)
        proposal.normalized_arguments["content"] = "tampered"
        self.store.update_proposal_status(proposal.action_id, proposal.status)
        with patch.object(
            self.store,
            "get_proposal",
            return_value=proposal,
        ):
            failed = self.runtime.confirm_proposal(proposal.action_id)
        self.assertEqual(failed["receipt"].status, "failed")
        self.assertEqual(list(self.workspace.rglob("*.md")), [])

    def test_explicit_preference_is_recalled_and_can_be_forgotten(self):
        self.runtime.handle_message("请记住我喜欢简洁回答。", AgentMode.CHAT)
        memories = self.runtime.memories()
        self.assertTrue(memories)
        self.assertEqual(len(memories), 1)
        self.assertTrue(
            any("简洁回答" in memory.content for memory in memories)
        )
        memory = next(
            item for item in memories if "简洁回答" in item.content
        )
        self.runtime.forget_memory(memory.memory_id)
        updated = next(
            item for item in self.store.list_memories("main")
            if item.memory_id == memory.memory_id
        )
        self.assertEqual(updated.status, "forgotten")

    def test_affect_can_be_corrected(self):
        self.runtime.handle_message("我今天很难过，压力很大。", AgentMode.CHAT)
        self.assertIn(
            self.runtime.affect_snapshot()["user"]["mood_label"],
            ("low", "tired"),
        )
        self.runtime.correct_affect("neutral")
        self.assertEqual(
            self.runtime.affect_snapshot()["user"]["mood_label"],
            "neutral",
        )


class SearchParserTest(unittest.TestCase):
    def test_bing_rss_parser(self):
        payload = (
            b"<rss><channel><item><title>Title</title>"
            b"<link>https://example.com</link>"
            b"<description>Summary</description></item></channel></rss>"
        )
        from agent_framework.companion.search import parse_bing_rss

        results = parse_bing_rss(payload)
        self.assertEqual(results[0].title, "Title")
        self.assertEqual(results[0].url, "https://example.com")


if __name__ == "__main__":
    unittest.main()
