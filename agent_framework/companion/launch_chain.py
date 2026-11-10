"""Launch-chain service for the desktop execution agent (S5).

A chain references two registered applications instead of carrying executable
paths itself. The starter is launched through the existing S4 executor, then a
new target process is accepted only if it satisfies the stored detection rules.
No process or window observation is treated as proof by default.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import ctypes
from ctypes import wintypes
import hashlib
import os
import re
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from .app_catalog import AppCatalogService
from .app_launch import (
    AppLaunchService,
    launch_row,
    shell_open,
)
from .file_actions import NotExecutedError, UncertainExecutionError, proposal_expired
from .models import (
    ActionResult,
    ActionProposal,
    EXECUTOR_CHAIN,
    RiskLevel,
    canonical_json,
    confirmation_digest,
    new_id,
    utc_now,
)
from .store import (
    APP_READY,
    APP_TARGET_EXE,
    CompanionStore,
)


LAUNCH_CHAIN_TOOL = "run_launch_chain"
TRIGGER_AUTO = "auto"
TRIGGER_ARGUMENT = "argument"
TRIGGER_URI = "uri"
TRIGGER_API = "api"
TRIGGER_UIA = "uia"
TRIGGER_MANUAL = "manual"
TRIGGER_KINDS = ("auto", "argument", "uri", "api", "uia", "manual")
IMPLEMENTED_TRIGGER_KINDS = (
    TRIGGER_AUTO,
    TRIGGER_ARGUMENT,
    TRIGGER_URI,
    TRIGGER_API,
    TRIGGER_MANUAL,
)
ADAPTER_NONE = "none"
ADAPTER_UIA = "uia"
POST_LAUNCH_ADAPTERS = (ADAPTER_NONE, ADAPTER_UIA)

MAX_LAUNCH_CHAINS = 50
MAX_CHAIN_NAME_CHARS = 120
PROPOSAL_TTL_HOURS = 24
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 120.0
DEFAULT_POLL_SECONDS = 0.25
MIN_POLL_SECONDS = 0.05
MAX_POLL_SECONDS = 2.0
MAX_TRIGGER_ARGUMENTS = 16
MAX_TRIGGER_ARGUMENT_CHARS = 512
MAX_TRIGGER_TOTAL_CHARS = 4096
MAX_TRIGGER_URI_CHARS = 2048
MAX_RECORDING_CANDIDATES = 40
RECORDING_TTL_SECONDS = 900.0
BLOCKED_URI_SCHEMES = frozenset(
    {"data", "file", "javascript", "shell", "vbscript"}
)

CHAIN_UNDO_REASON = (
    "启动链已经启动了外部进程，本应用不托管这些进程的生命周期；"
    "撤销不可用。"
)

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAX_PATH = 32768


class LaunchChainError(RuntimeError):
    def __init__(self, message, code="launch_chain_error"):
        super().__init__(message)
        self.code = code


class _ProcessEntryW(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", wintypes.WPARAM),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _canonical_path(value) -> str:
    text = str(value or "").strip().strip('"')
    if text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(os.path.abspath(text)) if text else ""


def _process_image_path(process_id: int) -> str:
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(MAX_PATH)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        ):
            return ""
        return str(buffer.value or "")
    finally:
        kernel32.CloseHandle(handle)


def process_snapshot() -> List[Dict]:
    """Return current processes as dictionaries: pid, ppid, name and path."""
    if os.name != "nt":
        return []
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessEntryW),
    ]
    kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessEntryW),
    ]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not handle or handle == ctypes.c_void_p(-1).value:
        raise LaunchChainError(
            "无法读取本机进程列表，启动链未执行。", "process_snapshot_failed"
        )
    rows: List[Dict] = []
    try:
        entry = _ProcessEntryW()
        entry.dwSize = ctypes.sizeof(entry)
        found = kernel32.Process32FirstW(handle, ctypes.byref(entry))
        while found:
            pid = int(entry.th32ProcessID)
            rows.append(
                {
                    "pid": pid,
                    "ppid": int(entry.th32ParentProcessID),
                    "name": str(entry.szExeFile or ""),
                    "exe_path": _process_image_path(pid),
                }
            )
            found = kernel32.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(handle)
    return rows


def window_snapshot() -> List[Dict]:
    """Return visible top-level windows with pid, title and class name."""
    if os.name != "nt":
        return []
    user32 = ctypes.windll.user32
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    ]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    ]
    rows: List[Dict] = []
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )

    @callback_type
    def collect(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        title_length = max(0, int(user32.GetWindowTextLengthW(hwnd)))
        title = ctypes.create_unicode_buffer(title_length + 1)
        user32.GetWindowTextW(hwnd, title, title_length + 1)
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_name, 256)
        rows.append(
            {
                "hwnd": int(hwnd or 0),
                "pid": int(pid.value),
                "title": str(title.value or ""),
                "class_name": str(class_name.value or ""),
            }
        )
        return True

    if not user32.EnumWindows(collect, 0):
        raise LaunchChainError(
            "无法读取本机窗口列表，启动链未执行。", "window_snapshot_failed"
        )
    return rows


def _rule_list(rules: Dict, key: str) -> List[str]:
    value = rules.get(key)
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise LaunchChainError(
            "检测规则 {} 必须是字符串列表。".format(key),
            "invalid_detection_rules",
        )
    return [str(item).strip() for item in value if str(item).strip()]


def normalize_detection_rules(value) -> Dict:
    rules = value if isinstance(value, dict) else {}
    allowed = {
        "executable_path",
        "process_name",
        "reuse_existing",
        "require_descendant",
        "require_window",
        "window_title_contains",
        "window_class",
    }
    unknown = sorted(set(rules) - allowed)
    if unknown:
        raise LaunchChainError(
            "未知的检测规则：{}".format("、".join(unknown)),
            "invalid_detection_rules",
        )
    return {
        "executable_path": str(rules.get("executable_path") or "").strip(),
        "process_name": str(rules.get("process_name") or "").strip(),
        "reuse_existing": bool(rules.get("reuse_existing", False)),
        "require_descendant": bool(rules.get("require_descendant", False)),
        "require_window": bool(rules.get("require_window", False)),
        "window_title_contains": _rule_list(rules, "window_title_contains"),
        "window_class": _rule_list(rules, "window_class"),
    }


def normalize_readiness_rules(value) -> Dict:
    rules = value if isinstance(value, dict) else {}
    allowed = {"timeout_seconds", "poll_interval_seconds"}
    unknown = sorted(set(rules) - allowed)
    if unknown:
        raise LaunchChainError(
            "未知的就绪规则：{}".format("、".join(unknown)),
            "invalid_readiness_rules",
        )
    try:
        timeout = float(rules.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        interval = float(
            rules.get("poll_interval_seconds", DEFAULT_POLL_SECONDS)
        )
    except (TypeError, ValueError) as error:
        raise LaunchChainError(
            "启动链超时或轮询间隔不是数字。", "invalid_readiness_rules"
        ) from error
    if not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise LaunchChainError(
            "启动链超时必须为 0 到 {} 秒。".format(int(MAX_TIMEOUT_SECONDS)),
            "invalid_readiness_rules",
        )
    if not MIN_POLL_SECONDS <= interval <= MAX_POLL_SECONDS:
        raise LaunchChainError(
            "轮询间隔必须在 {} 到 {} 秒之间。".format(
                MIN_POLL_SECONDS, MAX_POLL_SECONDS
            ),
            "invalid_readiness_rules",
        )
    return {
        "timeout_seconds": timeout,
        "poll_interval_seconds": interval,
    }


def _trigger_arguments(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise LaunchChainError(
            "触发参数必须是字符串列表。", "chain_trigger_invalid"
        )
    if len(value) > MAX_TRIGGER_ARGUMENTS:
        raise LaunchChainError(
            "触发参数不能超过 {} 项。".format(MAX_TRIGGER_ARGUMENTS),
            "chain_trigger_invalid",
        )
    result = []
    for item in value:
        text = str(item or "")
        if "\x00" in text or len(text) > MAX_TRIGGER_ARGUMENT_CHARS:
            raise LaunchChainError(
                "触发参数为空、过长或包含非法字符。", "chain_trigger_invalid"
            )
        if text:
            result.append(text)
    if sum(len(item) for item in result) > MAX_TRIGGER_TOTAL_CHARS:
        raise LaunchChainError(
            "触发参数总长度过大。", "chain_trigger_invalid"
        )
    return result


def normalize_trigger_config(kind: str, value) -> Dict:
    """Return the small, stable trigger schema stored with a chain.

    Trigger parameters never come from the model at execution time. They are
    part of the confirmed chain digest, so changing them invalidates the plan.
    """
    trigger = str(kind or TRIGGER_AUTO)
    config = value if isinstance(value, dict) else None
    if config is None:
        raise LaunchChainError(
            "触发配置必须是 JSON 对象。", "chain_trigger_invalid"
        )
    if trigger in (TRIGGER_AUTO, TRIGGER_MANUAL):
        if config:
            raise LaunchChainError(
                "{} 触发不接受额外配置。".format(trigger),
                "chain_trigger_invalid",
            )
        return {}
    if trigger == TRIGGER_ARGUMENT:
        unknown = sorted(set(config) - {"arguments"})
        if unknown:
            raise LaunchChainError(
                "未知的参数触发配置：{}".format("、".join(unknown)),
                "chain_trigger_invalid",
            )
        arguments = _trigger_arguments(config.get("arguments"))
        if not arguments:
            raise LaunchChainError(
                "参数触发至少需要一个固定参数。", "chain_trigger_invalid"
            )
        return {"arguments": arguments}
    if trigger == TRIGGER_URI:
        unknown = sorted(set(config) - {"uri"})
        if unknown:
            raise LaunchChainError(
                "未知的 URI 触发配置：{}".format("、".join(unknown)),
                "chain_trigger_invalid",
            )
        uri = str(config.get("uri") or "").strip()
        if not uri or len(uri) > MAX_TRIGGER_URI_CHARS:
            raise LaunchChainError(
                "URI 触发需要一个长度有效的固定 URI。",
                "chain_trigger_invalid",
            )
        if any(char.isspace() or ord(char) < 32 for char in uri):
            raise LaunchChainError(
                "URI 不能包含空白或控制字符。", "chain_trigger_invalid"
            )
        parsed = urlsplit(uri)
        if (
            not parsed.scheme
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*", parsed.scheme)
            or parsed.scheme.casefold() in BLOCKED_URI_SCHEMES
        ):
            raise LaunchChainError(
                "URI 协议未通过启动白名单。", "chain_trigger_invalid"
            )
        return {"uri": uri}
    if trigger == TRIGGER_API:
        unknown = sorted(set(config) - {"api_app_id", "arguments"})
        if unknown:
            raise LaunchChainError(
                "未知的 API 触发配置：{}".format("、".join(unknown)),
                "chain_trigger_invalid",
            )
        app_id = str(config.get("api_app_id") or "").strip()
        if not app_id:
            raise LaunchChainError(
                "API 触发需要一个已登记的应用入口。",
                "chain_trigger_invalid",
            )
        return {
            "api_app_id": app_id,
            "arguments": _trigger_arguments(config.get("arguments")),
        }
    if trigger == TRIGGER_UIA:
        # The UI adapter is supplied by the runtime. Its configuration is
        # immutable here even when this installation cannot execute UIA.
        allowed = {
            "window_title_contains",
            "window_class",
            "automation_id",
            "control_name",
            "control_type",
        }
        unknown = sorted(set(config) - allowed)
        if unknown:
            raise LaunchChainError(
                "未知的 UIA 触发配置：{}".format("、".join(unknown)),
                "chain_trigger_invalid",
            )
        clean = {
            key: str(config.get(key) or "").strip() for key in allowed
        }
        if not clean["automation_id"] and not clean["control_name"]:
            raise LaunchChainError(
                "UIA 触发需要 automation_id 或 control_name。",
                "chain_trigger_invalid",
            )
        return clean
    raise LaunchChainError(
        "未知的启动链触发方式：{}".format(trigger),
        "chain_trigger_invalid",
    )


def chain_config_payload(row: Dict) -> Dict:
    return {
        "chain_id": str(row.get("chain_id") or ""),
        "name": str(row.get("name") or ""),
        "starter_app_id": str(row.get("starter_app_id") or ""),
        "target_app_id": str(row.get("target_app_id") or ""),
        "trigger_kind": str(row.get("trigger_kind") or TRIGGER_AUTO),
        "trigger_config": row.get("trigger_config") or {},
        "detection_rules": row.get("detection_rules") or {},
        "readiness_rules": row.get("readiness_rules") or {},
        "post_launch_adapter": str(row.get("post_launch_adapter") or ADAPTER_NONE),
        "enabled": bool(row.get("enabled")),
    }


def chain_config_digest(row: Dict) -> str:
    return hashlib.sha256(
        canonical_json(chain_config_payload(row)).encode("utf-8")
    ).hexdigest()


def _pid_in_tree(pid: int, ancestor_pid: int, by_pid: Dict[int, Dict]) -> bool:
    current = int(pid)
    seen = set()
    for _ in range(64):
        if current == int(ancestor_pid):
            return True
        if current in seen:
            return False
        seen.add(current)
        parent = int((by_pid.get(current) or {}).get("ppid") or 0)
        if not parent:
            return False
        current = parent
    return False


def _matches_window(window: Dict, rules: Dict) -> bool:
    titles = [item.casefold() for item in rules.get("window_title_contains") or []]
    classes = [item.casefold() for item in rules.get("window_class") or []]
    title = str(window.get("title") or "").casefold()
    class_name = str(window.get("class_name") or "").casefold()
    if titles and not any(item in title for item in titles):
        return False
    if classes and class_name not in classes:
        return False
    return True


def match_target_processes(
    processes: List[Dict],
    windows: List[Dict],
    target: Dict,
    rules: Dict,
    before_pids,
    starter_pid: int = 0,
    allow_existing: bool = False,
) -> List[Dict]:
    """Return target matches; pre-existing PIDs are excluded unless allowed."""
    before = {int(item) for item in (before_pids or [])}
    by_pid = {int(item.get("pid") or 0): item for item in processes}
    windows_by_pid: Dict[int, List[Dict]] = {}
    for window in windows:
        windows_by_pid.setdefault(int(window.get("pid") or 0), []).append(window)
    expected_path = _canonical_path(
        rules.get("executable_path") or target.get("target")
    )
    expected_name = os.path.normcase(
        str(rules.get("process_name") or os.path.basename(expected_path)).strip()
    )
    matches = []
    for process in processes:
        pid = int(process.get("pid") or 0)
        if pid <= 0 or (not allow_existing and pid in before):
            continue
        process_path = _canonical_path(process.get("exe_path"))
        process_name = os.path.normcase(str(process.get("name") or ""))
        if expected_path and process_path:
            if process_path != expected_path:
                continue
        elif expected_name and process_name != expected_name:
            continue
        if (
            rules.get("require_descendant")
            and starter_pid
            and not _pid_in_tree(pid, starter_pid, by_pid)
        ):
            continue
        attached = [
            window
            for window in windows_by_pid.get(pid, [])
            if _matches_window(window, rules)
        ]
        if rules.get("require_window") and not attached:
            continue
        matches.append(
            {
                "pid": pid,
                "ppid": int(process.get("ppid") or 0),
                "name": str(process.get("name") or ""),
                "exe_path": str(process.get("exe_path") or ""),
                "windows": attached,
                "preexisting": pid in before,
            }
        )
    return matches


class LaunchChainService:
    """CRUD, preflight and execution for registered launch chains."""

    def __init__(
        self,
        store: CompanionStore,
        catalog: AppCatalogService,
        apps: AppLaunchService,
        snapshot_processes: Optional[Callable[[], List[Dict]]] = None,
        snapshot_windows: Optional[Callable[[], List[Dict]]] = None,
        launch: Optional[Callable[[Dict], Dict]] = None,
        open_uri: Optional[Callable[[str], int]] = None,
        trigger_target: Optional[
            Callable[[str, Dict, Dict, int, Dict], Dict]
        ] = None,
        sleep: Optional[Callable[[float], None]] = None,
        clock: Optional[Callable[[], float]] = None,
        stop_event: Optional[threading.Event] = None,
    ):
        self.store = store
        self.catalog = catalog
        self.apps = apps
        self._snapshot_processes = snapshot_processes or process_snapshot
        self._snapshot_windows = snapshot_windows or window_snapshot
        self._launch = launch or launch_row
        self._open_uri = open_uri or shell_open
        self._trigger_target = trigger_target
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self.stop_event = stop_event
        self._progress_callback: Optional[Callable[[Dict], None]] = None
        self._recording_lock = threading.Lock()
        self._recordings: Dict[str, Dict] = {}

    def set_progress_callback(
        self, callback: Optional[Callable[[Dict], None]]
    ) -> None:
        """Install a best-effort observer for UI progress, never for success."""
        self._progress_callback = callback

    def _progress(self, action_id: str, stage: str, detail: str) -> None:
        callback = self._progress_callback
        if callback is None:
            return
        try:
            callback(
                {
                    "action_id": str(action_id or ""),
                    "stage": str(stage),
                    "detail": str(detail),
                    "at": utc_now(),
                }
            )
        except Exception:
            # Progress is presentation only. A disconnected browser must not
            # turn into a failed launch after the executor already started.
            return

    # -- configuration -----------------------------------------------------

    def _app(self, app_id, role: str) -> Dict:
        row = self.store.get_app_catalog(str(app_id or ""))
        if row is None:
            raise LaunchChainError(
                "启动链的{}应用不在应用目录里。".format(role),
                "chain_app_missing",
            )
        return row

    def validate_chain(
        self,
        name: str,
        starter_app_id: str,
        target_app_id: str,
        trigger_kind: str = TRIGGER_AUTO,
        trigger_config=None,
        detection_rules=None,
        readiness_rules=None,
        post_launch_adapter: str = ADAPTER_NONE,
        enabled: bool = True,
    ) -> Dict:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise LaunchChainError("启动链需要一个名称。", "chain_name_required")
        if len(clean_name) > MAX_CHAIN_NAME_CHARS:
            raise LaunchChainError(
                "启动链名称不能超过 {} 个字符。".format(MAX_CHAIN_NAME_CHARS),
                "chain_name_too_long",
            )
        starter_id = str(starter_app_id or "").strip()
        target_id = str(target_app_id or "").strip()
        if not starter_id or not target_id:
            raise LaunchChainError(
                "启动链需要起点应用和目标应用。", "chain_apps_required"
            )
        if starter_id == target_id:
            raise LaunchChainError(
                "启动链的起点和目标必须是两个不同应用。", "chain_apps_identical"
            )
        starter = self._app(starter_id, "起点")
        target = self._app(target_id, "目标")
        if str(starter.get("target_kind") or "") != APP_TARGET_EXE:
            raise LaunchChainError(
                "启动链起点必须是可执行文件，不能是 shell 特殊位置。",
                "chain_starter_kind",
            )
        if str(target.get("target_kind") or "") != APP_TARGET_EXE:
            raise LaunchChainError(
                "启动链目标必须是可执行文件，才能用进程快照确认它已启动。",
                "chain_target_kind",
            )
        trigger = str(trigger_kind or TRIGGER_AUTO)
        if trigger not in TRIGGER_KINDS:
            raise LaunchChainError(
                "未知的启动链触发方式：{}".format(trigger),
                "chain_trigger_invalid",
            )
        adapter = str(post_launch_adapter or ADAPTER_NONE)
        if adapter not in POST_LAUNCH_ADAPTERS:
            raise LaunchChainError(
                "未知的启动后适配器：{}".format(adapter),
                "chain_adapter_invalid",
            )
        clean_trigger_config = normalize_trigger_config(
            trigger, trigger_config if trigger_config is not None else {}
        )
        return {
            "name": clean_name,
            "starter_app_id": starter_id,
            "target_app_id": target_id,
            "trigger_kind": trigger,
            "trigger_config": clean_trigger_config,
            "detection_rules": normalize_detection_rules(detection_rules),
            "readiness_rules": normalize_readiness_rules(readiness_rules),
            "post_launch_adapter": adapter,
            "enabled": bool(enabled),
            "starter": starter,
            "target": target,
        }

    def save_chain(
        self,
        name: str,
        starter_app_id: str,
        target_app_id: str,
        chain_id: Optional[str] = None,
        **options,
    ) -> Dict:
        current = self.store.get_launch_chain(chain_id) if chain_id else None
        if current is None and self.store.count_launch_chains() >= MAX_LAUNCH_CHAINS:
            raise LaunchChainError(
                "启动链数量已达到上限 {}。".format(MAX_LAUNCH_CHAINS),
                "chain_limit",
            )
        clean = self.validate_chain(
            name,
            starter_app_id,
            target_app_id,
            **options,
        )
        existing = self.store.find_launch_chain(clean["name"])
        if existing is not None and existing["chain_id"] != chain_id:
            raise LaunchChainError(
                "已经有一条同名的启动链。", "chain_name_duplicate"
            )
        clean["chain_id"] = str(chain_id or new_id("chain"))
        clean["created_at"] = str((current or {}).get("created_at") or utc_now())
        clean["config_digest"] = chain_config_digest(clean)
        return self.store.upsert_launch_chain(clean)

    def set_enabled(self, chain_id, enabled: bool) -> Dict:
        current = self.store.get_launch_chain(chain_id)
        if current is None:
            raise LaunchChainError("找不到这条启动链。", "chain_missing")
        clean = self.validate_chain(
            current["name"],
            current["starter_app_id"],
            current["target_app_id"],
            trigger_kind=current["trigger_kind"],
            trigger_config=current["trigger_config"],
            detection_rules=current["detection_rules"],
            readiness_rules=current["readiness_rules"],
            post_launch_adapter=current["post_launch_adapter"],
            enabled=bool(enabled),
        )
        clean["chain_id"] = current["chain_id"]
        clean["created_at"] = current["created_at"]
        clean["config_digest"] = chain_config_digest(clean)
        return self.store.upsert_launch_chain(clean)

    def delete_chain(self, chain_id) -> bool:
        return self.store.delete_launch_chain(chain_id)

    def list_chains(self, enabled: Optional[bool] = None) -> List[Dict]:
        return self.store.list_launch_chains(enabled=enabled)

    def _trigger_app(self, row: Dict) -> Optional[Dict]:
        config = normalize_trigger_config(
            row.get("trigger_kind"), row.get("trigger_config") or {}
        )
        app_id = str(config.get("api_app_id") or "")
        if not app_id:
            return None
        app = self._app(app_id, "API 触发")
        decision = self.apps.decide(app)
        if not decision.allowed:
            raise LaunchChainError(
                decision.reason, "chain_trigger_app_not_allowed"
            )
        return app

    def _check_trigger_available(self, row: Dict) -> None:
        trigger = str(row.get("trigger_kind") or TRIGGER_AUTO)
        if trigger == TRIGGER_UIA:
            if self._trigger_target is None:
                raise LaunchChainError(
                    "这条启动链的 UIA 触发没有可用适配器。",
                    "chain_trigger_unsupported",
                )
            available = getattr(self._trigger_target, "available", None)
            if callable(available):
                try:
                    ready, reason = available()
                except Exception as error:
                    raise LaunchChainError(
                        "UIA 适配器检查失败：{}".format(error),
                        "chain_trigger_unsupported",
                    ) from error
                if not ready:
                    raise LaunchChainError(
                        str(reason or "UIA 适配器当前不可用。"),
                        "chain_trigger_unsupported",
                    )
            return
        if trigger not in IMPLEMENTED_TRIGGER_KINDS:
            raise LaunchChainError(
                "这条启动链的 {} 触发未配置可用适配器。".format(trigger),
                "chain_trigger_unsupported",
            )
        self._trigger_app(row)

    def runnable_chains(self) -> List[Dict]:
        found = []
        for row in self.store.list_launch_chains(enabled=True):
            if row.get("post_launch_adapter") != ADAPTER_NONE:
                continue
            try:
                self._check_trigger_available(row)
            except LaunchChainError:
                continue
            starter = self.store.get_app_catalog(row.get("starter_app_id"))
            target = self.store.get_app_catalog(row.get("target_app_id"))
            if (
                starter is None
                or target is None
                or starter.get("status") != APP_READY
                or target.get("status") != APP_READY
            ):
                continue
            starter_grant, _reason = self.apps.access.app_launch_grant(
                starter.get("target")
            )
            target_grant, _reason = self.apps.access.app_launch_grant(
                target.get("target")
            )
            if starter_grant is not None and target_grant is not None:
                found.append(row)
        return found

    # -- recording and target discovery ------------------------------------

    def _expire_recordings(self) -> None:
        now = self._clock()
        expired = [
            recording_id
            for recording_id, item in self._recordings.items()
            if now - float(item.get("started_clock") or now)
            > RECORDING_TTL_SECONDS
        ]
        for recording_id in expired:
            self._recordings.pop(recording_id, None)

    def start_recording(self, starter_app_id) -> Dict:
        """Snapshot a baseline, then let the user perform one manual launch.

        Recording has no side effect other than reading process and window
        metadata. It neither launches the starter nor grants the target.
        """
        starter = self._app(starter_app_id, "录制起点")
        decision = self.apps.decide(starter)
        if not decision.allowed:
            raise LaunchChainError(decision.reason, decision.code)
        with self._recording_lock:
            self._expire_recordings()
            if self._recordings:
                raise LaunchChainError(
                    "已有一条录制正在进行，请先刷新或取消它。",
                    "recording_active",
                )
            processes, windows = self._snapshots()
            recording_id = new_id("rec")
            self._recordings[recording_id] = {
                "recording_id": recording_id,
                "starter_app_id": str(starter.get("app_id") or ""),
                "baseline_pids": {
                    int(item.get("pid") or 0) for item in processes
                },
                "baseline_hwnds": {
                    int(item.get("hwnd") or 0) for item in windows
                },
                "started_at": utc_now(),
                "started_clock": self._clock(),
            }
        return self.recording_status(recording_id)

    def _recording_candidates(self, item: Dict) -> List[Dict]:
        processes, windows = self._snapshots()
        baseline_pids = set(item.get("baseline_pids") or set())
        baseline_hwnds = set(item.get("baseline_hwnds") or set())
        by_pid = {
            int(row.get("pid") or 0): row
            for row in processes
            if int(row.get("pid") or 0) > 0
        }
        changed_windows = [
            row
            for row in windows
            if int(row.get("hwnd") or 0) not in baseline_hwnds
        ]
        pids = {
            int(row.get("pid") or 0)
            for row in processes
            if int(row.get("pid") or 0)
            and int(row.get("pid") or 0) not in baseline_pids
        }
        pids.update(
            int(row.get("pid") or 0)
            for row in changed_windows
            if int(row.get("pid") or 0)
        )
        current_pid = os.getpid()
        candidates = []
        for pid in pids:
            if pid == current_pid:
                continue
            process = by_pid.get(pid) or {}
            attached = [
                {
                    "hwnd": int(row.get("hwnd") or 0),
                    "title": str(row.get("title") or ""),
                    "class_name": str(row.get("class_name") or ""),
                }
                for row in changed_windows
                if int(row.get("pid") or 0) == pid
            ]
            exe_path = str(process.get("exe_path") or "")
            app = (
                self.store.find_app_catalog(exe_path)
                if exe_path
                else None
            )
            if app is None and not attached:
                continue
            grant = None
            if app is not None:
                if str(app.get("app_id") or "") == str(
                    item.get("starter_app_id") or ""
                ):
                    continue
                grant, _reason = self.apps.access.app_launch_grant(
                    str(app.get("target") or "")
                )
            name = (
                str((app or {}).get("name") or "")
                or next(
                    (
                        window["title"]
                        for window in attached
                        if window["title"]
                    ),
                    "",
                )
                or str(process.get("name") or "")
                or "进程 {}".format(pid)
            )
            candidates.append(
                {
                    "pid": pid,
                    "ppid": int(process.get("ppid") or 0),
                    "name": name,
                    "process_name": str(process.get("name") or ""),
                    "exe_path": exe_path,
                    "windows": attached[:5],
                    "app_id": str((app or {}).get("app_id") or ""),
                    "app_name": str((app or {}).get("name") or ""),
                    "app_status": str((app or {}).get("status") or ""),
                    "app_allowed": grant is not None,
                    "selectable": bool(app),
                }
            )
        candidates.sort(
            key=lambda row: (
                not bool(row.get("selectable")),
                not bool(row.get("windows")),
                not bool(row.get("app_allowed")),
                str(row.get("name") or "").casefold(),
                int(row.get("pid") or 0),
            )
        )
        return candidates[:MAX_RECORDING_CANDIDATES]

    def recording_status(self, recording_id=None) -> Dict:
        with self._recording_lock:
            self._expire_recordings()
            if recording_id:
                item = self._recordings.get(str(recording_id))
            else:
                item = next(iter(self._recordings.values()), None)
            if item is None:
                return {
                    "active": False,
                    "recording_id": "",
                    "candidates": [],
                    "candidate_count": 0,
                }
            candidates = self._recording_candidates(item)
            return {
                "active": True,
                "recording_id": item["recording_id"],
                "starter_app_id": item["starter_app_id"],
                "started_at": item["started_at"],
                "expires_in_seconds": max(
                    0,
                    int(
                        RECORDING_TTL_SECONDS
                        - (self._clock() - float(item["started_clock"]))
                    ),
                ),
                "candidates": candidates,
                "candidate_count": len(candidates),
                "instructions": (
                    "现在手动完成一次中转应用启动目标应用的操作，"
                    "然后刷新候选。录制只生成待审核配置。"
                ),
            }

    def cancel_recording(self, recording_id=None) -> bool:
        with self._recording_lock:
            if recording_id:
                return self._recordings.pop(str(recording_id), None) is not None
            removed = bool(self._recordings)
            self._recordings.clear()
            return removed

    def detect_chain_targets(self, chain_id) -> Dict:
        """Read-only target candidates for refining a chain's detection rules."""
        row = self.store.get_launch_chain(str(chain_id or ""))
        if row is None:
            raise LaunchChainError("找不到这条启动链。", "chain_missing")
        target = self._app(row.get("target_app_id"), "目标")
        processes, windows = self._snapshots()
        rules = normalize_detection_rules(row.get("detection_rules"))
        matches = self._target_matches(
            target,
            rules,
            processes,
            windows,
            [],
            allow_existing=True,
        )
        return {
            "chain_id": row["chain_id"],
            "target_app_id": row["target_app_id"],
            "candidates": matches[:MAX_RECORDING_CANDIDATES],
            "candidate_count": len(matches),
            "ambiguous": len(matches) > 1,
        }

    def resolve_chain(self, term) -> Dict:
        text = str(term or "").strip()
        if not text:
            raise LaunchChainError("缺少启动链名称。", "chain_required")
        exact = self.store.find_launch_chain(text)
        if exact is not None:
            return exact
        matches = [
            row
            for row in self.store.list_launch_chains(limit=200)
            if text.casefold() in str(row.get("name") or "").casefold()
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise LaunchChainError("找不到这条启动链。", "chain_missing")
        raise LaunchChainError(
            "有多条启动链匹配，请使用完整名称或 chain_id。",
            "chain_ambiguous",
        )

    # -- preflight and matching -------------------------------------------

    def _snapshots(self) -> Tuple[List[Dict], List[Dict]]:
        processes = self._snapshot_processes()
        windows = self._snapshot_windows()
        if not isinstance(processes, list) or not isinstance(windows, list):
            raise LaunchChainError(
                "进程或窗口快照格式无效。", "snapshot_invalid"
            )
        return processes, windows

    def _target_matches(
        self,
        target: Dict,
        rules: Dict,
        processes: List[Dict],
        windows: List[Dict],
        before_pids,
        starter_pid: int = 0,
        allow_existing: bool = False,
    ) -> List[Dict]:
        return match_target_processes(
            processes,
            windows,
            target,
            rules,
            before_pids,
            starter_pid=starter_pid,
            allow_existing=allow_existing,
        )

    def _preflight(
        self,
        row: Dict,
        allow_preexisting_target: bool = False,
    ) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        chain_id = str((row or {}).get("chain_id") or "")
        if not chain_id:
            raise LaunchChainError("找不到这条启动链。", "chain_missing")
        if not row.get("enabled"):
            raise LaunchChainError(
                "这条启动链已经停用。", "chain_disabled"
            )
        self._check_trigger_available(row)
        if row.get("post_launch_adapter") != ADAPTER_NONE:
            raise LaunchChainError(
                "本版本不执行启动后的界面适配器。",
                "chain_adapter_unsupported",
            )
        if chain_config_digest(row) != str(row.get("config_digest") or ""):
            raise LaunchChainError(
                "启动链配置摘要不一致，请重新保存后再试。",
                "chain_config_drifted",
            )
        starter = self._app(row.get("starter_app_id"), "起点")
        target = self._app(row.get("target_app_id"), "目标")
        starter_decision = self.apps.decide(starter)
        if not starter_decision.allowed:
            raise LaunchChainError(
                starter_decision.reason, "chain_starter_not_allowed"
            )
        target_decision = self.apps.decide(target)
        if not target_decision.allowed:
            raise LaunchChainError(
                target_decision.reason, "chain_target_not_allowed"
            )
        processes, windows = self._snapshots()
        before_pids = [int(item.get("pid") or 0) for item in processes]
        rules = normalize_detection_rules(row.get("detection_rules"))
        existing = self._target_matches(
            target,
            rules,
            processes,
            windows,
            before_pids,
            allow_existing=True,
        )
        if existing and not rules.get("reuse_existing") and not allow_preexisting_target:
            raise LaunchChainError(
                "目标应用已经在运行；请先关闭它，或在检测规则中明确允许复用。",
                "chain_target_preexisting",
            )
        return processes, windows, existing

    def preview(self, proposal: ActionProposal) -> Dict:
        state = proposal.expected_state or {}
        row = self.store.get_launch_chain(str(state.get("chain_id") or ""))
        if row is None:
            error = "启动链已经不存在。"
        else:
            try:
                self._preflight(row)
                error = ""
            except LaunchChainError as exc:
                error = str(exc)
        return {
            "change": "launch" if not error else "unavailable",
            "operation": "launch-chain",
            "workspace": "",
            "relative_path": "",
            "target_path": str(state.get("target_path") or ""),
            "diff": [],
            "added": 0,
            "removed": 0,
            "truncated": False,
            "omitted_lines": 0,
            "error": error,
        }

    # -- proposal ----------------------------------------------------------

    def create_proposal(
        self,
        session_id: str,
        term,
        mode=None,
        source_message_id: Optional[int] = None,
        intent: str = "启动一条已登记的应用启动链",
        ttl_hours: float = PROPOSAL_TTL_HOURS,
    ) -> ActionProposal:
        row = self.resolve_chain(term)
        processes, _windows, _existing = self._preflight(row)
        starter = self._app(row["starter_app_id"], "起点")
        target = self._app(row["target_app_id"], "目标")
        trigger_config = normalize_trigger_config(
            row.get("trigger_kind"), row.get("trigger_config") or {}
        )
        trigger_app = self._trigger_app(row)
        expected_state = {
            "chain_id": row["chain_id"],
            "chain_digest": row["config_digest"],
            "starter_app_id": row["starter_app_id"],
            "target_app_id": row["target_app_id"],
            "starter_identity_digest": str(starter.get("identity_digest") or ""),
            "target_identity_digest": str(target.get("identity_digest") or ""),
            "trigger_kind": str(row.get("trigger_kind") or TRIGGER_AUTO),
            "trigger_config": trigger_config,
            "trigger_app_id": str(
                (trigger_app or {}).get("app_id") or ""
            ),
            "trigger_app_identity_digest": str(
                (trigger_app or {}).get("identity_digest") or ""
            ),
            "starter_target": str(starter.get("target") or ""),
            "target_path": str(target.get("target") or ""),
            "preflight_pids": [
                int(item.get("pid") or 0) for item in processes
            ],
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(hours=float(ttl_hours))
            ).isoformat(),
            "undo_reason": CHAIN_UNDO_REASON,
        }
        arguments_map = {
            "chain_id": str(row["chain_id"]),
            "source_message_id": source_message_id,
        }
        target_summary = "{}｜{} -> {}".format(
            row.get("name") or "",
            starter.get("name") or starter.get("target") or "",
            target.get("name") or target.get("target") or "",
        )
        plan = (
            "启动已登记的应用启动链。\n"
            "链路：{} -> {}\n"
            "链 ID：{}\n"
            "确认方式：进程与窗口快照；只有检测到新的目标实例才算完成。\n"
            "触发方式：{}\n"
            "超时：{} 秒\n"
            "撤销：不支持——启动的进程不由本应用托管。"
        ).format(
            starter.get("name") or starter.get("target") or "",
            target.get("name") or target.get("target") or "",
            row["chain_id"],
            str(row.get("trigger_kind") or TRIGGER_AUTO),
            normalize_readiness_rules(row.get("readiness_rules"))[
                "timeout_seconds"
            ],
        )
        digest = confirmation_digest(
            LAUNCH_CHAIN_TOOL,
            arguments_map,
            RiskLevel.LAUNCH_CHAIN.value,
            target_summary,
        )
        return ActionProposal(
            action_id=new_id("act"),
            session_id=session_id,
            mode=mode,
            intent=intent,
            tool_name=LAUNCH_CHAIN_TOOL,
            raw_arguments=dict(arguments_map),
            normalized_arguments=arguments_map,
            expected_state=expected_state,
            risk=RiskLevel.LAUNCH_CHAIN,
            target_summary=target_summary,
            reversible=False,
            user_visible_plan=plan,
            confirmation_digest=digest,
            idempotency_key=new_id("idem"),
            executor=EXECUTOR_CHAIN,
        )

    def precheck(self, proposal: ActionProposal, current_workspace) -> Tuple[str, str]:
        if proposal.tool_name != LAUNCH_CHAIN_TOOL:
            return "unknown_action", "不支持这个启动链动作。"
        state = proposal.expected_state or {}
        row = self.store.get_launch_chain(str(state.get("chain_id") or ""))
        if row is None:
            return "chain_missing", "启动链已经不存在。"
        if str(row.get("config_digest") or "") != str(
            state.get("chain_digest") or ""
        ):
            return "chain_config_drifted", "启动链配置在确认后发生了变化。"
        try:
            self._preflight(row)
        except LaunchChainError as exc:
            return exc.code, str(exc)
        return "", ""

    def _verify_digest(self, proposal: ActionProposal) -> None:
        expected = confirmation_digest(
            proposal.tool_name,
            proposal.normalized_arguments,
            RiskLevel(proposal.risk).value,
            proposal.target_summary,
        )
        if expected != proposal.confirmation_digest:
            raise LaunchChainError(
                "动作参数与确认内容不一致，已拒绝执行。", "digest_mismatch"
            )

    def _wait_for_target(
        self,
        target: Dict,
        rules: Dict,
        readiness: Dict,
        before_pids,
        starter_pid: int,
    ) -> Dict:
        timeout = float(readiness["timeout_seconds"])
        interval = float(readiness["poll_interval_seconds"])
        deadline = self._clock() + timeout
        last_error = ""
        while True:
            if self.stop_event is not None and self.stop_event.is_set():
                raise UncertainExecutionError(
                    "启动器已经启动，但等待目标时收到取消请求；"
                    "目标是否就绪尚未确认。"
                )
            try:
                processes, windows = self._snapshots()
                matches = self._target_matches(
                    target,
                    rules,
                    processes,
                    windows,
                    before_pids,
                    starter_pid=starter_pid,
                    allow_existing=bool(rules.get("reuse_existing")),
                )
            except LaunchChainError as exc:
                last_error = str(exc)
                matches = []
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise UncertainExecutionError(
                    "启动器已经启动，但检测到多个目标实例，无法确认唯一结果。"
                )
            if self._clock() >= deadline:
                detail = "：{}".format(last_error) if last_error else ""
                raise UncertainExecutionError(
                    "启动器已经启动，但在 {} 秒内没有确认目标应用就绪{}。".format(
                        int(timeout), detail
                    )
                )
            self._sleep(interval)

    def _with_arguments(self, row: Dict, arguments: List[str]) -> Dict:
        if not arguments:
            return dict(row)
        tail = subprocess.list2cmdline(arguments)
        current = str(row.get("arguments") or "").strip()
        return dict(
            row,
            arguments="{} {}".format(current, tail).strip(),
        )

    def _invoke_trigger(
        self,
        kind: str,
        starter: Dict,
        target: Dict,
        starter_pid: int,
        config: Dict,
        trigger_app: Optional[Dict],
    ) -> Dict:
        if kind == TRIGGER_UIA:
            if self._trigger_target is None:
                raise LaunchChainError(
                    "没有可用的 UIA 触发适配器。",
                    "chain_trigger_unsupported",
                )
            return self._trigger_target(
                kind, starter, target, starter_pid, config
            )
        if kind in (TRIGGER_AUTO, TRIGGER_MANUAL):
            return {"kind": kind, "automatic": False}
        if kind == TRIGGER_ARGUMENT:
            evidence = self._launch(
                self._with_arguments(starter, config["arguments"])
            )
            return {"kind": kind, "launch": evidence}
        if kind == TRIGGER_URI:
            instance = int(self._open_uri(config["uri"]) or 0)
            return {
                "kind": kind,
                "uri": config["uri"],
                "shell_instance": instance,
            }
        if kind == TRIGGER_API:
            if trigger_app is None:
                raise LaunchChainError(
                    "API 触发应用不存在。", "chain_trigger_app_missing"
                )
            evidence = self._launch(
                self._with_arguments(
                    trigger_app, config.get("arguments") or []
                )
            )
            return {
                "kind": kind,
                "app_id": trigger_app.get("app_id"),
                "launch": evidence,
            }
        raise LaunchChainError(
            "没有可用的 {} 触发适配器。".format(kind),
            "chain_trigger_unsupported",
        )

    def execute(self, proposal: ActionProposal) -> ActionResult:
        started = utc_now()
        if proposal.tool_name != LAUNCH_CHAIN_TOOL:
            raise NotExecutedError(
                "不支持的动作：{}".format(proposal.tool_name)
            )
        self._verify_digest(proposal)
        if proposal_expired(proposal.expected_state):
            raise NotExecutedError("提案已过期，请重新生成。")
        state = proposal.expected_state or {}
        row = self.store.get_launch_chain(str(state.get("chain_id") or ""))
        if row is None:
            raise NotExecutedError("启动链已经不存在，动作没有执行。")
        if str(row.get("config_digest") or "") != str(
            state.get("chain_digest") or ""
        ):
            raise NotExecutedError("启动链配置发生了变化，动作没有执行。")
        self._progress(
            proposal.action_id,
            "checking",
            "正在复核应用身份、授权和当前实例。",
        )
        try:
            processes, _windows, _existing = self._preflight(row)
        except LaunchChainError as exc:
            self._progress(proposal.action_id, "failed", str(exc))
            raise NotExecutedError(str(exc)) from exc
        starter = self._app(row["starter_app_id"], "起点")
        target = self._app(row["target_app_id"], "目标")
        trigger_config = normalize_trigger_config(
            row.get("trigger_kind"), row.get("trigger_config") or {}
        )
        trigger_app = self._trigger_app(row)
        if str(starter.get("identity_digest") or "") != str(
            state.get("starter_identity_digest") or ""
        ):
            raise NotExecutedError(
                "起点应用身份在确认后变化，动作没有执行。"
            )
        if str(target.get("identity_digest") or "") != str(
            state.get("target_identity_digest") or ""
        ):
            raise NotExecutedError(
                "目标应用身份在确认后变化，动作没有执行。"
            )
        if str((trigger_app or {}).get("identity_digest") or "") != str(
            state.get("trigger_app_identity_digest") or ""
        ):
            raise NotExecutedError(
                "API 触发应用身份在确认后变化，动作没有执行。"
            )
        before_pids = [int(item.get("pid") or 0) for item in processes]
        self._progress(
            proposal.action_id,
            "launching",
            "预检通过，正在启动中转应用。",
        )
        try:
            launch_row_value = (
                self._with_arguments(starter, trigger_config["arguments"])
                if row.get("trigger_kind") == TRIGGER_ARGUMENT
                else starter
            )
            launch = self._launch(launch_row_value)
        except (NotExecutedError, UncertainExecutionError):
            raise
        except Exception as error:
            self._progress(
                proposal.action_id,
                "failed",
                "中转应用没有成功启动：{}".format(error),
            )
            raise NotExecutedError(
                "启动器没有成功启动：{}".format(error)
            ) from error
        starter_pid = int((launch or {}).get("pid") or 0)
        try:
            trigger_kind = str(row.get("trigger_kind") or TRIGGER_AUTO)
            if trigger_kind not in (TRIGGER_AUTO, TRIGGER_MANUAL):
                self._progress(
                    proposal.action_id,
                    "triggering",
                    "中转应用已启动，正在执行 {} 触发。".format(
                        trigger_kind
                    ),
                )
            trigger_evidence = self._invoke_trigger(
                trigger_kind,
                starter,
                target,
                starter_pid,
                trigger_config,
                trigger_app,
            )
            self._progress(
                proposal.action_id,
                "waiting",
                "正在等待目标进程和窗口就绪。",
            )
            match = self._wait_for_target(
                target,
                normalize_detection_rules(row.get("detection_rules")),
                normalize_readiness_rules(row.get("readiness_rules")),
                before_pids,
                starter_pid,
            )
        except UncertainExecutionError as error:
            self._progress(proposal.action_id, "unknown", str(error))
            raise
        except Exception as error:
            self._progress(
                proposal.action_id,
                "unknown",
                "触发或确认目标应用失败：{}".format(error),
            )
            raise UncertainExecutionError(
                "启动器已经启动，但触发或确认目标应用失败：{}".format(error)
            ) from error
        name = str(row.get("name") or row["chain_id"])
        target_name = str(target.get("name") or target.get("target") or "")
        self._progress(
            proposal.action_id,
            "completed",
            "目标 {} 已确认（PID {}）。".format(
                target_name, match.get("pid")
            ),
        )
        return ActionResult(
            receipt_id=new_id("rc"),
            action_id=proposal.action_id,
            session_id=proposal.session_id,
            status="completed",
            summary="启动链「{}」已完成，目标 {} 已确认".format(
                name, target_name
            ),
            result={
                "chain_id": row["chain_id"],
                "chain_name": name,
                "starter": starter,
                "target": target,
                "starter_launch": launch,
                "trigger": trigger_evidence,
                "target_process": match,
                "undo_reason": CHAIN_UNDO_REASON,
                "after": {
                    "chain_id": row["chain_id"],
                    "target_pid": match["pid"],
                },
            },
            started_at=started,
            finished_at=utc_now(),
            undoable=False,
            undo_status="unavailable",
        )

    def undo(self, receipt: ActionResult):
        raise LaunchChainError(CHAIN_UNDO_REASON, "chain_undo_unavailable")
