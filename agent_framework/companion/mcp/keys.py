"""MCP credentials on disk: config/mcp_keys.json, never the database.

The server rows in ``mcp_servers`` describe *where* a server is. The secrets that
let us in — an env value for a stdio child, an ``Authorization`` header for an
HTTP endpoint — live here instead, so a database backup, a zip export or a
screenshot of the settings panel cannot carry a token out of the machine.
Nothing in this module returns a value: reads hand back masked strings, and the
only place the plaintext exists is the process that is about to spawn or call.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Tuple

from ..settings import mask_secret

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
DEFAULT_KEYS_FILE = os.path.join(REPO_ROOT, "config", "mcp_keys.json")

# Same override shape as config.py: a second data directory needs its own
# credential file without rebuilding the app.
KEYS_PATH_ENV = "COMPANION_MCP_KEYS_FILE"

SECTION_ENV = "env"
SECTION_HEADERS = "headers"
SECTIONS = (SECTION_ENV, SECTION_HEADERS)

MAX_NAME_CHARS = 128
MAX_VALUE_CHARS = 4096
MAX_SECTION_ENTRIES = 32

_ID_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."
)


class McpKeysError(ValueError):
    def __init__(self, reason: str, code: str = "mcp_keys_error"):
        super().__init__(reason)
        self.reason = reason
        self.code = code


def keys_path() -> str:
    return os.getenv(KEYS_PATH_ENV) or DEFAULT_KEYS_FILE


def server_id_valid(server_id: str) -> bool:
    text = str(server_id or "")
    return bool(text) and len(text) <= 64 and all(ch in _ID_CHARS for ch in text)


def read_all(path: str = None) -> Dict[str, Dict[str, Dict[str, str]]]:
    """Return ``{server_id: {section: {name: value}}}`` with the file unescaped.

    Callers that need plaintext are the transport builders only; anything that
    shows the value to a person must go through :func:`describe_server`.
    """
    raw = _read_json(path or keys_path())
    servers = raw.get("servers") if isinstance(raw, dict) else None
    result: Dict[str, Dict[str, Dict[str, str]]] = {}
    if not isinstance(servers, dict):
        return result
    for server_id, entry in servers.items():
        if not server_id_valid(str(server_id)) or not isinstance(entry, dict):
            continue
        cleaned: Dict[str, Dict[str, str]] = {}
        for section in SECTIONS:
            values = entry.get(section)
            if not isinstance(values, dict):
                continue
            cleaned[section] = {
                str(name): str(value)
                for name, value in values.items()
                if str(name).strip() and str(value)
            }
        if cleaned:
            result[str(server_id)] = cleaned
    return result


def server_credentials(server_id: str) -> Dict[str, Dict[str, str]]:
    return read_all().get(str(server_id or ""), {})


def transport_payload(
    server_id: str, transport: str
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """``(env, headers)`` for one server, keeping only the section it can use.

    A stdio child takes environment variables; an HTTP endpoint takes headers.
    Handing the other section over would send a token somewhere the user never
    pointed it.
    """
    entry = server_credentials(server_id)
    if str(transport or "").strip().lower() == "stdio":
        return dict(entry.get(SECTION_ENV) or {}), {}
    return {}, dict(entry.get(SECTION_HEADERS) or {})


def save_server(
    server_id: str,
    env: Any = None,
    headers: Any = None,
    path: str = None,
) -> Dict[str, List[str]]:
    """Merge one server's secrets into the file; returns the surviving names.

    A given section replaces that section; an empty value deletes that name; a
    section left as ``None`` is not touched. This is what the 「替换」 button in
    the panel needs and it is the only write path.
    """
    if not server_id_valid(server_id):
        raise McpKeysError(
            "服务器标识不能用于凭据文件：{}".format(server_id),
            "mcp_keys_bad_server",
        )
    target = path or keys_path()
    raw = _read_json(target)
    servers = raw.get("servers")
    if not isinstance(servers, dict):
        servers = {}
    entry = servers.get(str(server_id))
    if not isinstance(entry, dict):
        entry = {}
    touched = {}
    for section, values in ((SECTION_ENV, env), (SECTION_HEADERS, headers)):
        if values is None:
            if isinstance(entry.get(section), dict):
                touched[section] = sorted(
                    str(name) for name in entry[section] if entry[section].get(name)
                )
            else:
                touched[section] = []
            continue
        cleaned = _clean_section(section, values)
        if cleaned:
            entry[section] = cleaned
        else:
            entry.pop(section, None)
        touched[section] = sorted(cleaned)
    if entry:
        servers[str(server_id)] = entry
    else:
        servers.pop(str(server_id), None)
    _write_json({"servers": servers}, target)
    return touched


def clear_server(server_id: str, path: str = None) -> bool:
    """Drop one server's entry; used when the user removes the server (S04c)."""
    target = path or keys_path()
    raw = _read_json(target)
    servers = raw.get("servers")
    if not isinstance(servers, dict) or str(server_id) not in servers:
        return False
    servers.pop(str(server_id), None)
    _write_json({"servers": servers}, target)
    return True


