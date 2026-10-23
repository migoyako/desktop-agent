"""OpenAI-compatible bridge around the local ChatAgent."""

import json
import threading
import time
import uuid
import re
from typing import Dict, Iterable, Optional

from .agent import ChatAgent
from .config import AgentConfig
from .llm import create_llm_client


MODEL_ID = "python-agent"

OPENCLAW_TIMESTAMP_PREFIX = re.compile(
    r"^\[[A-Z][a-z]{2} \d{4}-\d{2}-\d{2} \d{2}:\d{2} GMT[+-]\d{1,2}\]\s*"
)

OPENCLAW_RUNTIME_CONTEXT_BLOCK = re.compile(
    r"(?ms)^[ \t]*OpenClaw runtime context[^\r\n]*\r?\n"
    r".*?^[ \t]*End OpenClaw runtime context\.[ \t]*(?:\r?\n|$)"
)

OPENCLAW_INTERNAL_CONTEXT_BLOCK = re.compile(
    r"(?ms)^[ \t]*<<<BEGIN_OPENCLAW_INTERNAL_CONTEXT>>>[ \t]*(?:\r?\n|$)"
    r".*?^[ \t]*<<<END_OPENCLAW_INTERNAL_CONTEXT>>>[ \t]*(?:\r?\n|$)"
)


def strip_openclaw_runtime_context(prompt: str) -> str:
    cleaned = prompt
    while True:
        previous = cleaned
        cleaned = OPENCLAW_RUNTIME_CONTEXT_BLOCK.sub("", cleaned)
        cleaned = OPENCLAW_INTERNAL_CONTEXT_BLOCK.sub("", cleaned)
        if cleaned == previous:
            return cleaned.strip()


def normalize_agent_prompt(prompt: str) -> str:
    prompt = strip_openclaw_runtime_context(prompt)
    return OPENCLAW_TIMESTAMP_PREFIX.sub("", prompt.strip()).strip()


def extract_latest_user_message(messages: Iterable[Dict]) -> str:
    for message in reversed(list(messages)):
        if message.get("role") == "user":
            if (
                message.get("runtimeContext") is not None
                or message.get("runtimeContextCarrier") is True
            ):
                continue
            content = message.get("content", "")
            if isinstance(content, str):
                candidate = normalize_agent_prompt(content)
                if candidate:
                    return candidate
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(str(item.get("text", "")))
                candidate = normalize_agent_prompt("\n".join(parts))
                if candidate:
                    return candidate
    return ""


class AgentService:
    """Keeps one ChatAgent per local session id."""

    def __init__(self, offline: bool = False):
        self.offline = offline
        self._agents: Dict[str, ChatAgent] = {}
        self._lock = threading.Lock()

    def _build_agent(self) -> ChatAgent:
        config = AgentConfig.from_env()
        client = create_llm_client(config, offline=self.offline or config.offline)
        return ChatAgent(llm_client=client, config=config)

    def get_agent(self, session_id: str) -> ChatAgent:
        with self._lock:
            if session_id not in self._agents:
                self._agents[session_id] = self._build_agent()
            return self._agents[session_id]

    def chat(self, session_id: str, prompt: str) -> str:
        return self.get_agent(session_id).ask(normalize_agent_prompt(prompt))

    def completion_payload(
        self,
        model: str,
        session_id: str,
        answer: str,
    ) -> Dict:
        return {
            "id": "chatcmpl-" + uuid.uuid4().hex,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model or MODEL_ID,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": answer},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    @staticmethod
    def stream_chunks(model: str, answer: str) -> Iterable[str]:
        chunk_id = "chatcmpl-" + uuid.uuid4().hex
        created = int(time.time())
        first = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model or MODEL_ID,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": answer},
                    "finish_reason": None,
                }
            ],
        }
        last = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model or MODEL_ID,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield "data: " + json.dumps(first, ensure_ascii=False) + "\n\n"
        yield "data: " + json.dumps(last, ensure_ascii=False) + "\n\n"
        yield "data: [DONE]\n\n"

    def models_payload(self) -> Dict:
        return {
            "object": "list",
            "data": [
                {
                    "id": MODEL_ID,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "local",
                }
            ],
        }

    def get_session_id(self, body: Dict, headers: Optional[Dict] = None) -> str:
        session_id = body.get("user")
        if not session_id and headers:
            session_id = (
                headers.get("X-OpenClaw-Session-Id")
                or headers.get("X-Session-Id")
            )
        return str(session_id or "default")
