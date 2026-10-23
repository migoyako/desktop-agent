"""Agent loop: model reply, tool execution, and conversation memory."""

from typing import List

from .config import AgentConfig
from .llm import LLMClient, LLMResponse, ToolCall
from .messages import ConversationMemory
from .tools import ToolRegistry, build_default_tools


DEFAULT_SYSTEM_PROMPT = (
    "你是一个简洁、可靠的中文助理。"
    "需要精确计算或读取当前时间时，请调用已提供的工具。"
    "回答应直接、清楚，并在不确定时说明不确定性。"
)


class ChatAgent:
    def __init__(
        self,
        llm_client: LLMClient,
        tools=None,
        config=None,
        system_prompt=DEFAULT_SYSTEM_PROMPT,
    ):
        self.config = config or AgentConfig()
        self.llm_client = llm_client
        self.tools = tools or build_default_tools()
        self.memory = ConversationMemory(
            system_prompt=system_prompt,
            max_messages=self.config.max_history_messages,
        )

    def ask(self, user_text: str) -> str:
        user_text = user_text.strip()
        if not user_text:
            return "请输入内容。"

        self.memory.add_user(user_text)
        response = LLMResponse()
        for _ in range(self.config.max_tool_rounds + 1):
            response = self.llm_client.chat(
                self.memory.snapshot(),
                self.tools.as_openai_tools(),
            )
            if not response.tool_calls:
                answer = response.content or "模型没有返回文本。"
                self.memory.add_assistant(answer)
                return answer

            self.memory.add_assistant(
                response.content,
                tool_calls=[
                    tool_call.as_openai_dict()
                    for tool_call in response.tool_calls
                ],
            )
            for tool_call in response.tool_calls:
                result = self.tools.execute(
                    tool_call.name,
                    tool_call.arguments,
                )
                self.memory.add_tool_result(tool_call.call_id, result)

        answer = "达到工具调用上限，请换一种问法。"
        self.memory.add_assistant(answer)
        return answer

    def clear(self):
        self.memory.clear()

    def history(self):
        return self.memory.snapshot()

    @property
    def tool_names(self):
        return self.tools.names()