def describe_server(server_id: str, path: str = None) -> Dict[str, Any]:
    """What the panel may render: names plus a mask, never the value."""
    entry = read_all(path).get(str(server_id or ""), {})
    sections = {}
    for section in SECTIONS:
        values = entry.get(section) or {}
        sections[section] = [
            {"name": name, "masked": mask_secret(values[name])}
            for name in sorted(values)
        ]
    return {
        "server_id": str(server_id or ""),
        "present": bool(entry),
        "sections": sections,
        "file": os.path.basename(path or keys_path()),
    }


def status(path: str = None) -> Dict[str, Any]:
    """Existence, size, permission and entry count — for doctor, no contents."""
    target = path or keys_path()
    report = {
        "path": target,
        "exists": os.path.exists(target),
        "readable": True,
        "mode": "",
        "bytes": 0,
        "servers": 0,
        "entries": 0,
        "error": "",
    }
    if not report["exists"]:
        return report
    try:
        info = os.stat(target)
        report["bytes"] = int(info.st_size)
        report["mode"] = "{:o}".format(info.st_mode & 0o777)
        servers = read_all(target)
        report["servers"] = len(servers)
        report["entries"] = sum(
            len(values)
            for entry in servers.values()
            for values in entry.values()
        )
    except OSError as error:
        report["readable"] = False
        report["error"] = "凭据文件读不出来：{}".format(error)
    except ValueError as error:
        report["error"] = "凭据文件不是有效 JSON：{}".format(error)
    return report


def _clean_section(section: str, values: Any) -> Dict[str, str]:
    if isinstance(values, (list, tuple)):
        values = {
            str(item.get("name")): item.get("value")
            for item in values
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }
    if not isinstance(values, dict):
        raise McpKeysError(
            "{} 段必须是键值对。".format(section), "mcp_keys_bad_shape"
        )
    cleaned: Dict[str, str] = {}
    for name, value in values.items():
        key = str(name or "").strip()
        if not key:
            continue
        if len(key) > MAX_NAME_CHARS:
            raise McpKeysError(
                "{} 里的名称超过 {} 字符。".format(section, MAX_NAME_CHARS),
                "mcp_keys_bad_name",
            )
        text = "" if value is None else str(value).strip()
        if not text:
            continue
        if len(text) > MAX_VALUE_CHARS:
            raise McpKeysError(
                "{} 的值超过 {} 字符。".format(section, MAX_VALUE_CHARS),
                "mcp_keys_too_long",
            )
        cleaned[key] = text
    if len(cleaned) > MAX_SECTION_ENTRIES:
        raise McpKeysError(
            "{} 最多 {} 条。".format(section, MAX_SECTION_ENTRIES),
            "mcp_keys_too_many",
        )
    return cleaned


def _read_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as file:
            raw = json.load(file)
    except OSError as error:
        raise McpKeysError(
            "凭据文件读不出来：{}".format(error), "mcp_keys_unreadable"
        )
    except ValueError as error:
        raise McpKeysError(
            "凭据文件不是有效 JSON：{}".format(error), "mcp_keys_bad_json"
        )
    return raw if isinstance(raw, dict) else {}


def _write_json(values: Dict[str, Any], target: str) -> None:
    """Replace the file atomically; the payload holds secrets, so never append.

    A half-written credential file would silently drop a token and turn the next
    connection into a 401 the user cannot explain.
    """
    folder = os.path.dirname(target)
    if folder:
        os.makedirs(folder, exist_ok=True)
    temp = "{}.tmp".format(target)
    with open(temp, "w", encoding="utf-8") as file:
        json.dump(values, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    try:
        os.chmod(temp, 0o600)
    except OSError:
        # Windows keeps its own ACL here; a mode we cannot set is not a reason to
        # leave the credential file unwritten.
        pass
    os.replace(temp, target)
