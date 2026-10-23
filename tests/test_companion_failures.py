"""Failure-path tests that must not create file proposals."""

import tempfile
import unittest
from pathlib import Path

from agent_framework.companion.models import AgentMode
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchError, SearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.llm import OfflineLLMClient


class FailingSearch:
    def search(self, query, limit=5):
        raise SearchError("network unavailable")


class SearchFailureTest(unittest.TestCase):
    def test_search_failure_creates_no_proposal_or_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = CompanionRuntime(
                CompanionStore(root / "db.sqlite"),
                OfflineLLMClient(),
                SearchService(FailingSearch()),
                root,
                offline=True,
            )
            result = runtime.handle_message(
                "搜索失败场景并保存为笔记",
                AgentMode.WORK,
            )
            self.assertIsNone(result["proposal"])
            self.assertIn("network unavailable", result["answer"])
            self.assertEqual(runtime.pending_proposals(), [])
            self.assertEqual(list(root.rglob("*.md")), [])


if __name__ == "__main__":
    unittest.main()
