"""LLM clients used by the agent."""

from dataclasses import dataclass, field
import json
import re
import uuid
from typing import Any, Dict, List, Optional

from .config import AgentConfig
from .messages import Message


@dataclass
class ToolCall:
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    call_id: str = field(default_factory=lambda: "call_" + uuid.uuid4().hex[:12])

    def as_openai_dict(self):
        return {
            "id": self.call_id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)


class LLMClient:
    """Small interface shared by remote and offline clients."""

    def chat(self, messages, tools=None) -> LLMResponse:
        raise NotImplementedError


class OfflineLLMClient(LLMClient):
    """Deterministic client so the framework can run without API credentials."""

    def chat(self, messages, tools=None) -> LLMResponse:
        user_text = ""
        for message in reversed(messages):
            if message.role == "tool":
                return LLMResponse(content=message.content)
            if message.role == "user":
                user_text = message.content.strip().strip("\"'“”‘’")
                break

        if not user_text:
            return LLMResponse(content="请输入一个问题。")

        if re.search(r"(几点|时间|日期)", user_text):
            return LLMResponse(
                tool_calls=[ToolCall(name="current_time", arguments={})]
            )

        expression = self._extract_expression(user_text)
        if expression:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        name="calculate",
                        arguments={"expression": expression},
                    )
                ]
            )

        return LLMResponse(
            content=(
                "当前运行在离线演示模式。已收到：{}\n"
                "配置 config/keys.json 后，Agent 会切换到远程大模型。"
            ).format(user_text)
        )

    @staticmethod
    def _extract_expression(text):
        normalized = text.strip().strip("\"'“”‘’")
        normalized = normalized.replace("×", "*").replace("÷", "/")
        normalized = normalized.replace("＝", "=")
        normalized = normalized.replace("，", ",").replace("。", "")
        normalized = re.sub(
            r"^(请|帮我|帮忙)?(计算|算一下|算算|求)?\s*", "",
            normalized,
        )
        normalized = re.sub(
            r"\s*(=|等于)?\s*(几|多少|是多少|等于几)?\s*[？?]?$",
            "",
            normalized,
        )
        normalized = normalized.strip(" ：:，。")
        if not normalized:
            return ""
        allowed = r"[\d\s\.\+\-\*/%\(\)]+"
        if re.fullmatch(allowed, normalized):
            return normalized
        number = r"(?:\d+(?:\.\d+)?|\([^()\n]+\))"
        candidate_pattern = number + r"(?:\s*[\+\-\*/%]\s*" + number + r")+"
        candidates = re.findall(candidate_pattern, normalized)
        candidates = [item.strip() for item in candidates if re.fullmatch(allowed, item)]
        return max(candidates, key=len) if candidates else ""


class RemoteLLMClient(LLMClient):
    def __init__(self, config: AgentConfig):
        try:
            from openai import OpenAI
        except ImportError as error:
            raise RuntimeError(
                "远程模式需要安装 openai：python -m pip install openai"
            ) from error

        self.config = config
        self.client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=60,
        )

    def chat(self, messages, tools=None) -> LLMResponse:
        request: Dict[str, Any] = {
            "model": self.config.model,
            "messages": [message.to_api_dict() for message in messages],
            "temperature": self.config.temperature,
        }
        if tools:
            request["tools"] = tools
            request["tool_choice"] = "auto"

        response = self.client.chat.completions.create(**request)
        message = response.choices[0].message
        parsed_calls = []
        for tool_call in message.tool_calls or []:
            try:
                arguments = json.loads(tool_call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            parsed_calls.append(
                ToolCall(
                    name=tool_call.function.name,
                    arguments=arguments,
                    call_id=tool_call.id,
                )
            )
        return LLMResponse(
            content=message.content or "",
            tool_calls=parsed_calls,
        )


def create_llm_client(config: AgentConfig, offline: bool = False) -> LLMClient:
    if offline or not config.can_use_remote_llm:
        return OfflineLLMClient()
    return RemoteLLMClient(config)
