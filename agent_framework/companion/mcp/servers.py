"""Turning ``mcp_servers`` rows into live specs, with credentials from disk (A9, Q2).

The database keeps addresses and switches; ``config/mcp_keys.json`` keeps the two
secret sections. They meet only here, and only in memory: nothing in this module
writes a credential into a row, a receipt or a log line.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .keys import McpKeysError, transport_payload
from .service import (
    ENDPOINT_FIELDS,
    McpError,
    McpServerSpec,
    McpService,
    endpoint_identity,
)

# Kept as the name the reload code reads: the parts of a config that decide *which*
# machine a call lands on. Changing one while a connection is open would let a
# confirmation approved against address A travel to address B over the socket we
# already opened for A.
IDENTITY_FIELDS = ENDPOINT_FIELDS


def spec_from_record(record: Dict[str, Any]) -> McpServerSpec:
    """Build one spec; secrets come from the keys file, never from the row.

    A credential file we cannot read is a missing credential, never a partially
    loaded one: the caller turns ``McpKeysError`` into a readable issue and the
    server still gets a spec, so the console can say 未连接缺少凭据.
    """
    server_id = str(record.get("server_id") or "")
    env, headers = transport_payload(
        server_id, str(record.get("transport") or "")
    )
    return McpServerSpec(
        server_id=str(record.get("server_id") or ""),
        label=str(record.get("label") or ""),
        transport=str(record.get("transport") or ""),
        command=record.get("command") or "",
        args=tuple(str(item) for item in (record.get("args") or ())),
        cwd=str(record.get("cwd") or ""),
        url=str(record.get("url") or ""),
        headers=headers,
        env=env,
        timeout=float(record.get("timeout") or 15.0),
        enabled_tools=tuple(str(name) for name in (record.get("tools") or ())),
    )


def reload_service(
    service: McpService, records: List[Dict[str, Any]]
) -> Tuple[List[str], List[str]]:
    """Sync the live specs to the table; returns (loaded ids, readable issues).

    No connection is opened here. ``tools/list`` is outbound, so it stays behind
    the 「连接并获取工具」 button (and behind the enabled switch).
    """
    loaded: List[str] = []
    issues: List[str] = []
    wanted: Dict[str, Dict[str, Any]] = {}
    for record in records or []:
        if not isinstance(record, dict):
            continue
        server_id = str(record.get("server_id") or "")
        if not server_id:
            issues.append("有一条远程服务器记录没有标识，已跳过。")
            continue
        wanted[server_id] = record
        try:
            spec = spec_from_record(record)
        except (McpError, McpKeysError, ValueError, TypeError) as error:
            issues.append(
                "服务器 {}：{}".format(
                    server_id, getattr(error, "reason", None) or error
                )
            )
            continue
        current = service.spec(server_id)
        if current is not None and endpoint_identity(current) != endpoint_identity(spec):
            # Editing the address invalidates the socket that was opened for the
            # old one; drop it instead of sending new traffic down it.
            service.disconnect(server_id)
        service.add_spec(spec)
        if not record.get("enabled", True):
            # 停用 = 现在就别再出网；配置留在表里，开关回到开启时才重新可用。
            service.disconnect(server_id)
        loaded.append(server_id)
    for spec in service.specs:
        if spec.server_id not in wanted:
            service.remove_spec(spec.server_id)
    return loaded, issues
