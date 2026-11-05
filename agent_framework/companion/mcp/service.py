"""Connection lifecycle and tool discovery for the servers the user added.

A server is only ever reached because somebody pressed "连接" (slice 4 wires this
to the console); the turn, by contrast, reads :meth:`McpService.snapshot`, which
touches no network and therefore cannot make a model round-trip wait on a machine
that is down. Values that look like credentials stay out of every message we show.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .jsonrpc import (
    MAX_LIST_PAGES,
    McpError,
    McpProtocolError,
    McpRemoteError,
    McpTimeout,
    call_text,
    initialize_params,
    parse_tools,
)

TRANSPORT_STDIO = "stdio"
TRANSPORT_HTTP = "http"
TRANSPORTS = (TRANSPORT_STDIO, TRANSPORT_HTTP)

STATE_DOWN = "down"
STATE_CONNECTED = "connected"
STATE_FAILED = "failed"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# The fields that decide *which* endpoint a call lands on. A connection was opened
# against one of these; swapping them under a live socket would send traffic meant
# for B down the pipe we dialed for A.
ENDPOINT_FIELDS = ("transport", "command", "args", "cwd", "url")


def endpoint_identity(spec) -> Tuple[str, ...]:
    values = []
    for name in ENDPOINT_FIELDS:
        value = getattr(spec, name, "")
        if isinstance(value, (list, tuple)):
            values.append("|".join(str(item) for item in value))
        else:
            values.append(str(value or ""))
    return tuple(values)


@dataclass
class McpServerSpec:
    """What the user typed in: where the server is and how to reach it."""

    server_id: str
    label: str = ""
    transport: str = TRANSPORT_STDIO
    command: Any = ()
    args: Tuple[str, ...] = ()
    cwd: str = ""
    env: Dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    timeout: float = 15.0
    # Which remote tool names this server is allowed to expose. Empty means none:
    # a server that was just added has no tools until somebody ticks them (K3).
    enabled_tools: Tuple[str, ...] = ()

    def __post_init__(self):
        self.server_id = str(self.server_id or "").strip().lower()
        if not _ID_RE.match(self.server_id):
            raise McpError(
                "服务器标识只能用小写字母、数字、下划线和连字符，且不超过 32 个字符。",
                "mcp_bad_id",
            )
        self.transport = str(self.transport or "").strip().lower()
        if self.transport not in TRANSPORTS:
            raise McpError(
                "不支持的传输类型：{}。".format(self.transport), "mcp_bad_transport"
            )
        if not isinstance(self.enabled_tools, (list, tuple)):
            self.enabled_tools = ()
        self.enabled_tools = tuple(str(name) for name in self.enabled_tools)
        try:
            self.timeout = float(self.timeout)
        except (TypeError, ValueError):
            self.timeout = 15.0
        if self.timeout <= 0:
            self.timeout = 15.0
        if self.transport == TRANSPORT_HTTP:
            if not str(self.url or "").strip():
                raise McpError("HTTP 服务器需要填写地址。", "mcp_bad_config")
        else:
            if not _has_command(self.command):
                raise McpError("stdio 服务器需要填写启动命令。", "mcp_bad_config")

    @property
    def display(self) -> str:
        return self.label or self.server_id

    def allows(self, tool_name: str) -> bool:
        return str(tool_name) in self.enabled_tools


@dataclass
class McpCallResult:
    """Outcome of a remote call; ``text`` is always safe to put in front of a user."""

    ok: bool
    text: str = ""
    code: str = ""
    detail: str = ""
    remote_error: bool = False

    @classmethod
    def success(cls, text: str) -> "McpCallResult":
        return cls(True, text=text)

    @classmethod
    def failure(cls, text: str, code: str = "mcp_call_failed", detail: str = "") -> "McpCallResult":
        return cls(False, text=text, code=code, detail=detail)


class _Connection:
    def __init__(self, spec: McpServerSpec, transport):
        self.spec = spec
        self.transport = transport
        self.tools: List[Dict[str, Any]] = []
        self.issues: List[str] = []
        self.server_info: Dict[str, Any] = {}
        self.connected_at = time.time()
        self.state = STATE_CONNECTED
        self.error = ""

    @property
    def alive(self) -> bool:
        return self.state == STATE_CONNECTED and self.transport.connected


class McpService:
    """Registry of servers, their live connections and the tools we found on them."""

    def __init__(self, specs=None, transport_factory=None):
        self._lock = threading.RLock()
        self._specs: Dict[str, McpServerSpec] = {}
        self._connections: Dict[str, _Connection] = {}
        self._factory = transport_factory or _default_factory
        for spec in specs or []:
            self.add_spec(spec)

    # configuration

    def add_spec(self, spec: McpServerSpec) -> None:
        if not isinstance(spec, McpServerSpec):
            raise McpError("服务器配置格式不正确。", "mcp_bad_config")
        with self._lock:
            self._specs[spec.server_id] = spec
            connection = self._connections.get(spec.server_id)
            if (
                connection is not None
                and connection.alive
                and endpoint_identity(connection.spec) == endpoint_identity(spec)
            ):
                # 面板改的是开关或名称，不是地址：在线的那条连接也要拿到新 spec，
                # 否则 snapshot() 报的还是旧 enabled_tools，勾上/取消要等重连才生效。
                connection.spec = spec

    def remove_spec(self, server_id: str) -> None:
        with self._lock:
            self._specs.pop(str(server_id), None)
            self.disconnect(str(server_id))

    @property
    def specs(self) -> Tuple[McpServerSpec, ...]:
        with self._lock:
            return tuple(self._specs.values())

    def spec(self, server_id: str) -> Optional[McpServerSpec]:
        with self._lock:
            return self._specs.get(str(server_id))

    # connection

    def connect(self, server_id: str) -> _Connection:
        spec = self.spec(server_id)
        if spec is None:
            raise McpError("没有这台服务器：{}".format(server_id), "mcp_unknown_server")
        with self._lock:
            existing = self._connections.get(spec.server_id)
            if existing is not None and existing.alive:
                return existing
            self._retire(existing)
            transport = self._factory(spec)
            connection = _Connection(spec, transport)
            try:
                transport.connect()
                info = self._handshake(transport)
                connection.server_info = info
                connection.tools, connection.issues = self._list_tools(transport)
            except McpError as error:
                self._retire(connection, STATE_FAILED, error.reason)
                self._connections[spec.server_id] = connection
                raise
            self._connections[spec.server_id] = connection
            return connection

    def refresh(self, server_id: str) -> Tuple[Dict[str, Any], ...]:
        """Re-read tools/list on an already open connection."""
        with self._lock:
            connection = self._connections.get(str(server_id))
            if connection is None or not connection.alive:
                raise McpError(
                    "服务器未连接，请先连接。", "mcp_not_connected"
                )
            try:
                tools, issues = self._list_tools(connection.transport)
            except McpError as error:
                self._retire(connection, STATE_FAILED, error.reason)
                raise
            connection.tools = tools
            connection.issues = issues
            return tuple(tools)

    def disconnect(self, server_id: str) -> None:
        with self._lock:
            connection = self._connections.pop(str(server_id), None)
            self._retire(connection, STATE_DOWN, "")

    def close_all(self) -> None:
        with self._lock:
            for server_id in list(self._connections):
                connection = self._connections.pop(server_id, None)
                self._retire(connection, STATE_DOWN, "")

    def status(self, server_id: str) -> Dict[str, Any]:
        with self._lock:
            spec = self._specs.get(str(server_id))
            connection = self._connections.get(str(server_id))
            if connection is None:
                return {
                    "state": STATE_DOWN,
                    "error": "",
                    "tools": [],
                    "issues": [],
                    "label": spec.display if spec else str(server_id),
                }
            state = connection.state
            if state == STATE_CONNECTED and not connection.transport.connected:
                # 还没轮到 snapshot 收尾：先按离线报，别把死连接说成在线。
                state = STATE_DOWN
            return {
                "state": state,
                "error": connection.error,
                "tools": list(connection.tools),
                "issues": list(connection.issues),
                "label": connection.spec.display,
                "server_info": dict(connection.server_info),
            }

    # turn-time surface

    def snapshot(self) -> List[Tuple[McpServerSpec, List[Dict[str, Any]]]]:
        """Connected servers with their cached tools; never touches the network."""
        with self._lock:
            found = []
            for connection in list(self._connections.values()):
                if connection.alive:
                    found.append((connection.spec, list(connection.tools)))
                elif connection.state == STATE_CONNECTED:
                    # The process or session went away mid-life. Say so, and stop
                    # offering tools that can only fail, but keep the record so
                    # the console can still name the server as 离线.
                    self._retire(
                        connection,
                        STATE_DOWN,
                        connection.error or "服务器连接已断开。",
                    )
            return found

    def call_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> McpCallResult:
        with self._lock:
            connection = self._connections.get(str(server_id))
        if connection is None or not connection.alive:
            return McpCallResult.failure(
                "服务器 {} 未连接，无法调用。".format(server_id),
                "mcp_not_connected",
                detail=connection.error if connection else "",
            )
        payload = {"name": str(tool_name), "arguments": dict(arguments or {})}
        try:
            result = connection.transport.request(
                "tools/call", payload, timeout=timeout
            )
            text, is_error = call_text(result)
        except McpTimeout as error:
            self._retire(connection, STATE_DOWN, error.reason)
            return McpCallResult.failure(error.reason, error.code, error.detail)
        except McpRemoteError as error:
            # The server ran and refused; the connection itself is still good.
            with self._lock:
                connection.error = error.reason
            return McpCallResult.failure(error.reason, error.code, error.detail)
        except McpError as error:
            self._retire(connection, STATE_DOWN, error.reason)
            return McpCallResult.failure(error.reason, error.code, error.detail)
        except (OSError, ValueError) as error:
            reason = "服务器调用异常：{}".format(error)
            self._retire(connection, STATE_DOWN, reason)
            return McpCallResult.failure(reason, "mcp_call_failed", detail=str(error))
        if is_error:
            return McpCallResult(
                False,
                text=text,
                code="mcp_tool_error",
                remote_error=True,
            )
        return McpCallResult.success(text)

    # internals

    def _retire(
        self,
        connection: Optional[_Connection],
        state: str = STATE_DOWN,
        reason: str = "",
    ) -> None:
        """Close a transport and remember why, keeping whatever record exists."""
        if connection is None:
            return
        connection.state = state
        connection.error = reason
        try:
            connection.transport.close()
        except Exception:  # closing a dead thing is never worth a crash
            pass

    def _handshake(self, transport) -> Dict[str, Any]:
        info = transport.request("initialize", initialize_params())
        if not isinstance(info, dict):
            raise McpProtocolError("服务器对 initialize 返回了不是对象的结果。")
        transport.notify("notifications/initialized")
        return info

    def _list_tools(self, transport):
        tools: List[Dict[str, Any]] = []
        issues: List[str] = []
        cursor = None
        for page in range(MAX_LIST_PAGES):
            params: Dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            result = transport.request("tools/list", params)
            found, page_issues = parse_tools(result)
            tools.extend(found)
            issues.extend(page_issues)
            if not isinstance(result, dict):
                break
            cursor = result.get("nextCursor")
            if not cursor:
                break
        else:
            issues.append("工具列表分页超过 {} 页，已停止。".format(MAX_LIST_PAGES))
        duplicates = _duplicate_names(tools)
        if duplicates:
            issues.append(
                "服务器上有重名工具，已保留第一个：{}".format("、".join(duplicates))
            )
        return tools, issues


def _has_command(command) -> bool:
    if isinstance(command, str):
        return bool(command.strip())
    if isinstance(command, (list, tuple)):
        return any(str(item).strip() for item in command)
    return bool(command)


def _default_factory(spec: McpServerSpec):
    if spec.transport == TRANSPORT_HTTP:
        from .transport_http import HttpTransport

        return HttpTransport(spec.url, headers=spec.headers, timeout=spec.timeout,
                             label=spec.display)
    from .transport_stdio import StdioTransport

    return StdioTransport(
        spec.command,
        args=spec.args,
        env=spec.env or None,
        cwd=spec.cwd or None,
        timeout=spec.timeout,
        label=spec.display,
    )


def _duplicate_names(tools: List[Dict[str, Any]]) -> List[str]:
    seen = set()
    dupes = []
    for item in tools:
        name = str(item.get("name") or "")
        if name in seen and name not in dupes:
            dupes.append(name)
        seen.add(name)
    return dupes
