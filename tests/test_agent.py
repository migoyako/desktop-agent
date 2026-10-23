import re
import unittest

from agent_framework.agent import ChatAgent
from agent_framework.config import AgentConfig
from agent_framework.llm import OfflineLLMClient
from agent_framework.tools import build_default_tools


class AgentSmokeTest(unittest.TestCase):
    def setUp(self):
        self.agent = ChatAgent(
            llm_client=OfflineLLMClient(),
            tools=build_default_tools(),
            config=AgentConfig(offline=True),
        )

    def test_calculator_tool(self):
        answer = self.agent.ask("计算 12 * (3 + 4)")
        self.assertEqual(answer, "84")

    def test_quoted_calculator_input(self):
        answer = self.agent.ask('"计算 12 * (3 + 4)"')
        self.assertEqual(answer, "84")

    def test_natural_calculator_input(self):
        answer = self.agent.ask("1+1=几")
        self.assertEqual(answer, "2")

    def test_time_tool(self):
        answer = self.agent.ask("现在几点")
        self.assertRegex(answer, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_offline_fallback(self):
        answer = self.agent.ask("你好")
        self.assertIn("离线演示模式", answer)


if __name__ == "__main__":
    unittest.main()
