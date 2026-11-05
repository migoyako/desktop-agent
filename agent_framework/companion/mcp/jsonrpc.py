"""JSON-RPC 2.0 framing for the MCP clients.

Hand-written on purpose (plan K2): the official SDK drags pydantic/anyio into a
shipped application, and the protocol surface we need is four methods. Every
message crossing this boundary comes from a machine the user added, so parsing
is strict and each rejection carries a sentence the user can act on.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

PROTOCOL_VERSION = "2025-06-18"

# One JSON message in either direction. A server that answers with more than
# this is either broken or trying to make us buffer it.
MAX_MESSAGE_BYTES = 4 * 1024 * 1024
# What we are willing to hand to the model (and therefore into the prompt) as
# one tool result. Anything past this is cut with a visible marker.
MAX_RESULT_CHARS = 8000
# tools/list is cursor-paginated; a server that never finishes paginating must
# not spin forever.
MAX_LIST_PAGES = 8

JSONRPC_VERSION = "2.0"

_ERROR_TEXT = {
    -32700: "返回的不是合法 JSON。",
    -32600: "请求不符合 JSON-RPC 规范。",
    -32601: "服务器说没有这个工具。",
    -32602: "参数不符合服务器声明的格式。",
    -32603: "服务器内部错误。",
}


class McpError(RuntimeError):
    """Base class; `reason` is always a sentence to show the user."""

    def __init__(self, reason: str, code: str = "mcp_error", detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.code = code
        self.detail = detail


class McpProtocolError(McpError):
    """Framing/shape problem on the wire — we could not read the answer."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason, "mcp_protocol_error", detail)


class McpRemoteError(McpError):
    """The server answered with a JSON-RPC error object."""

    def __init__(self, reason: str, error_code=None, detail: str = ""):
        super().__init__(reason, "mcp_remote_error", detail)
        self.error_code = error_code


class McpTimeout(McpError):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason, "mcp_timeout", detail)


class IdSequence:
    """Per-transport request ids, so a stale answer cannot be matched to us."""

    def __init__(self, start: int = 1):
        self._next = int(start)

    def next(self) -> int:
        value = self._next
        self._next += 1
        return value


def request(message_id: int, method: str, params=None) -> Dict[str, Any]:
    message: Dict[str, Any] = {
        "jsonrpc": JSONRPC_VERSION,
        "id": int(message_id),
        "method": method,
    }
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params=None) -> Dict[str, Any]:
    message: Dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "method": method}
    if params is not None:
        message["params"] = params
    return message


def initialize_params(client_name: str = "companion", version: str = "1") -> Dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": client_name, "version": version},
    }


