"""OpenAI-compatible bridge forwarding OpenClaw messages to the running companion."""

import json
import time
import urllib.error
import urllib.request
import uuid
import re
from pathlib import Path
from typing import Dict, Iterable, Optional

from .companion.activation import read_endpoint


MODEL_ID = "python-agent"

# The companion endpoint waits 100s for a run; this client must outlive that
# answer but stay under OpenClaw's 120s provider timeout.
COMPANION_REQUEST_TIMEOUT = 110.0

COMPANION_NOT_RUNNING_ANSWER = (
    "数字分身没有在运行，请先启动桌面数字分身（python companion.py），再给我发消息。"
)

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
    """Forwards every message to the companion instance that owns the data dir.

    The companion runtime is the single execution kernel (D6): persona, memory,
    permissions and receipts stay shared with the desktop, and the turn is
    visible in the desktop UI. Messages land in the companion's current session.
    """

    def __init__(self, data_dir=None):
        root = Path(__file__).resolve().parents[1]
        self.data_dir = Path(data_dir) if data_dir else root / "data"

    def chat(self, session_id: str, prompt: str) -> str:
        text = normalize_agent_prompt(prompt)
        if not text:
            return "请输入内容。"
        endpoint = read_endpoint(self.data_dir)
        if endpoint is None:
            return COMPANION_NOT_RUNNING_ANSWER
        return self._forward(endpoint, text)

    def _forward(self, endpoint: Dict, text: str) -> str:
        request = urllib.request.Request(
            "http://127.0.0.1:{}/api/bridge/turn".format(endpoint["port"]),
            data=json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Companion-Token": endpoint["token"],
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=COMPANION_REQUEST_TIMEOUT
            ) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as error:
            try:
                detail = json.loads(error.read().decode("utf-8") or "{}")
            except ValueError:
                detail = {}
            finally:
                error.close()
            message = str(detail.get("error") or "").strip()
            return message or "数字分身拒绝了这条消息（HTTP {}）。".format(error.code)
        except (OSError, ValueError):
            # Stale endpoint file: the instance exited without cleaning up or
            # the port is gone. Same user-facing remedy as "not running".
            return COMPANION_NOT_RUNNING_ANSWER
        answer = str(payload.get("answer") or "").strip()
        return answer or COMPANION_NOT_RUNNING_ANSWER

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
