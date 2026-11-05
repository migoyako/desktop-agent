"""The panel side of MCP governance (A9): rows, switches and masked credentials.

Two rules hold this module together. Nothing that leaves it may carry a
credential value — names and ``mask_secret()`` shapes only, because the page, the
audit log and a database backup all travel to different places (Q2). And nothing
here opens a connection by accident: ``tools/list`` is outbound, so it runs when
the user presses 「连接并获取工具」 and nowhere else.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..redaction import redact_text
from . import keys
from .bridge import tool_name
from .jsonrpc import McpError
from .servers import reload_service
from .service import (
    STATE_CONNECTED,
    STATE_DOWN,
    TRANSPORT_HTTP,
    TRANSPORT_STDIO,
    McpServerSpec,
    McpService,
)

MAX_LABEL_CHARS = 40
MAX_COMMAND_CHARS = 400
MAX_ARGS_ITEMS = 16
MAX_ARG_CHARS = 200
MAX_URL_CHARS = 400
MAX_DESCRIPTION_CHARS = 240
TIMEOUT_MIN = 1.0
TIMEOUT_MAX = 300.0
TIMEOUT_DEFAULT = 15.0

# S04b draws three transport segments; only two are implemented, so the third is
# rendered as a stated dead end rather than silently missing.
TRANSPORT_CHOICES = (
    {"value": TRANSPORT_STDIO, "label": "stdio", "available": True, "reason": ""},
    {
        "value": "sse",
        "label": "SSE",
        "available": False,
        "reason": "这个版本没有实现 SSE 传输；远程服务请用 streamable-http。",
    },
    {
        "value": TRANSPORT_HTTP,
        "label": "streamable-http",
        "available": True,
        "reason": "",
    },
)

TRANSPORT_LABELS = {
    TRANSPORT_STDIO: "stdio",
    TRANSPORT_HTTP: "streamable-http",
}


class McpConsoleError(ValueError):
    """A rejected edit, in a sentence the panel can show as-is."""

    def __init__(self, reason: str, code: str = "mcp_console_error"):
        super().__init__(reason)
        self.reason = reason
        self.code = code


class McpConsole:
    """Reads and edits behind ``/api/mcp``: the table, the live specs, the keys file."""

    def __init__(self, store, service_provider):
        self.store = store
        # Resolved per call: each session has its own runtime and its own
        # McpService, so the console must not hold on to whichever one existed
        # when it was built.
        self._service_provider = service_provider
        self.load_issues: List[str] = []

    @property
    def service(self) -> McpService:
        return self._service_provider()

    # -- reads ---------------------------------------------------------

    def panel(self) -> Dict[str, Any]:
        records = self.store.list_mcp_servers()
        rows = [self.row(record) for record in records]
        connected = sum(1 for item in rows if item["state"] == STATE_CONNECTED)
        return {
            "rows": rows,
            "summary": {
                "servers": len(rows),
                "connected": connected,
                "tools": sum(len(item["tools"]) for item in rows),
                "enabled_tools": sum(item["enabled_tools"] for item in rows),
            },
            "transports": [dict(item) for item in TRANSPORT_CHOICES],
            "issues": list(self.load_issues),
            "keys": self._keys_status(),
            "limits": {
                "label": MAX_LABEL_CHARS,
                "command": MAX_COMMAND_CHARS,
                "args": MAX_ARGS_ITEMS,
                "url": MAX_URL_CHARS,
                "timeout_min": TIMEOUT_MIN,
                "timeout_max": TIMEOUT_MAX,
                "timeout_default": TIMEOUT_DEFAULT,
            },
            # The panel is wired to real data as of 切片 4; the page keeps the
            # greyed look for anything still without an approved artboard.
            "connected_ui": True,
        }

    def row(self, record: Dict[str, Any]) -> Dict[str, Any]:
        server_id = str(record.get("server_id") or "")
        status = self._status(server_id)
        tools, enabled_count = self._tool_rows(server_id, record, status)
        return {
            "server_id": server_id,
            "label": str(record.get("label") or ""),
            "transport": str(record.get("transport") or TRANSPORT_STDIO),
            "transport_label": TRANSPORT_LABELS.get(
                str(record.get("transport") or TRANSPORT_STDIO),
                str(record.get("transport") or ""),
            ),
            "command": str(record.get("command") or ""),
            "args": [str(item) for item in (record.get("args") or [])],
            "cwd": str(record.get("cwd") or ""),
            "url": str(record.get("url") or ""),
            "timeout": float(record.get("timeout") or 15.0),
            "enabled": bool(record.get("enabled")),
            "state": status["state"],
            # 连接失败的原因是远端给的原文，先过一遍脱敏再进面板。
            "error": _mask(str(status.get("error") or "")),
            # S04e 的「参数结构超出上限」这类收敛说明来自连接，不是来自表。
            "issues": [_mask(str(item)) for item in (status.get("issues") or [])],
            "tools": tools,
            "enabled_tools": enabled_count,
            "credentials": self._credentials(server_id),
            "created_at": str(record.get("created_at") or ""),
            "updated_at": str(record.get("updated_at") or ""),
        }

    # -- edits ---------------------------------------------------------

    def save(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Create or edit one server row, then re-sync the live specs.

        A new row starts with every tool switch off (S04b); an edit keeps them,
        because the tools belong to the server the switches already name.
        """
        server_id = self._id(str(record.get("server_id") or ""))
        current = self.store.get_mcp_server(server_id)
        transport = str(record.get("transport") or TRANSPORT_STDIO)
        if current is None:
            tools: List[str] = []
            discovered: List[Dict[str, Any]] = []
            created = None
        else:
            if transport != str(current.get("transport") or ""):
                raise McpConsoleError(
                    "传输类型建立后不能改；要换协议请新建一台服务器。",
                    "mcp_transport_locked",
                )
            tools = [str(item) for item in (current.get("tools") or [])]
            discovered = list(current.get("discovered") or [])
            created = str(current.get("created_at") or "")
        spec = self._spec(
            server_id,
            label=str(record.get("label") or ""),
            transport=transport,
            command=str(record.get("command") or ""),
            args=_as_list(record.get("args")),
            cwd=str(record.get("cwd") or ""),
            url=str(record.get("url") or ""),
            timeout=record.get("timeout"),
        )
        self.store.upsert_mcp_server(
            {
                "server_id": server_id,
                "label": spec.label,
                "transport": spec.transport,
                "command": _command_text(record.get("command")),
                "args": list(spec.args),
                "cwd": spec.cwd,
                "url": spec.url,
                "timeout": spec.timeout,
                "enabled": bool(record.get("enabled")),
                "tools": tools,
                "discovered": discovered,
                "created_at": created,
            }
        )
        self.reload()
        return self.row(self.store.get_mcp_server(server_id))

    def validate(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """「重新校验」：把这张表单走一遍保存时的字段校验，不写表、不出网。

        工具数量只有连上远端才知道。已经存在的服务器带回缓存的清单长度；新建的
        那台只能说「字段没问题」，页面不能拿它当「已发现工具」的回执。
        """
        server_id = self._id(str(record.get("server_id") or ""))
        current = self.store.get_mcp_server(server_id)
        transport = str(record.get("transport") or TRANSPORT_STDIO)
        if current is not None and transport != str(current.get("transport") or ""):
            raise McpConsoleError(
                "传输类型建立后不能改；要换协议请新建一台服务器。",
                "mcp_transport_locked",
            )
        spec = self._spec(
            server_id,
            label=str(record.get("label") or ""),
            transport=transport,
            command=str(record.get("command") or ""),
            args=_as_list(record.get("args")),
            cwd=str(record.get("cwd") or ""),
            url=str(record.get("url") or ""),
            timeout=record.get("timeout"),
        )
        discovered = list((current or {}).get("discovered") or [])
        return {
            "ok": True,
            "server_id": spec.server_id,
            "transport": spec.transport,
            "exists": current is not None,
            "discovered": len(discovered),
            "enabled_tools": len((current or {}).get("tools") or []),
            "credentials": self._credentials(spec.server_id)["present"],
        }

    def set_enabled(self, server_id: str, enabled: bool) -> Optional[Dict[str, Any]]:
        record = self._require(server_id)
        if not self.store.set_mcp_server_enabled(record["server_id"], bool(enabled)):
            raise McpConsoleError("没有这台服务器。", "mcp_unknown_server")
        self.reload()
        return self.row(self.store.get_mcp_server(record["server_id"]))

    def set_tools(
        self, server_id: str, names, enable: Optional[bool] = None
    ) -> Optional[Dict[str, Any]]:
        """Replace the enabled set, or move one tool into/out of it.

        Only a name the server actually reported can be switched on: a typo in the
        request must not become a tool the model gets offered.
        """
        record = self._require(server_id)
        known = {str(item.get("name") or "") for item in (record.get("discovered") or [])}
        known |= {str(item.get("name") or "") for item in self._status(record["server_id"])["tools"]}
        wanted = [str(name) for name in (names or [])]
        if enable is not None:
            current = {str(name) for name in (record.get("tools") or [])}
            for name in wanted:
                if enable:
                    current.add(name)
                else:
                    current.discard(name)
            wanted = sorted(current)
        unknown = [name for name in wanted if name and name not in known]
        if unknown:
            raise McpConsoleError(
                "这些工具不在服务器上报的清单里：{}。".format("、".join(unknown)),
                "mcp_unknown_tool",
            )
        if not self.store.set_mcp_server_tools(record["server_id"], wanted):
            raise McpConsoleError("没有这台服务器。", "mcp_unknown_server")
        self.reload()
        return self.row(self.store.get_mcp_server(record["server_id"]))

    def set_all_tools(self, server_id: str, enable: bool) -> Optional[Dict[str, Any]]:
        """全开 / 全关（S04e）：清单就是这台服务器报上来的工具。"""
        record = self._require(server_id)
        names = [
            str(item.get("name") or "")
            for item in self._discovered(record)
            if str(item.get("name") or "")
        ]
        return self.set_tools(record["server_id"], names if enable else [], None)

    def remove(self, server_id: str) -> Dict[str, Any]:
        """Drop the row, its tool switches and its credential entry (S04c)."""
        record = self._require(server_id)
        text_id = record["server_id"]
        switches = len(record.get("tools") or [])
        self.service.remove_spec(text_id)
        removed = bool(self.store.delete_mcp_server(text_id))
        try:
            cleared = bool(keys.clear_server(text_id))
        except keys.McpKeysError as error:
            # The row is gone either way; say which half did not happen instead of
            # pretending the removal was clean.
            return {
                "removed": removed,
                "switches": switches,
                "credentials_cleared": False,
                "warning": _mask(error.reason),
            }
        return {
            "removed": removed,
            "switches": switches,
            "credentials_cleared": cleared,
        }

    # -- credentials (Q2) ----------------------------------------------

    def set_credentials(
        self,
        server_id: str,
        env: Any = None,
        headers: Any = None,
    ) -> Dict[str, Any]:
        record = self._require(server_id)
        text_id = record["server_id"]
        transport = str(record.get("transport") or TRANSPORT_STDIO)
        if env is not None and transport != TRANSPORT_STDIO:
            raise McpConsoleError(
                "环境变量只用于 stdio 服务器；HTTP 服务器请填写请求头。",
                "mcp_credentials_section",
            )
        if headers is not None and transport != TRANSPORT_HTTP:
            raise McpConsoleError(
                "请求头只用于 streamable-http 服务器；stdio 服务器请填写环境变量。",
                "mcp_credentials_section",
            )
        try:
            keys.save_server(text_id, env=env, headers=headers)
        except keys.McpKeysError as error:
            raise McpConsoleError(error.reason, error.code)
        # The live spec carries the previous secrets; rebuild it from the row so a
        # 「替换」 takes effect without another connect dance.
        self.reload()
        return self._credentials(text_id)

    def clear_credentials(self, server_id: str) -> Dict[str, Any]:
        record = self._require(server_id)
        text_id = record["server_id"]
        try:
            cleared = bool(keys.clear_server(text_id))
        except keys.McpKeysError as error:
            raise McpConsoleError(error.reason, error.code)
        self.reload()
        return {"cleared": cleared, **self._credentials(text_id)}

    def set_credential(
        self,
        server_id: str,
        name: str,
        value: Any = None,
        remove: bool = False,
    ) -> Dict[str, Any]:
        """Replace or drop one name (S04b/S04c 的「替换」) without touching its siblings.

        :meth:`set_credentials` replaces a whole section, which is what the add
        form needs and the wrong thing for 「把这一个令牌换成新的」: the page never
        sees the other values, so it cannot resend them. The merge therefore
        happens here, against the file, and the answer is still names plus masks.
        """
        record = self._require(server_id)
        text_id = record["server_id"]
        transport = str(record.get("transport") or TRANSPORT_STDIO)
        section = keys.SECTION_ENV if transport == TRANSPORT_STDIO else keys.SECTION_HEADERS
        clean_name = str(name or "").strip()
        if not clean_name:
            raise McpConsoleError("请填写凭据名称。", "mcp_bad_credential")
        merged = dict((keys.server_credentials(text_id) or {}).get(section) or {})
        if remove:
            if clean_name not in merged:
                raise McpConsoleError(
                    "{} 里没有 {}。".format(
                        "环境变量" if section == keys.SECTION_ENV else "请求头", clean_name
                    ),
                    "mcp_unknown_credential",
                )
            merged.pop(clean_name)
        else:
            merged[clean_name] = str(value or "")
        try:
            keys.save_server(text_id, **{section: merged})
        except keys.McpKeysError as error:
            raise McpConsoleError(error.reason, error.code)
        self.reload()
        return self._credentials(text_id)

    # -- connections ----------------------------------------------------

    def connect(self, server_id: str) -> Dict[str, Any]:
        record = self._require(server_id)
        text_id = record["server_id"]
        if not record.get("enabled"):
            return {
                "ok": False,
                "error": "这台服务器处于停用状态，先打开开关才会连接。",
                "row": self.row(record),
            }
        if not self.load_issues and self.service.spec(text_id) is None:
            # A credential file we could not read left the row un-loaded; say so
            # instead of reporting an unknown server.
            self.reload()
        try:
            self.service.connect(text_id)
        except McpError as error:
            return {"ok": False, "error": _mask(error.reason), "row": self.row(self._require(text_id))}
        except Exception as error:
            return {
                "ok": False,
                "error": _mask("连接失败：{}".format(error)),
                "row": self.row(self._require(text_id)),
            }
        self._cache_discovered(text_id)
        return {"ok": True, "error": "", "row": self.row(self._require(text_id))}

    def disconnect(self, server_id: str) -> Dict[str, Any]:
        record = self._require(server_id)
        self.service.disconnect(record["server_id"])
        return {"ok": True, "row": self.row(record)}

    def refresh(self, server_id: str) -> Dict[str, Any]:
        """重新校验：只重读 tools/list，不做任何写入。"""
        record = self._require(server_id)
        text_id = record["server_id"]
        try:
            self.service.refresh(text_id)
        except McpError as error:
            return {"ok": False, "error": _mask(error.reason), "row": self.row(self._require(text_id))}
        self._cache_discovered(text_id)
        return {"ok": True, "error": "", "row": self.row(self._require(text_id))}

    def reload(self) -> List[str]:
        _, issues = reload_service(self.service, self.store.list_mcp_servers())
        self.load_issues = list(issues)
        return self.load_issues

    def reload_all(self) -> Dict[str, Any]:
        """重新加载全部：按表重建配置，再让已在线的服务器重报一次工具。"""
        issues = self.reload()
        refreshed = []
        for spec in list(self.service.specs):
            status = self._status(spec.server_id)
            if status["state"] != STATE_CONNECTED:
                continue
            try:
                self.service.refresh(spec.server_id)
            except McpError as error:
                refreshed.append(
                    {"server_id": spec.server_id, "ok": False, "error": _mask(error.reason)}
                )
                continue
            self._cache_discovered(spec.server_id)
            refreshed.append({"server_id": spec.server_id, "ok": True, "error": ""})
        return {"issues": issues, "refreshed": refreshed, **self.panel()}

    # -- internals ------------------------------------------------------

    def _require(self, server_id: str) -> Dict[str, Any]:
        record = self.store.get_mcp_server(server_id)
        if record is None:
            raise McpConsoleError(
                "没有这台服务器：{}".format(server_id), "mcp_unknown_server"
            )
        return record

    def _id(self, server_id: str) -> str:
        text = str(server_id or "").strip().lower()
        if not text:
            raise McpConsoleError("请填写服务器标识。", "mcp_bad_id")
        if not keys.server_id_valid(text):
            raise McpConsoleError(
                "服务器标识只能用小写字母、数字、下划线和连字符，且不超过 32 个字符。",
                "mcp_bad_id",
            )
        return text

    def _spec(self, server_id, **values) -> McpServerSpec:
        """Validate the human's typing by building the thing we would use."""
        label = str(values.get("label") or "")
        if len(label) > MAX_LABEL_CHARS:
            raise McpConsoleError(
                "名称最长 {} 个字符。".format(MAX_LABEL_CHARS), "mcp_bad_label"
            )
        command = _command_text(values.get("command"))
        if len(command) > MAX_COMMAND_CHARS:
            raise McpConsoleError(
                "命令最长 {} 个字符。".format(MAX_COMMAND_CHARS), "mcp_bad_command"
            )
        args = [str(item).strip() for item in (values.get("args") or [])]
        args = [item for item in args if item]
        if len(args) > MAX_ARGS_ITEMS:
            raise McpConsoleError(
                "参数最多 {} 项。".format(MAX_ARGS_ITEMS), "mcp_bad_args"
            )
        if any(len(item) > MAX_ARG_CHARS for item in args):
            raise McpConsoleError(
                "单个参数最长 {} 个字符。".format(MAX_ARG_CHARS), "mcp_bad_args"
            )
        url = str(values.get("url") or "").strip()
        if len(url) > MAX_URL_CHARS:
            raise McpConsoleError(
                "地址最长 {} 个字符。".format(MAX_URL_CHARS), "mcp_bad_url"
            )
        # 留空就是默认值：页面的「超时」不是必填项，不能因为没填就整条保存失败。
        raw_timeout = values.get("timeout")
        if raw_timeout is None or (isinstance(raw_timeout, str) and not raw_timeout.strip()):
            raw_timeout = TIMEOUT_DEFAULT
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError):
            raise McpConsoleError("超时必须是数字。", "mcp_bad_timeout")
        if not (TIMEOUT_MIN <= timeout <= TIMEOUT_MAX):
            raise McpConsoleError(
                "超时要在 {} 到 {} 秒之间。".format(TIMEOUT_MIN, TIMEOUT_MAX),
                "mcp_bad_timeout",
            )
        try:
            return McpServerSpec(
                server_id=server_id,
                label=label,
                transport=str(values.get("transport") or TRANSPORT_STDIO),
                command=command,
                args=tuple(args),
                cwd=str(values.get("cwd") or "").strip(),
                url=url,
                timeout=timeout,
            )
        except McpError as error:
            raise McpConsoleError(error.reason, error.code)

    def _status(self, server_id: str) -> Dict[str, Any]:
        try:
            status = self.service.status(server_id)
        except Exception:
            return {"state": STATE_DOWN, "error": "", "tools": []}
        return status

    def _discovered(self, record: Dict[str, Any]) -> List[Dict[str, Any]]:
        return list(record.get("discovered") or [])

    def _tool_rows(
        self, server_id: str, record: Dict[str, Any], status: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Discovered tools, plus anything a live connection just reported.

        The cached list is what the panel can draw while the server is offline;
        a fresh ``tools/list`` only ever adds or refreshes entries, so the switches
        do not blink off between connections.
        """
        enabled = {str(name) for name in (record.get("tools") or [])}
        merged: Dict[str, Dict[str, Any]] = {}
        for item in self._discovered(record):
            name = str(item.get("name") or "")
            if not name:
                continue
            merged[name] = {
                "description": _clip(str(item.get("description") or "")),
                "read_only": item.get("read_only"),
            }
        for item in status.get("tools") or []:
            name = str(item.get("name") or "")
            if not name:
                continue
            hint = item.get("annotations")
            read_only = None
            if isinstance(hint, dict) and isinstance(hint.get("readOnlyHint"), bool):
                read_only = hint["readOnlyHint"]
            merged[name] = {
                "description": _clip(str(item.get("description") or "")),
                "read_only": read_only,
            }
        rows = []
        for name in sorted(merged):
            rows.append(
                {
                    "name": name,
                    "tool_name": tool_name(server_id, name),
                    "description": merged[name]["description"],
                    "enabled": name in enabled,
                    # 「远端写入」只由服务器自己声明的 readOnlyHint 决定；没声明就
                    # 统一报「外部调用」，不替服务器猜。
                    "tag": "远端写入" if merged[name]["read_only"] is False else "外部调用",
                    "risk": "external",
                }
            )
        return rows, sum(1 for row in rows if row["enabled"])

    def _cache_discovered(self, server_id: str) -> None:
        tools = self._status(server_id).get("tools") or []
        if tools:
            self.store.set_mcp_server_discovered(server_id, tools)

    def _credentials(self, server_id: str) -> Dict[str, Any]:
        try:
            described = keys.describe_server(server_id)
        except keys.McpKeysError as error:
            return {
                "present": False,
                "error": _mask(error.reason),
                "sections": {keys.SECTION_ENV: [], keys.SECTION_HEADERS: []},
                "file": "",
            }
        described["error"] = ""
        return described

    def _keys_status(self) -> Dict[str, Any]:
        try:
            report = keys.status()
        except Exception as error:  # pragma: no cover - stat() cannot fail quietly
            return {"exists": False, "readable": False, "error": _mask(str(error))}
        report.pop("path", None)
        report["error"] = _mask(str(report.get("error") or ""))
        return report


def _as_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def _command_text(command) -> str:
    if isinstance(command, (list, tuple)):
        return " ".join(str(item).strip() for item in command if str(item).strip())
    return " ".join(str(command or "").split())


def _clip(text: str) -> str:
    text = " ".join(str(text or "").split())
    if len(text) > MAX_DESCRIPTION_CHARS:
        return text[:MAX_DESCRIPTION_CHARS] + "…"
    return text


def _mask(text: str) -> str:
    """远端原文里可能夹着令牌：只把凭据形状换成掩码，句子本身留着。"""
    return redact_text(str(text or ""))