def encode(message: Dict[str, Any]) -> bytes:
    try:
        text = json.dumps(message, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise McpProtocolError("请求无法编码成 JSON。", detail=_clip(str(error)))
    data = text.encode("utf-8")
    if len(data) > MAX_MESSAGE_BYTES:
        raise McpProtocolError(
            "请求体 {} 字节，超过 {} 字节上限。".format(len(data), MAX_MESSAGE_BYTES)
        )
    return data + b"\n"


def decode(raw) -> Dict[str, Any]:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if raw is None:
        raise McpProtocolError("服务器没有返回内容。")
    if len(raw) > MAX_MESSAGE_BYTES:
        raise McpProtocolError(
            "响应 {} 字节，超过 {} 字节上限。".format(len(raw), MAX_MESSAGE_BYTES)
        )
    try:
        message = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise McpProtocolError("响应不是 UTF-8 文本。")
    except ValueError as error:
        raise McpProtocolError("响应不是合法 JSON。", detail=_clip(str(error)))
    if isinstance(message, list):
        raise McpProtocolError("不支持批量响应：一次只能回一条消息。")
    if not isinstance(message, dict):
        raise McpProtocolError("响应不是 JSON 对象。")
    return message


def result_of(message: Dict[str, Any], method: str):
    """Return `result`, or raise a readable error for an error object."""
    if "error" in message:
        raise _remote_error(message.get("error"), method)
    result = message.get("result")
    if result is None:
        raise McpProtocolError(
            "服务器对 {} 既没有 result 也没有 error。".format(method)
        )
    if not isinstance(result, (dict, list)):
        raise McpProtocolError("服务器对 {} 返回了不是对象或数组的结果。".format(method))
    return result


def check(message: Dict[str, Any], message_id: int, method: str):
    """Validate the envelope of a response and return `result`."""
    if message.get("jsonrpc") != JSONRPC_VERSION:
        raise McpProtocolError(
            "响应缺少 jsonrpc 2.0 标记（来自 {}）。".format(method)
        )
    if "id" not in message:
        raise McpProtocolError("响应没有 id，无法与请求配对（来自 {}）。".format(method))
    if _as_int(message.get("id")) != int(message_id):
        raise McpProtocolError(
            "响应的 id 与请求不一致（期望 {}，得到 {}）。".format(
                message_id, message.get("id")
            )
        )
    return result_of(message, method)


def parse_tools(result) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Normalise a tools/list result.

    A single malformed entry does not take the whole server down: it is skipped
    and reported, because the other tools on that server are still real.
    """
    if not isinstance(result, dict):
        raise McpProtocolError("tools/list 的结果不是一个对象。")
    raw_tools = result.get("tools")
    if raw_tools is None:
        raise McpProtocolError("tools/list 的结果里没有 tools 字段。")
    if not isinstance(raw_tools, list):
        raise McpProtocolError("tools/list 的 tools 不是一个数组。")
    tools: List[Dict[str, Any]] = []
    issues: List[str] = []
    for index, item in enumerate(raw_tools):
        if not isinstance(item, dict):
            issues.append("第 {} 个工具不是对象，已跳过。".format(index + 1))
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            issues.append("第 {} 个工具没有可用的名称，已跳过。".format(index + 1))
            continue
        tools.append(item)
    return tools, issues


def call_text(result) -> Tuple[str, bool]:
    """Flatten a tools/call result into (text, is_error)."""
    if not isinstance(result, dict):
        raise McpProtocolError("tools/call 的结果不是一个对象。")
    parts = result.get("content")
    chunks: List[str] = []
    if parts is not None and not isinstance(parts, list):
        raise McpProtocolError("tools/call 的 content 不是一个数组。")
    for part in parts or []:
        if not isinstance(part, dict):
            chunks.append("[无法解析的内容片段]")
            continue
        kind = part.get("type")
        if kind == "text":
            text = part.get("text")
            chunks.append(text if isinstance(text, str) else "[文本片段缺少内容]")
        else:
            chunks.append("[{} 类型的返回，已省略其内容]".format(kind or "未知"))
    structured = result.get("structuredContent")
    if structured is not None and not chunks:
        chunks.append(_clip_json(structured))
    text = "\n".join(chunk for chunk in chunks if chunk)
    if not text:
        text = "（服务器返回了空结果）"
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "…（已截断，共 {} 字符）".format(len(text))
    return text, bool(result.get("isError"))


def _remote_error(error, method: str) -> McpRemoteError:
    if not isinstance(error, dict):
        return McpRemoteError(
            "服务器对 {} 返回了格式错误的 error 字段。".format(method),
            detail=_clip(str(error)),
        )
    code = error.get("code")
    message = error.get("message")
    text = message if isinstance(message, str) and message.strip() else ""
    known = _ERROR_TEXT.get(code)
    if text and known:
        reason = "{}（{}）".format(known, text)
    elif text:
        reason = "服务器报错：{}".format(text)
    elif known:
        reason = known
    else:
        reason = "服务器拒绝了这次调用（错误码 {}）。".format(
            code if code is not None else "未知"
        )
    detail = ""
    data = error.get("data")
    if data is not None:
        detail = _clip_json(data)
    return McpRemoteError(_clip(reason, 400), error_code=code, detail=detail)


def _as_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clip(text: str, limit: int = 300) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _clip_json(value) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return _clip(text, MAX_RESULT_CHARS)
