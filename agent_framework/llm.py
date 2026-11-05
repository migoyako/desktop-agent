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


@dataclass
class StreamEvent:
    kind: str = "delta"
    text: str = ""
    response: Optional[LLMResponse] = None
    error: str = ""


class LLMClient:
    """Small interface shared by remote and offline clients."""

    def chat(self, messages, tools=None) -> LLMResponse:
        raise NotImplementedError

    def stream_chat(self, messages, tools=None):
        response = self.chat(messages, tools)
        if response.content:
            yield StreamEvent(kind="delta", text=response.content)
        yield StreamEvent(kind="done", response=response)


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

    def _apply_sampling(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Only narrow the request when the user actually moved a slider.

        Upstreams differ on whether they accept top_p at all, so the default
        1.0 keeps the payload byte-identical to the pre-settings request.
        """
        max_tokens = int(getattr(self.config, "max_tokens", 0) or 0)
        if max_tokens > 0:
            request["max_tokens"] = max_tokens
        top_p = float(getattr(self.config, "top_p", 1.0) or 1.0)
        if 0 < top_p < 1:
            request["top_p"] = round(top_p, 4)
        return request

    def chat(self, messages, tools=None) -> LLMResponse:
        request: Dict[str, Any] = {
            "model": self.config.model,
            "messages": [message.to_api_dict() for message in messages],
            "temperature": self.config.temperature,
        }
        self._apply_sampling(request)
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

    def stream_chat(self, messages, tools=None):
        request: Dict[str, Any] = {
            "model": self.config.model,
            "messages": [message.to_api_dict() for message in messages],
            "temperature": self.config.temperature,
            "stream": True,
        }
        self._apply_sampling(request)
        if tools:
            request["tools"] = tools
            request["tool_choice"] = "auto"

        stream = None
        content_parts: List[str] = []
        call_parts: Dict[int, Dict[str, str]] = {}
        try:
            stream = self.client.chat.completions.create(**request)
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue
                if delta.content:
                    content_parts.append(delta.content)
                    yield StreamEvent(kind="delta", text=delta.content)
                for tool_delta in delta.tool_calls or []:
                    index = int(getattr(tool_delta, "index", 0) or 0)
                    entry = call_parts.setdefault(
                        index,
                        {"id": "", "name": "", "arguments": ""},
                    )
                    if tool_delta.id:
                        entry["id"] = tool_delta.id
                    function = getattr(tool_delta, "function", None)
                    if function is not None:
                        if function.name and not entry["name"]:
                            entry["name"] = function.name
                        if function.arguments:
                            entry["arguments"] += function.arguments

            parsed_calls = []
            for index in sorted(call_parts):
                entry = call_parts[index]
                if not entry["name"]:
                    continue
                try:
                    arguments = json.loads(entry["arguments"] or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                parsed_calls.append(
                    ToolCall(
                        name=entry["name"],
                        arguments=arguments,
                        call_id=entry["id"] or "call_" + uuid.uuid4().hex[:12],
                    )
                )
            yield StreamEvent(
                kind="done",
                response=LLMResponse(
                    content="".join(content_parts),
                    tool_calls=parsed_calls,
                ),
            )
        except Exception as error:
            yield StreamEvent(kind="error", error=str(error) or error.__class__.__name__)
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    pass


def create_llm_client(config: AgentConfig, offline: bool = False) -> LLMClient:
    if offline or not config.can_use_remote_llm:
        return OfflineLLMClient()
    return RemoteLLMClient(config)
