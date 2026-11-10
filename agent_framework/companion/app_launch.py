"""应用启动执行器（桌面执行代理 S4）：登记即授权、启动前复核、回执只说真发生过的账。

三条线分得很开，谁也不替谁说话：

* **授权**在 ``access_grants`` 里那条 ``resource_kind='app'`` 的行上。撤销了就是不能
  启动，重新扫描、重启都救不回来。
* **身份**在 ``app_catalog`` 那一行的摘要基线上。启动前复核一次：换过文件、发布者变了
  都拒绝，并要求重新登记。
* **命令行**只从目录那一行来（快捷方式或登记时记录的参数与工作目录）。模型能选的只有
  "启动哪一个"，塞不进参数——否则一条 app-launch 授权等于任意命令行。

启动本身走 ``CreateProcessW``（可执行文件）或 ``ShellExecuteW``（回收站这类只能由
shell 解析的目标），全部 ctypes，不引第三方。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sqlite3
from typing import Dict, List, Optional, Tuple

from .access_control import (
    CAPABILITY_APP_LAUNCH,
    AccessControlService,
    CAPABILITY_FS_READ,
)
from .app_catalog import APP_MISSING, AppCatalogService
from .file_actions import NotExecutedError, UncertainExecutionError, proposal_expired
from .file_index import FileIndexService
from .models import (
    ActionResult,
    ActionProposal,
    EXECUTOR_APP,
    RiskLevel,
    confirmation_digest,
    new_id,
    utc_now,
)
from .path_safety import (
    PathSafetyError,
    WorkspaceGuard,
    app_target_key,
    assert_not_link,
    is_shell_target,
)
from .store import (
    APP_BLOCKED,
    APP_DRIFTED,
    APP_READY,
    APP_TARGET_EXE,
    CompanionStore,
)


APP_LAUNCH_TOOL = "open_app"
OPEN_PATH_TOOL = "open_path"
PROPOSAL_TTL_HOURS = 24
APP_LAUNCHABLE_LIST_LIMIT = 40

UNDO_REASON = (
    "启动的进程不由本应用托管，不会替你退出它；需要在任务管理器或程序自己里关闭。"
)

SIGNATURE_TEXT = {
    "signed": "已签名，本机核验通过",
    "unsigned": "无签名身份",
    "invalid": "签名核验未通过",
    "unverified": "有签名，但本机核验不了",
    "": "未核验",
}

# open_path 用的是 fs-read 那一档：读授权目录的权限不该同时是"在本机跑一个程序"的
# 权限。可执行文件、脚本、能挂宏的文档和会被 shell 直接解析的容器都在这一档里挡掉——
# 要跑它们走 open_app，那条线另有 app-launch 授权与身份复核。
NON_OPENABLE_SUFFIXES = frozenset(
    {
        "appx", "bat", "cmd", "com", "cpl", "dll", "docm", "dotm", "exe", "hta",
        "inf", "jar", "jse", "js", "lnk", "msc", "msi", "msp", "msu", "pif",
        "pl", "potm", "ppsm", "ps1", "ps1xml", "psm2", "psm1", "reg", "scf",
        "scr", "sh", "url", "vbe", "vbs", "ws", "wsh", "xlsm", "xlam", "xltm",
    }
)

# ShellExecuteW 的返回值不是句柄而是错误码：>32 才算起来，<=32 是那批 SE_* 之一。
SHELL_EXECUTE_OK = 32
SE_ERR_FILE_NOT_FOUND = 2
SHELL_ERROR_TEXT = {
    0: "系统内存或资源不足。",
    2: "找不到这个目标。",
    3: "目标路径有无法解析的共享句柄。",
    4: "共享冲突。",
    5: "系统拒绝访问。",
    6: "目标错误。",
    7: "参数错误。",
    8: "没有安装相应的文件关联。",
    11: "路径不存在。",
    15: "驱动器不存在。",
    22: "网络共享已断开。",
    26: "找不到指定的共享。",
    27: "共享名无效。",
    28: "DDE 事务超时。",
    29: "DDE 事务失败。",
    30: "DDE 事务失败。",
    31: "DDE 事务失败。",
    32: "与其他实例的 DDE 事务失败。",
}

# CreateProcessW 常见的几个失败码：每一个都意味着进程根本没起来，所以是不执行而不是失败。
WIN_ERROR_TEXT = {
    2: "找不到这个文件。",
    3: "找不到这个路径。",
    5: "系统拒绝访问。",
    8: "内存资源不足，无法启动。",
    14: "已在使用的程序被删除。",
    193: "这不是有效的 Win32 程序。",
    740: "这个程序需要管理员权限，本应用不会代为提权。",
    1260: "组策略禁止运行这个程序。",
}
ERROR_ELEVATION_REQUIRED = 740
SW_SHOWNORMAL = 1


class AppLaunchError(RuntimeError):
    def __init__(self, message, code="app_launch_error"):
        super().__init__(message)
        self.code = code


class _StartupInfoW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def _configure_kernel32():
    """给两个 Win32 入口补上签名。

    ctypes 默认把返回值当 32 位 int：``ShellExecuteW`` 的 HINSTANCE 在高 32 位非零的
    句柄上会被截断，错误码就看错了；``CreateProcessW`` 的出参不声明就退化成按值传递，
    进程信息读不到。
    """
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(_StartupInfoW),
        ctypes.POINTER(_ProcessInformation),
    ]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.ShellExecuteW.restype = wintypes.HINSTANCE
    kernel32.ShellExecuteW.argtypes = [
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_int,
    ]
    return kernel32


def quoted_program(target) -> str:
    """命令行第一个字段：带引号的可执行文件路径。

    ``lpApplicationName`` 留空、路径写进命令行是 CreateProcessW 唯一稳的用法，但带空格
    的路径必须自己加引号——``C:\\Program Files`` 下的程序全靠这一对引号才起得来。
    """
    return '"{}"'.format(str(target or "").strip().strip('"'))


def build_command_line(target, arguments: str = "") -> str:
    text = quoted_program(target)
    tail = str(arguments or "").strip()
    return "{} {}".format(text, tail) if tail else text


def usable_working_dir(value: str) -> Optional[str]:
    """工作目录只有真的存在才交给系统，否则宁可不给。

    快捷方式里存的工作目录经常是卸载后留下的死路径，而 CreateProcessW 会因为一个不存在
    的当前目录直接失败——要启动的是那个 exe，不是它当年的目录。
    """
    text = str(value or "").strip().strip('"')
    if not text:
        return None
    return text if os.path.isdir(text) else None


def create_process(command_line: str, working_dir: str = "") -> int:
    """起一个进程并立刻返回 pid。

    进程与线程句柄都被马上关掉：本应用不托管任何进程的生命周期，留着句柄只会变成
    没人收的泄漏。
    """
    if os.name != "nt":
        raise NotExecutedError("非 Windows 系统不能启动本机应用。")
    kernel32 = _configure_kernel32()
    buffer = ctypes.create_unicode_buffer(command_line)
    startup = _StartupInfoW()
    startup.cb = ctypes.sizeof(startup)
    info = _ProcessInformation()
    ok = kernel32.CreateProcessW(
        None,
        buffer,
        None,
        None,
        False,
        0,
        None,
        usable_working_dir(working_dir),
        ctypes.byref(startup),
        ctypes.byref(info),
    )
    if not ok:
        code = int(kernel32.GetLastError())
        detail = WIN_ERROR_TEXT.get(code, "系统返回错误码 {}。".format(code))
        raise NotExecutedError("{}启动动作没有执行。".format(detail))
    pid = int(info.dwProcessId)
    kernel32.CloseHandle(info.hThread)
    kernel32.CloseHandle(info.hProcess)
    if not pid:
        raise UncertainExecutionError("系统说进程已创建，却没给回进程号。")
    return pid


def shell_open(target: str, arguments: str = "", working_dir: str = "") -> int:
    """用 shell 的 ``open`` verb 打开一个解析名或文件夹。

    拿不到 pid：shell 可能自己起一个 DDE 服务器再转交，那个数字我们不认得，所以回执里
    也只写"已通过 shell 打开"，不编一个进程号。
    """
    if os.name != "nt":
        raise NotExecutedError("非 Windows 系统不能打开 shell 命名空间目标。")
    kernel32 = _configure_kernel32()
    value = (
        int(
            kernel32.ShellExecuteW(
                None,
                "open",
                target,
                str(arguments or "").strip() or None,
                usable_working_dir(working_dir),
                SW_SHOWNORMAL,
            )
            or 0
        )
        & 0xFFFFFFFF
    )
    if value > SHELL_EXECUTE_OK:
        return value
    if value == SE_ERR_FILE_NOT_FOUND:
        raise NotExecutedError(SHELL_ERROR_TEXT.get(value, "找不到这个目标。"))
    raise AppLaunchError(
        SHELL_ERROR_TEXT.get(value, "shell 返回错误码 {}。".format(value)),
        "shell_execute_failed",
    )


def launch_row(row: Dict) -> Dict:
    """按目录那一行真的把进程起来，返回可进回执的证据。"""
    target = str(row.get("target") or "")
    arguments = str(row.get("arguments") or "")
    working_dir = str(row.get("working_dir") or "")
    if str(row.get("target_kind") or APP_TARGET_EXE) == APP_TARGET_EXE:
        command_line = build_command_line(target, arguments)
        pid = create_process(command_line, working_dir)
        return {
            "launcher": "create_process",
            "pid": pid,
            "command_line": command_line,
        }
    instance = shell_open(target, arguments, working_dir)
    return {
        "launcher": "shell_execute",
        "pid": 0,
        "shell_instance": instance,
        "command_line": target,
    }


def openable(name_or_path) -> Tuple[bool, str]:
    """这个名字能不能用 ``open_path`` 打开——只看后缀，不看内容。

    后缀是这一档唯一负担得起的判断：整包扫描一个文件才知道它是不是脚本，那不是读取
    授权该有的代价。真正的可执行目标另有 ``open_app`` 那条带身份复核的路。
    """
    text = str(name_or_path or "").strip()
    if not text:
        return False, "缺少文件名。"
    suffix = os.path.splitext(os.path.basename(text))[1].lower().lstrip(".")
    if suffix in NON_OPENABLE_SUFFIXES:
        return False, (
            "「{}」是可执行、脚本或可挂宏的文档类型：读取授权不能用来启动它。"
            "确实需要运行，请把它登记进「允许启动」清单。".format(os.path.basename(text))
        )
    return True, ""


@dataclass(frozen=True)
class LaunchDecision:
    """一次"现在能不能启动"的结论，带着给用户看的那句话。"""

    allowed: bool
    reason: str
    code: str = ""
    row: Optional[Dict] = None


class AppLaunchService:
    """执行器：提案、复核、启动、回执。不提供撤销，理由写在回执里。"""

    def __init__(
        self,
        store: CompanionStore,
        catalog: AppCatalogService,
        access: AccessControlService,
        index: Optional[FileIndexService] = None,
    ):
        self.store = store
        self.catalog = catalog
        self.access = access
        # open_path 用的引用解析器。没有它时 open_path 直接说"本机没有文件索引"，
        # 而不是退回接受绝对路径——那只工具的安全前提就是入参只能是 file_ref。
        self.index = index

    # -- 选择条目 -----------------------------------------------------------

    def resolve(self, term) -> Optional[Dict]:
        """把 app_id、目标路径或名字解析成一条目录行。

        名字只在唯一命中时才算数：模型说"记事本"而目录里有两条的时候，替它挑一条等于
        替用户决定启动哪个程序。
        """
        text = str(term or "").strip()
        if not text:
            return None
        row = self.store.get_app_catalog(text)
        if row is not None:
            return row
        row = self.store.find_app_catalog(text)
        if row is not None:
            return row
        matches = self.catalog.lookup(text, limit=5)
        exact = [
            item
            for item in matches
            if os.path.normcase(str(item.get("name") or "")) == os.path.normcase(text)
        ]
        if len(exact) == 1:
            return exact[0]
        return matches[0] if len(matches) == 1 else None

    def decide(self, row: Optional[Dict]) -> LaunchDecision:
        """授权与身份两层的结论。

        两层的话分开说：「没登记」要去设置页点允许，「换过文件」要重新登记，混成一句
        "不能启动"用户就不知道该做哪一件。
        """
        if row is None:
            return LaunchDecision(False, "应用目录里找不到这个应用。", "app_not_found")
        status = str(row.get("status") or "")
        if status == APP_BLOCKED:
            return LaunchDecision(
                False, str(row.get("last_error") or "这一条已被禁用。"), "app_blocked", row
            )
        grant, reason = self.access.app_launch_grant(str(row.get("target") or ""))
        if grant is None:
            return LaunchDecision(False, reason, "app_not_allowed", row)
        checked = self.catalog.recheck_identity(row["app_id"])
        if not checked["ok"]:
            fresh = checked.get("row") or row
            # 三种"不行"各有各的下一步：文件不在原位（可能是移动存储没插）、换过文件
            # （要重新登记）、连基线都没有（也是重新登记）。说成一句话用户就没法对症。
            if str(fresh.get("status") or "") == APP_MISSING:
                return LaunchDecision(
                    False,
                    checked["reason"] or "目标文件当前不在原位，动作没有执行。",
                    "app_missing",
                    fresh,
                )
            if checked["changed"]:
                return LaunchDecision(False, checked["reason"], "app_drifted", fresh)
            return LaunchDecision(False, checked["reason"], "app_unchecked", fresh)
        return LaunchDecision(True, "", "", row)

    # -- 提案 ---------------------------------------------------------------

    def create_proposal(
        self,
        session_id: str,
        term,
        mode=None,
        source_message_id: Optional[int] = None,
        intent: str = "启动一个已登记的应用",
        ttl_hours: float = PROPOSAL_TTL_HOURS,
    ) -> ActionProposal:
        row = self.resolve(term)
        decision = self.decide(row)
        if not decision.allowed:
            raise AppLaunchError(decision.reason, decision.code)
        stored = decision.row or {}
        target = str(stored.get("target") or "")
        arguments = str(stored.get("arguments") or "")
        shell = is_shell_target(target)
        plan = (
            "启动已在允许启动清单里的应用。\n"
            "名称：{}\n"
            "目标：{}\n"
            "命令行：{}\n"
            "发布者：{}\n"
            "签名：{}\n"
            "撤销：不支持——本应用不托管已启动的进程，不会替你退出它。\n"
            "提示：只能启动清单里那一个程序，参数来自登记时记录的内容，不能由对话临时指定。"
        ).format(
            stored.get("name") or "",
            target,
            target if shell else build_command_line(target, arguments),
            stored.get("publisher") or "无签名身份",
            SIGNATURE_TEXT.get(str(stored.get("signature_state") or ""), "未核验"),
        )
        arguments_map = {
            "app_id": str(stored.get("app_id") or ""),
            "source_message_id": source_message_id,
        }
        target_summary = "{}｜{}".format(stored.get("name") or "", target)
        digest = confirmation_digest(
            APP_LAUNCH_TOOL, arguments_map, RiskLevel.APP_LAUNCH.value, target_summary
        )
        return ActionProposal(
            action_id=new_id("act"),
            session_id=session_id,
            mode=mode,
            intent=intent,
            tool_name=APP_LAUNCH_TOOL,
            raw_arguments=dict(arguments_map),
            normalized_arguments=arguments_map,
            expected_state={
                "app_id": str(stored.get("app_id") or ""),
                "target": target,
                "target_key": str(stored.get("target_key") or ""),
                "identity_digest": str(stored.get("identity_digest") or ""),
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(hours=float(ttl_hours))
                ).isoformat(),
                "undo_reason": UNDO_REASON,
            },
            risk=RiskLevel.APP_LAUNCH,
            target_summary=target_summary,
            reversible=False,
            user_visible_plan=plan,
            confirmation_digest=digest,
            idempotency_key=new_id("idem"),
            executor=EXECUTOR_APP,
        )

    def precheck(self, proposal: ActionProposal, current_workspace) -> Tuple[str, str]:
        """没有全局工作区可比：目标由目录那一行自己带着，执行前逐条复核。"""
        return "", ""

    def launchable(self, limit: int = APP_LAUNCHABLE_LIST_LIMIT) -> List[Dict]:
        """「允许启动」清单里现在还起得来的条目，给工具描述和设置页读。

        只列授权过的：发现（扫描）到的应用有几十上百条，把它们都塞进提示词既没用也
        是把本机装了些什么软件告诉模型。授权行是唯一的真相，所以这里逐条拿授权表里的
        目标去目录里配对，不信任扫描缓存。
        """
        found = []
        for grant in self.access.launchable_grants():
            row = self.store.find_app_catalog(str(grant.path or ""))
            if row is None:
                continue
            found.append(row)
            if len(found) >= max(1, int(limit)):
                break
        return found

    def launchable_names(self) -> List[str]:
        return sorted(
            {str(row.get("name") or row.get("target") or "") for row in self.launchable()}
            - {""}
        )

    # -- 打开文档 -----------------------------------------------------------

    def open_ref(self, file_ref) -> Dict:
        """按 file_ref 用系统关联打开授权目录里的文件或文件夹。

        这一只工具不建提案（风险档是 ``fs-read``，直接回答），所以把关全在动手之前：
        引用必须落在授权目录里、后缀必须不是可执行/脚本/宏文档、目标必须还是索引里
        那一个文件。
        """
        if self.index is None:
            raise AppLaunchError(
                "本机没有启用文件索引，按引用打不开东西。", "index_unavailable"
            )
        row, grant, reason = self.index.resolve_reference(file_ref, CAPABILITY_FS_READ)
        if row is None or grant is None:
            raise AppLaunchError(reason or "文件引用无法解析。", "file_ref_unresolved")
        usable, why = openable(str(row.get("name") or row.get("rel_path") or ""))
        if not usable:
            raise AppLaunchError(why, "not_openable")
        try:
            target = WorkspaceGuard(grant.canonical_root).resolve(
                str(row["rel_path"]).replace("\\", "/")
            )
        except PathSafetyError as error:
            raise AppLaunchError(str(error), "path_rejected") from error
        if not os.path.exists(target):
            raise AppLaunchError("文件已不存在，动作没有执行。", "file_missing")
        try:
            assert_not_link(target)
        except PathSafetyError as error:
            raise AppLaunchError(str(error), "reparse_point") from error
        try:
            info = os.lstat(target)
        except OSError as error:
            raise AppLaunchError("读不到这个目标：{}".format(error), "stat_failed")
        recorded = (str(row.get("volume_id") or ""), str(row.get("file_id") or ""))
        if all(part not in ("", "0") for part in recorded) and (
            str(info.st_dev) != recorded[0] or str(info.st_ino) != recorded[1]
        ):
            raise AppLaunchError(
                "文件身份与索引不一致（可能已被替换），请重新搜索。", "identity_changed"
            )
        return {
            "name": str(row.get("name") or ""),
            "rel_path": str(row.get("rel_path") or ""),
            "grant_id": grant.grant_id,
            "target": str(target),
            "shell_instance": shell_open(str(target)),
        }

    def preview(self, proposal: ActionProposal) -> Dict:
        state = proposal.expected_state or {}
        row = self.store.get_app_catalog(str(state.get("app_id") or ""))
        decision = self.decide(row)
        return {
            "change": "launch" if decision.allowed else "unavailable",
            "operation": "launch",
            "workspace": "",
            "relative_path": "",
            "target_path": str((row or {}).get("target") or ""),
            "diff": [],
            "added": 0,
            "removed": 0,
            "truncated": False,
            "omitted_lines": 0,
            "error": "" if decision.allowed else decision.reason,
        }

    # -- 执行 ---------------------------------------------------------------

    def _verify_digest(self, proposal: ActionProposal) -> None:
        expected = confirmation_digest(
            proposal.tool_name,
            proposal.normalized_arguments,
            RiskLevel(proposal.risk).value,
            proposal.target_summary,
        )
        if expected != proposal.confirmation_digest:
            raise AppLaunchError("动作参数与确认内容不一致，已拒绝执行。", "digest_mismatch")

    def execute(self, proposal: ActionProposal) -> ActionResult:
        started = utc_now()
        if proposal.tool_name != APP_LAUNCH_TOOL:
            raise NotExecutedError("不支持的动作：{}".format(proposal.tool_name))
        self._verify_digest(proposal)
        if proposal_expired(proposal.expected_state):
            raise NotExecutedError("提案已过期，请重新生成。")
        state = proposal.expected_state or {}
        app_id = str(state.get("app_id") or "")
        decision = self.decide(self.store.get_app_catalog(app_id))
        if not decision.allowed:
            # 全部拦在起进程之前：没动手的动作记 not_executed，不假装失败在别处。
            raise NotExecutedError(decision.reason)
        stored = decision.row or {}
        if str(state.get("identity_digest") or "") != str(
            stored.get("identity_digest") or ""
        ):
            raise NotExecutedError(
                "这个应用的身份在提案之后又核验过并变了，请重新发起。"
            )
        evidence = launch_row(stored)
        name = str(stored.get("name") or stored.get("target") or "")
        summary = (
            "已启动 {}（进程号 {}）".format(name, evidence["pid"])
            if evidence["pid"]
            else "已通过 shell 打开 {}".format(name)
        )
        return ActionResult(
            receipt_id=new_id("rc"),
            action_id=proposal.action_id,
            session_id=proposal.session_id,
            status="completed",
            summary=summary,
            result={
                "app_id": app_id,
                "name": name,
                "target": str(stored.get("target") or ""),
                "target_kind": str(stored.get("target_kind") or ""),
                "publisher": str(stored.get("publisher") or ""),
                "signature_state": str(stored.get("signature_state") or ""),
                "identity_digest": str(stored.get("identity_digest") or ""),
                "undo_reason": UNDO_REASON,
                "after": None,
                **evidence,
            },
            started_at=started,
            finished_at=utc_now(),
            undoable=False,
            undo_status="unavailable",
        )

    def undo(self, receipt: ActionResult):
        raise AppLaunchError(UNDO_REASON, "app_undo_unavailable")


def capability_list(value: str) -> List[str]:
    """``access_grants.capabilities`` 那一列读成名字列表；坏 JSON 当成没有能力。"""
    try:
        data = json.loads(value or "[]")
    except ValueError:
        return []
    return [str(item) for item in data] if isinstance(data, list) else []


def read_launch_state(connection) -> Dict:
    """只读地描述应用目录与「允许启动」清单，供 doctor 使用。

    自检不算身份、不扫描、也不改状态：一次自检把"移动硬盘没插"写成 ``missing``，
    下次插上以后就再也看不出它曾经不在。
    """
    try:
        rows = connection.execute(
            """
            SELECT app_id, name, target, target_kind, status, last_error,
                   identity_digest, publisher, signature_state
            FROM app_catalog
            ORDER BY name, target
            """
        ).fetchall()
        grants = connection.execute(
            """
            SELECT path, status, capabilities
            FROM access_grants
            WHERE resource_kind = 'app'
            ORDER BY status, path
            """
        ).fetchall()
    except sqlite3.Error:
        return {
            "available": False,
            "reason": "app_catalog 表不存在（数据库尚未迁移到 v15）",
        }
    allowed = {
        app_target_key(str(row["path"]))
        for row in grants
        if str(row["status"] or "") == "active"
        and CAPABILITY_APP_LAUNCH in capability_list(str(row["capabilities"] or ""))
    }
    allowed.discard("")
    launchable = [
        row
        for row in rows
        if str(row["status"] or "") == APP_READY
        and app_target_key(str(row["target"])) in allowed
    ]
    return {
        "available": True,
        "reason": "",
        "total": len(rows),
        "ready": len([row for row in rows if str(row["status"]) == APP_READY]),
        "missing": len([row for row in rows if str(row["status"]) == APP_MISSING]),
        "drifted": len([row for row in rows if str(row["status"]) == APP_DRIFTED]),
        "blocked": len([row for row in rows if str(row["status"]) == APP_BLOCKED]),
        "grant_count": len(allowed),
        "launchable_count": len(launchable),
        "launchable": [
            {
                "name": str(row["name"] or ""),
                "target": str(row["target"] or ""),
                "publisher": str(row["publisher"] or ""),
                "signature_state": str(row["signature_state"] or ""),
                "has_baseline": bool(str(row["identity_digest"] or "")),
            }
            for row in launchable[:APP_LAUNCHABLE_LIST_LIMIT]
        ],
        "without_baseline": len(
            [row for row in launchable if not str(row["identity_digest"] or "")]
        ),
        "drifted_rows": [
            {
                "name": str(row["name"] or ""),
                "target": str(row["target"] or ""),
                "reason": str(row["last_error"] or ""),
            }
            for row in rows
            if str(row["status"]) in (APP_DRIFTED, APP_BLOCKED)
        ][:APP_LAUNCHABLE_LIST_LIMIT],
    }


def launch_state_report(database_path) -> Dict:
    """以只读方式打开数据库并描述启动状态。"""
    uri = "{}?mode=ro".format(Path(database_path).resolve().as_uri())
    connection = sqlite3.connect(uri, uri=True, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        return read_launch_state(connection)
    finally:
        connection.close()
