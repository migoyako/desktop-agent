"""Streamable-HTTP MCP transport on top of urllib.

Unlike the stdio transport this one has no process to babysit: each call is a
POST, and the only piece of state that survives a request is the session id the
server hands back on ``initialize``. Servers that are not there, that answer in
HTML, or that stream an event we cannot parse all get a sentence, not a stack.
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from .jsonrpc import (
    MAX_MESSAGE_BYTES,
    McpError,
    McpProtocolError,
    McpTimeout,
    IdSequence,
    decode,
    encode,
    notification,
    request,
    result_of,
)

_ACCEPT = "application/json, text/event-stream"
_SESSION_HEADER = "Mcp-Session-Id"
_PROTOCOL_VERSION_HEADER = "MCP-Protocol-Version"
_READ_CHUNK = 64 * 1024


class HttpTransport:
    """One JSON-RPC call per POST, with an optional SSE body on the reply."""

    def __init__(self, url: str, headers=None, timeout=15.0, label=""):
        self.url = _checked_url(url)
        self.headers = _checked_headers(headers)
        self.timeout = float(timeout)
        self.label = label or urllib.parse.urlsplit(self.url).netloc or self.url
        self.session_id = ""
        self.protocol_version = ""
        self._ids = IdSequence()
        self._lock = threading.Lock()
        self._closed = False

    @property
    def connected(self) -> bool:
        return bool(self.url) and not self._closed

    @property
    def stderr_tail(self) -> str:
        return ""

    def describe(self) -> str:
        return "http {}".format(self.label)

    def connect(self) -> None:
        # Nothing to spawn; the handshake happens in request(), but a bad URL
        # must fail here so "连接" reports it rather than the first tool call.
        if self._closed:
            raise McpError("传输已关闭，请重新连接服务器。")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            session = self.session_id
        if not session:
            return
        # Best-effort "end this session"; a server that never supported it must
        # not turn a normal disconnect into an error the user has to read.
        try:
            self._post(b"", method="DELETE", expect_body=False)
        except McpError:
            pass

    def request(self, method: str, params=None, timeout=None) -> Any:
        if self._closed:
            raise McpError("传输已关闭，请重新连接服务器。", "mcp_not_connected")
        message_id = self._ids.next()
        payload = encode(request(message_id, method, params))
        body = self._post(payload, timeout=timeout)
        message = _pick_response(body, message_id, method)
        if method == "initialize" and isinstance(message.get("result"), dict):
            version = message["result"].get("protocolVersion")
            if isinstance(version, str):
                self.protocol_version = version[:64]
        return result_of(message, method)

    def notify(self, method: str, params=None) -> None:
        if self._closed:
            raise McpError("传输已关闭，请重新连接服务器。", "mcp_not_connected")
        payload = encode(notification(method, params))
        # A notification has no reply; 202 with an empty body is the normal case.
        self._post(payload, expect_body=False)

    # internals

    def _post(self, payload: bytes, timeout=None, expect_body=True, method="POST") -> bytes:
        limit = self.timeout if timeout is None else float(timeout)
        headers = dict(self.headers)
        headers["Content-Type"] = "application/json"
        headers["Accept"] = _ACCEPT
        if self.session_id:
            headers[_SESSION_HEADER] = self.session_id
        if self.protocol_version:
            headers[_PROTOCOL_VERSION_HEADER] = self.protocol_version
        prepared = urllib.request.Request(
            self.url,
            data=payload if method != "DELETE" else None,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(prepared, timeout=limit) as response:
                session = response.headers.get(_SESSION_HEADER)
                if session:
                    self.session_id = _clean_session(session)
                if method == "DELETE" or not expect_body:
                    return b""
                return _read_capped(response, limit)
        except urllib.error.HTTPError as error:
            raise _http_error(error)
        except urllib.error.URLError as error:
            reason = error.reason
            if isinstance(reason, TimeoutError):
                raise McpTimeout(
                    "连接服务器超时（{} 秒）。".format(_seconds(limit)),
                    detail=str(error),
                )
            raise McpError(
                "无法连接服务器：{}".format(reason),
                "mcp_unreachable",
                detail=str(error),
            )
        except TimeoutError:
            raise McpTimeout(
                "服务器在 {} 秒内没有回完整响应。".format(_seconds(limit)),
                detail="read timeout on {}".format(self.label),
            )
        except OSError as error:
            raise McpError(
                "与服务器通信失败：{}".format(error),
                "mcp_io_error",
                detail=str(error),
            )


def _read_capped(response, limit: float) -> bytes:
    buffer = bytearray()
    while True:
        try:
            chunk = response.read(min(_READ_CHUNK, MAX_MESSAGE_BYTES + 1 - len(buffer)))
        except (TimeoutError, OSError) as error:
            if isinstance(error, TimeoutError):
                raise McpTimeout("服务器在 {} 秒内没有回完整响应。".format(_seconds(limit)))
            raise McpError("读取服务器响应失败：{}".format(error), detail=str(error))
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > MAX_MESSAGE_BYTES:
            raise McpProtocolError(
                "响应 {} 字节，超过 {} 字节上限。".format(len(buffer), MAX_MESSAGE_BYTES)
            )
    return bytes(buffer)


def _pick_response(body: bytes, message_id: int, method: str) -> Dict[str, Any]:
    if not body.strip():
        raise McpError(
            "服务器对 {} 返回了空响应。".format(method), "mcp_empty_response"
        )
    candidates = _sse_data(body)
    if candidates is None:
        candidates = [body]
    last_error = None
    for raw in candidates:
        try:
            message = decode(raw)
        except McpError as error:
            last_error = error
            continue
        if "id" not in message:
            continue
        if str(message.get("id")) == str(message_id):
            return message
        last_error = McpProtocolError(
            "响应的 id 与请求不一致（期望 {}，得到 {}）。".format(
                message_id, message.get("id")
            )
        )
    if last_error is not None:
        raise last_error
    raise McpProtocolError("服务器响应里没有与请求配对的消息。")


def _sse_data(body: bytes) -> Optional[list]:
    """Return the ``data:`` payloads of an event-stream body, or None if it is not SSE."""
    text = body.decode("utf-8", "replace")
    if not _looks_like_sse(text):
        return None
    events = []
    current = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("data:"):
            current.append(stripped[5:].strip())
            continue
        if not stripped and current:
            events.append("\n".join(current))
            current = []
    if current:
        events.append("\n".join(current))
    return [event.encode("utf-8") for event in events if event]


def _looks_like_sse(text: str) -> bool:
    head = text[:2048].lstrip()
    return head.startswith("event:") or head.startswith("data:") or "\ndata:" in head


def _checked_url(url) -> str:
    text = str(url or "").strip()
    if not text:
        raise McpError("服务器地址为空。", "mcp_bad_url")
    parts = urllib.parse.urlsplit(text)
    if parts.scheme not in ("http", "https"):
        raise McpError(
            "服务器地址必须以 http:// 或 https:// 开头。", "mcp_bad_url"
        )
    if not parts.netloc:
        raise McpError("服务器地址缺少主机名。", "mcp_bad_url")
    return text


def _checked_headers(headers) -> Dict[str, str]:
    clean: Dict[str, str] = {}
    for key, value in dict(headers or {}).items():
        name = str(key)
        text = str(value)
        if _unsafe(name) or _unsafe(text):
            raise McpError(
                "服务器请求头包含非法字符：{}".format(name), "mcp_bad_header"
            )
        clean[name] = text
    return clean


def _unsafe(text: str) -> bool:
    if len(text) > 2048:
        return True
    return any(ord(char) < 32 or ord(char) == 127 for char in text)


def _clean_session(value: str) -> str:
    text = str(value).strip()
    if _unsafe(text) or len(text) > 256:
        return ""
    return text


def _http_error(error: urllib.error.HTTPError) -> McpError:
    code = error.code
    if code == 401:
        return McpError(
            "服务器要求鉴权（HTTP 401），请检查密钥配置。",
            "mcp_http_401",
            detail=str(error.reason),
        )
    if code == 403:
        return McpError("服务器拒绝访问（HTTP 403）。", "mcp_http_403")
    if code == 404:
        return McpError(
            "地址不存在或会话已失效（HTTP 404），请重新连接服务器。",
            "mcp_http_404",
        )
    if code == 405:
        return McpError("服务器不支持这种请求方式（HTTP 405）。", "mcp_http_405")
    if 400 <= code < 500:
        return McpError(
            "服务器返回错误 HTTP {}。".format(code),
            "mcp_http_{}".format(code),
            detail=_clip(str(error.reason)),
        )
    return McpError(
        "服务器出错，HTTP {}。".format(code),
        "mcp_http_{}".format(code),
        detail=_clip(str(error.reason)),
    )


def _clip(text: str, limit: int = 200) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _seconds(value) -> str:
    try:
        return "{:g}".format(round(float(value), 2))
    except (TypeError, ValueError):
        return str(value)
