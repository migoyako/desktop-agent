r"""应用发现：把机器上"已经存在的入口"整理成一份目录，不掺授权。

三个来源（用户 2026-11-05 定的那一档）：

* 开始菜单 ``.lnk``——自己按 MS-SHLNK 解，不引 Shell COM，也不引第三方依赖。
* 注册表 ``App Paths``——浏览器、编辑器这类装了路径的程序的权威入口。
* 系统特殊位置——回收站、此电脑、控制面板等只在 shell 命名空间里存在的目标。

外加用户手动登记。**发现不等于授权**：这里只往 ``app_catalog`` 写元数据，"允不允许
启动"永远由 ``access_grants`` 那张表说了算，所以扫得再多也不会自己长出启动权。

UWP/MSIX 的 AUMID 枚举本轮不做，计划书里也是这么划的。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import struct
from typing import Dict, Iterable, List, Optional, Tuple

from . import app_identity
from .path_safety import app_target_key, is_shell_target, resource_identity
from .store import (
    APP_CATALOG_MAX_ENTRIES,
    APP_CATALOG_NAME_MAX_CHARS,
    APP_DRIFTED,
    APP_MISSING,
    APP_READY,
    APP_SOURCE_APP_PATHS,
    APP_SOURCE_MANUAL,
    APP_SOURCE_SPECIAL_FOLDER,
    APP_SOURCE_START_MENU,
    APP_TARGET_EXE,
    APP_TARGET_KNOWN_FOLDER,
    APP_TARGET_SHELL,
    CompanionStore,
)

class AppCatalogError(RuntimeError):
    def __init__(self, message, code="app_catalog_error"):
        super().__init__(message)
        self.code = code

HEADER_SIZE = 0x4C
HEADER_ID = 0x0000004C

FLAG_IDLIST = 0x0001
FLAG_LINK_INFO = 0x0002
FLAG_NAME = 0x0004
FLAG_RELATIVE_PATH = 0x0008
FLAG_WORKING_DIR = 0x0010
FLAG_ARGUMENTS = 0x0020
FLAG_ICON = 0x0040
FLAG_UNICODE = 0x0080
FLAG_FORCE_NO_LINKINFO = 0x0100
FLAG_EXP_STRING = 0x0200

# LinkInfo 头部各字段的位置。MS-SHLNK 把本地基本路径的偏移写在 +0x18，但本机 232 个
# 真实快捷方式对着 Shell COM 的解析结果逐字节量下来，+0x18 处一律不是那个偏移，而
# +0x10 处的值在 180/182 个可解析的快捷方式上都精确指向 Shell 给出的那条路径
# （对真实快捷方式样本的扫描），且 +0x04 恒为 28、+0x08 恒是 1/3 这类标志位组合。
# 所以这里按量出来的位置读，并用"解出来的路径必须真的存在"再兜一层——读歪了只会变成
# missing，不会变成一个能启动的条目。
LI_HEADER_SIZE = 0x00
LI_LOCAL_BASE_OFFSET = 0x10
LI_MIN_HEADER_SIZE = 28

MAX_LNK_BYTES = 1 * 1024 * 1024
# 环境变量数据块的签名：有些系统快捷方式只在它里面留下路径。
ENV_BLOCK_SIGNATURE = b"\x01\x00\x00\xa0"
START_MENU_LIMIT = 4000

# 只读这几个键；App Paths 下还有 Path 值给子进程找 DLL，不是启动入口。
APP_PATHS_KEYS = (
    (0x80000002, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    (0x80000002, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
    (0x80000001, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
)

# 系统特殊位置：能填进"打开"的解析名，启动走 ShellExecuteW 而不是 CreateProcessW。
SPECIAL_LOCATIONS = (
    ("回收站", "shell:::{645FF040-509C-4060-A8D2-06E02583C470}"),
    ("此电脑", "shell:MyComputerFolder"),
    ("控制面板", "shell:ControlPanelFolder"),
    ("网络", "shell:NetworkFolder"),
    ("字体", "shell:Fonts"),
    ("用户主页", "shell:Personal"),
    ("启动文件夹", "shell:Startup"),
)


def _u16(data: bytes, at: int) -> int:
    if at + 2 > len(data):
        raise ValueError("快捷方式数据在 {} 处被截断".format(at))
    return struct.unpack_from("<H", data, at)[0]


def _u32(data: bytes, at: int) -> int:
    if at + 4 > len(data):
        raise ValueError("快捷方式数据在 {} 处被截断".format(at))
    return struct.unpack_from("<I", data, at)[0]


def _ansi(raw: bytes) -> str:
    """LinkInfo 里的路径永远是系统 ANSI 码页，跟头部的 Unicode 标志位无关。

    那个标志位只管 StringData 段的名称与参数。按 UTF-16 去解这里会读出一串乱码，
    而中文用户名这类路径恰恰只有按码页解才对——本机用 ``mbcs``。
    """
    text = raw.split(b"\x00", 1)[0]
    try:
        return text.decode("mbcs").strip().strip("\x00")
    except (LookupError, UnicodeDecodeError):
        return text.decode("utf-8", errors="replace").strip().strip("\x00")


def _idlist_end(data: bytes) -> int:
    """走到 IDList 的结束标记，返回 LinkInfo 的起点。

    ``ItemIDListSize`` 只数各项目、不含结尾那个 0，所以不能拿它直接偏移；逐项走到
    长度为 0 的那一项才是正解。
    """
    at = HEADER_SIZE + 2
    while at + 2 <= len(data):
        size = _u16(data, at)
        if size == 0:
            return at + 2
        at += size
    return -1


def _read_local_base_path(data: bytes, start: int) -> str:
    header_size = _u32(data, start + LI_HEADER_SIZE)
    if header_size < LI_MIN_HEADER_SIZE or start + header_size > len(data):
        return ""
    local_offset = _u32(data, start + LI_LOCAL_BASE_OFFSET)
    if not local_offset or start + local_offset >= len(data):
        return ""
    end = data.find(b"\x00", start + local_offset)
    if end < 0:
        end = len(data)
    return _ansi(data[start + local_offset:end])


def _nul_index(region: bytes, unit: int) -> int:
    """返回这一段里第一个 NUL 字符的下标（按字符算），没有则 -1。"""
    if unit == 2:
        for index in range(0, len(region) - 1, 2):
            if region[index] == 0 and region[index + 1] == 0:
                return index // 2
        return -1
    at = region.find(b"\x00")
    return -1 if at < 0 else at


def _read_counted_string(data: bytes, at: int, count: int, unicode_names: bool) -> Tuple[str, int]:
    """读一个计数字符串，返回 (文本, 下一个字段的起点)。

    规范说 StringData 的 Count 含结尾那个 NUL，本机量下来正好相反：222 条真实快捷方式
    的 working_dir / arguments 里，Count 一律等于纯字符数。
    两种写法都可能遇到，所以不照 Count 定长，而是"最多读 Count 个字符，然后看紧跟的
    那一格是不是 NUL"：是就把它吃掉，不是就停在文本末尾。按规范减一会把 Firefox 的
    工作目录读成 "Mozilla Firefo"、把 IDLE 参数结尾的引号弄丢，量出来的就是这个。
    """
    unit = 2 if unicode_names else 1
    if count < 1 or at + count * unit > len(data):
        # 声明的长度已经超出文件剩余部分：偏移本身落错了地方（LinkInfo 尺寸写歪就是
        # 这样）。与其从路径尾巴上拼出一串乱码当别名，不如不读。
        return "", at
    window = min((count + 1) * unit, len(data) - at)
    region = data[at:at + window]
    nul = _nul_index(region, unit)
    chars = count if nul < 0 or nul >= count else nul
    text = region[:chars * unit]
    if unicode_names:
        value = text.decode("utf-16-le", errors="replace").strip("\x00").strip()
    else:
        value = _ansi(text)
    consumed = chars + 1 if nul == chars else chars
    return value, at + consumed * unit


def _resolve_relative_target(link_path, relative_path: str, working_dir: str = "") -> str:
    """RelativePath 到底相对谁：规范说相对快捷方式自己的目录，本机量出来不止这一个口径。

    本机的 bilibili / mitmproxy 把这一串写成相对盘根（``..\\Program Files\\...``），
    Nsight 那批相对的是工作目录。按 快捷方式目录 → 工作目录 → 盘根 依次试，取第一个
    真的存在的文件（对 COM 地面真值量过：三种口径都对不上却恰好
    撞上同名文件的情况，本机 232 条里一条没有）。一个也解不出来就留在"只记录了相对
    路径"那条原因上——卸载干净的应用不该冒出一个可启动的条目。
    """
    if not link_path or not str(relative_path or "").strip():
        return ""
    text = str(relative_path).strip()
    try:
        absolute = os.path.abspath(str(link_path))
        directory = os.path.dirname(absolute)
        bases = [directory]
        work = expand_environment(working_dir)
        if work and os.path.isdir(work):
            bases.append(work)
        bases.append(os.path.splitdrive(absolute)[0] + os.sep)
        for base in bases:
            candidate = os.path.normpath(os.path.join(base, text))
            if os.path.isfile(candidate):
                return candidate
    except (OSError, ValueError):
        return ""
    return ""


def _read_env_target(data: bytes) -> str:
    """从环境变量数据块里取目标：有些系统快捷方式只在这儿留路径。

    自带的 Accessibility / Administrative Tools 头部既没标目标项列表也没标链接信息，
    Shell 却照样报得出 ``C:\\Windows\\system32\\narrator.exe``——那条路径在签名
    ``0xA0000001`` 的数据块里，而且是 ``%windir%`` 这种没展开的形式。展开之后确实有这
    个文件才认，不然一块出现在名字串里的巧合字节就能凭空捏出一个"应用"。
    """
    at = 0
    while True:
        at = data.find(ENV_BLOCK_SIGNATURE, at)
        if at < 0:
            return ""
        end = data.find(b"\x00", at + len(ENV_BLOCK_SIGNATURE))
        if end < 0:
            return ""
        raw = _ansi(data[at + len(ENV_BLOCK_SIGNATURE):end])
        candidate = expand_environment(raw)
        if candidate and os.path.isfile(candidate):
            return candidate
        # Continue after this environment block so the next search always
        # advances and an empty block cannot trap the loop.
        at = end + 1


def _read_strings(data: bytes, at: int, flags: int, unicode_names: bool, result: Dict) -> int:
    """顺序读 Name / RelativePath / WorkingDir / Arguments 四个计数字符串。

    越界就停，不抛：LinkInfo 尺寸写歪的快捷方式（本机的"控制面板"就是）在这里读出
    一个荒谬的偏移，但目标路径本身已经拿到了——为了丢掉的后缀字段把整条记录作废不划算。
    """
    for key, flag in (
        ("name", FLAG_NAME),
        ("relative_path", FLAG_RELATIVE_PATH),
        ("working_dir", FLAG_WORKING_DIR),
        ("arguments", FLAG_ARGUMENTS),
    ):
        if not flags & flag:
            continue
        if at + 2 > len(data):
            return at
        count = _u16(data, at)
        text, next_at = _read_counted_string(data, at + 2, count, unicode_names)
        if next_at == at + 2 and not text:
            # 偏移撞在文件边界上，读不动了；后面的字段只会读到同样的地方，一并放弃。
            return at
        result[key] = text
        at = next_at
    return at


def parse_lnk_bytes(data: bytes, link_path=None) -> Dict:
    """解析一个 ``.lnk`` 的字节；形状奇怪但不报错，用 ``reason`` 说明为什么没有目标。

    shell 命名空间的快捷方式（控制面板项、Store 应用）根本不含文件路径，这不是解析
    失败——调用方要的是"跳过并记下原因"，而不是把它当成一条坏数据。

    ``link_path`` 只在一个地方有用：基本路径为空、只有相对路径的那类快捷方式，相对的是
    快捷方式自己所在的目录，不是当前工作目录。没有它就只能放弃那一条。
    """
    result = {
        "target": "",
        "name": "",
        "relative_path": "",
        "working_dir": "",
        "arguments": "",
        "shell_item": False,
        "reason": "",
    }
    if len(data) < HEADER_SIZE + 4:
        result["reason"] = "文件太小，不是有效的快捷方式。"
        return result
    if _u32(data, 0) != HEADER_ID:
        result["reason"] = "文件头不是快捷方式标识。"
        return result
    try:
        flags = _u16(data, 20)
    except ValueError as error:
        result["reason"] = str(error)
        return result
    unicode_names = bool(flags & FLAG_UNICODE)
    result["shell_item"] = bool(flags & FLAG_EXP_STRING)

    try:
        start = HEADER_SIZE
        if flags & FLAG_IDLIST:
            start = _idlist_end(data)
            if start < 0:
                result["reason"] = "目标项列表没有结束标记。"
                return result
        if flags & FLAG_LINK_INFO and not flags & FLAG_FORCE_NO_LINKINFO:
            result["target"] = _read_local_base_path(data, start)
        # StringData 紧跟在 LinkInfo 之后；没有 LinkInfo 段时它就紧跟 IDList 的结束标记
        # （连 IDList 都没有时直接紧跟头部）。少这一层判断的话，无 LinkInfo 的快捷方式
        # 会把名称段的前 4 字节当尺寸跳过去。
        link_size = _u32(data, start) if flags & FLAG_LINK_INFO and start + 4 <= len(data) else 0
        after = start + link_size if link_size else start
        _read_strings(data, after, flags, unicode_names, result)
        if not result["target"] and result["relative_path"]:
            # VolumeInfo 把基本路径写成空（本机微信、哔哩哔哩、mitmproxy 都是），真实
            # 路径只剩这一串 "..\" ——Shell 的做法是相对快捷方式自己的目录去解它。
            result["target"] = _resolve_relative_target(
                link_path, result["relative_path"], result["working_dir"]
            )
        if not result["target"]:
            result["target"] = _read_env_target(data)
        if not result["target"] and result["relative_path"]:
            # 便携装的应用只有相对路径，相对快捷方式自己所在目录解析。
            result["reason"] = "快捷方式只记录了相对路径。"
        elif not result["target"]:
            result["reason"] = (
                "快捷方式指向 shell 命名空间，没有文件路径。"
                if result["shell_item"]
                else "快捷方式没有记录可执行文件路径。"
            )
        return result
    except ValueError as error:
        result["reason"] = str(error)
        return result


def parse_lnk_file(path) -> Dict:
    target = Path(path)
    try:
        size = target.stat().st_size
        if size > MAX_LNK_BYTES:
            return {"target": "", "reason": "快捷方式异常地大，已跳过。"}
        data = target.read_bytes()
    except OSError as error:
        return {"target": "", "reason": "读取快捷方式失败：{}".format(error)}
    return parse_lnk_bytes(data, target)


def expand_environment(value: str) -> str:
    """展开 ``%USERPROFILE%`` 这类串；注册表与快捷方式都可能给出没展开的形式。

    不用 ``os.path.expandvars``：它只认 ``%VAR%`` 且不处理 shell 变量名，注册表里
    ``%SystemRoot%`` 这种大小写混写的键名照样得靠系统自己展开。
    """
    text = str(value or "").strip()
    if not text or os.name != "nt":
        return text
    kernel32 = ctypes.windll.kernel32
    kernel32.ExpandEnvironmentStringsW.restype = wintypes.DWORD
    kernel32.ExpandEnvironmentStringsW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        wintypes.DWORD,
    ]
    size = int(kernel32.ExpandEnvironmentStringsW(text, None, 0))
    if size <= 0:
        return text
    buffer = ctypes.create_unicode_buffer(size)
    kernel32.ExpandEnvironmentStringsW(text, buffer, size)
    return buffer.value.strip()


def quote_stripped(value: str) -> str:
    """App Paths 的默认值常带引号，也常拖一个参数尾巴。"""
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith('"'):
        end = text.find('"', 1)
        return text[1:end] if end > 0 else text.strip('"')
    # 没加引号时，第一个空格之后可能就是参数。
    parts = text.split(".exe ", 1)
    if len(parts) == 2:
        return parts[0] + ".exe"
    return text


def start_menu_roots() -> List[Path]:
    return [
        Path(os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs")),
        Path(os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs")),
    ]


def iter_start_menu_links(roots: Optional[Iterable[Path]] = None) -> List[Path]:
    found: List[Path] = []
    for root in list(roots or start_menu_roots()):
        try:
            if not root.is_dir():
                continue
            for item in sorted(root.rglob("*.lnk")):
                found.append(item)
                if len(found) >= START_MENU_LIMIT:
                    return found
        except OSError:
            continue
    return found


def read_app_paths() -> List[Tuple[str, str, str]]:
    """注册表 App Paths：``(名称, 可执行文件, 工作目录)``。

    读不到就返回空表——这不是错误，非 Windows 与受限注册表本来就没有这些键。
    """
    if os.name != "nt":
        return []
    import winreg

    entries: List[Tuple[str, str, str]] = []
    seen = set()
    for hive, sub_key in APP_PATHS_KEYS:
        try:
            with winreg.OpenKey(hive, sub_key) as parent:
                for index in range(winreg.QueryInfoKey(parent)[0]):
                    try:
                        name = winreg.EnumKey(parent, index)
                    except OSError:
                        continue
                    try:
                        with winreg.OpenKey(parent, name) as leaf:
                            value, _ = winreg.QueryValueEx(leaf, None)
                            working_dir = ""
                            try:
                                working_dir = str(winreg.QueryValueEx(leaf, "Path")[0])
                            except OSError:
                                working_dir = ""
                    except OSError:
                        continue
                    target = quote_stripped(expand_environment(str(value or "")))
                    if not target:
                        continue
                    key = app_target_key(target)
                    if key in seen:
                        continue
                    seen.add(key)
                    entries.append((name, target, expand_environment(working_dir)))
        except OSError:
            continue
    return entries


def _entry(name: str, target: str, kind: str, source: str, source_path: str = "", **extra) -> Dict:
    """一条目录条目。签名/发布者这里一律不填——那是登记那一刻才算的东西。"""
    key = app_target_key(target)
    exists = kind == APP_TARGET_EXE and os.path.isfile(target)
    status = APP_READY if (kind != APP_TARGET_EXE or exists) else APP_MISSING
    row = {
        "name": str(name or "")[:APP_CATALOG_NAME_MAX_CHARS],
        "target": target,
        "target_key": key,
        "target_kind": kind,
        "source": source,
        "source_path": source_path,
        "status": status,
        "arguments": str(extra.get("arguments") or ""),
        "working_dir": str(extra.get("working_dir") or ""),
        "aliases": list(extra.get("aliases") or []),
        "last_error": "" if status == APP_READY else "目标文件当前不在原位。",
    }
    if kind == APP_TARGET_EXE and exists:
        info = app_identity.version_info(target)
        row["file_version"] = info.get("file_version") or ""
        try:
            stat = os.stat(target)
        except OSError:
            row["status"] = status = APP_MISSING
            row["last_error"] = "目标文件当前不在原位。"
        else:
            row["size_bytes"] = int(stat.st_size)
            row["mtime"] = str(int(stat.st_mtime))
    return row


def discovered_entries(
    links: Optional[Iterable[Path]] = None,
    app_paths: Optional[Iterable[Tuple[str, str, str]]] = None,
    include_special: bool = True,
) -> Tuple[List[Dict], List[Dict]]:
    """返回 ``(目录条目, 跳过记录)``；跳过记录带来源与原因，供 doctor 解释数量差。"""
    rows: List[Dict] = []
    skipped: List[Dict] = []

    for link in list(iter_start_menu_links() if links is None else links):
        parsed = parse_lnk_file(link)
        target = str(parsed.get("target") or "").strip()
        if not target:
            skipped.append(
                {
                    "source": APP_SOURCE_START_MENU,
                    "source_path": str(link),
                    "name": link.stem,
                    "reason": parsed.get("reason") or "没有解析出目标。",
                }
            )
            continue
        stem = link.stem
        rows.append(
            _entry(
                stem,
                target,
                APP_TARGET_EXE,
                APP_SOURCE_START_MENU,
                str(link),
                arguments=parsed.get("arguments") or "",
                working_dir=parsed.get("working_dir") or "",
                aliases=[parsed["name"]] if parsed.get("name") and parsed["name"] != stem else [],
            )
        )

    for name, target, working_dir in list(read_app_paths() if app_paths is None else app_paths):
        rows.append(
            _entry(
                name,
                target,
                APP_TARGET_EXE,
                APP_SOURCE_APP_PATHS,
                "",
                working_dir=working_dir,
            )
        )

    if include_special:
        for name, target in SPECIAL_LOCATIONS:
            rows.append(_entry(name, target, APP_TARGET_SHELL, APP_SOURCE_SPECIAL_FOLDER, ""))

    return rows, skipped


class AppCatalogService:
    """把发现结果落进 ``app_catalog``，并提供查名字的入口。"""

    def __init__(self, store: CompanionStore, max_entries: int = APP_CATALOG_MAX_ENTRIES):
        self.store = store
        self.max_entries = max(1, int(max_entries))

    def scan(
        self,
        links: Optional[Iterable[Path]] = None,
        app_paths: Optional[Iterable[Tuple[str, str, str]]] = None,
        include_special: bool = True,
    ) -> Dict:
        """重新发现一轮。只增不改授权，撤销过的应用也不会被这次扫描救回来。"""
        rows, skipped = discovered_entries(links, app_paths, include_special)
        # 同名目标在多个来源里各出现一次时，保留第一条有文件的那次。
        deduped: Dict[str, Dict] = {}
        for row in rows:
            key = row["target_key"]
            if key in deduped and deduped[key]["status"] == APP_READY and row["status"] != APP_READY:
                continue
            deduped.setdefault(key, row)
        ordered = sorted(deduped.values(), key=lambda item: item["name"])[: self.max_entries]
        written = self.store.upsert_app_catalog(ordered)
        return {
            "discovered": len(ordered),
            "written": written,
            "skipped": len(skipped),
            "skipped_samples": skipped[:10],
            "truncated": len(deduped) > len(ordered),
            "total": self.store.count_app_catalog(),
        }

    def lookup(self, term: str, limit: int = 20) -> List[Dict]:
        return self.store.search_app_catalog(term=term, status=APP_READY, limit=limit)

    def get(self, app_id) -> Optional[Dict]:
        return self.store.get_app_catalog(app_id)

    def find_target(self, target) -> Optional[Dict]:
        return self.store.find_app_catalog(target)

    def register_manual(
        self, name: str, target: str, arguments: str = "", working_dir: str = ""
    ) -> Dict:
        """用户手动登记一个入口。仍然只是"出现在目录里"，能不能启动看授权表。"""
        text = str(target or "").strip()
        if not text:
            raise AppCatalogError("要登记的应用需要一个目标路径或 shell 解析名。", "app_target_required")
        kind = APP_TARGET_EXE
        if is_shell_target(text):
            kind = APP_TARGET_SHELL
        elif os.path.isdir(text):
            kind = APP_TARGET_KNOWN_FOLDER
        if kind == APP_TARGET_EXE and not os.path.isfile(text):
            raise AppCatalogError("要登记的程序文件不存在：{}".format(text), "app_target_missing")
        row = _entry(
            str(name or "").strip() or Path(text).stem,
            text,
            kind,
            APP_SOURCE_MANUAL,
            "",
            arguments=arguments,
            working_dir=working_dir,
        )
        row["aliases"] = [Path(text).stem] if Path(text).stem else []
        self.store.upsert_app_catalog([row])
        stored = self.store.find_app_catalog(text)
        if stored is None:
            raise AppCatalogError("登记没能写入目录，请稍后再试。", "app_catalog_write_failed")
        return stored

    # -- 身份摘要：只在登记与复核那一刻算，扫描永远不碰 ----------------------

    def _row(self, app_id) -> Dict:
        stored = self.store.get_app_catalog(app_id)
        if stored is None:
            raise AppCatalogError(
                "应用目录里找不到这一条，请先重新扫描。", "app_not_in_catalog"
            )
        return stored

    def _identity_written(self, stored: Dict, snapshot: Dict) -> Dict:
        """把一次身份快照并进目录行。

        列名跟快照里不完全一样（``digest`` → ``identity_digest``），漏掉哪一列，那一列
        就被扫描交上来的空值洗掉；改名时必须在这儿改。
        """
        row = dict(stored)
        row.update(
            {
                "publisher": str(snapshot.get("publisher") or ""),
                "signed": bool(snapshot.get("signed")),
                "signature_state": str(snapshot.get("signature_state") or ""),
                "file_version": str(snapshot.get("file_version") or ""),
                "size_bytes": int(snapshot.get("size_bytes") or 0),
                "mtime": str(int(snapshot.get("mtime") or 0)),
                "identity_digest": str(snapshot.get("digest") or ""),
                "status": APP_READY,
                "last_error": "",
            }
        )
        try:
            identity = resource_identity(row["target"])
        except OSError:
            identity = {"device": "", "file_id": ""}
        row["volume_id"] = identity["device"]
        row["file_id"] = identity["file_id"]
        return row

    def capture_identity(self, app_id) -> Dict:
        """登记那一刻算一次身份写回目录行，作为之后每次启动前复核的基线。

        这里会走 WinVerifyTrust——整个 PE 要被哈希一遍。所以它只在用户点"允许启动"
        这一条时执行，绝不放进扫描：几百条目录项等于几百次全文件读加几百次吊销查询的
        入口，而用户选的是"元信息摘要"这一档。
        """
        stored = self._row(app_id)
        target = str(stored.get("target") or "")
        if stored["target_kind"] != APP_TARGET_EXE:
            return {
                "row": stored,
                "captured": False,
                "reason": "这一条指向 shell 命名空间，没有文件身份可算。",
            }
        snapshot = app_identity.identity_snapshot(target)
        if not snapshot:
            self.store.set_app_catalog_status(
                stored["app_id"], APP_MISSING, "目标文件当前不在原位。"
            )
            return {
                "row": self.store.get_app_catalog(stored["app_id"]),
                "captured": False,
                "reason": "目标文件当前不在原位。",
            }
        self.store.upsert_app_catalog([self._identity_written(stored, snapshot)])
        # 重新登记是一次明确的授权动作，之前换文件留下的 drifted 只在这儿才允许清零；
        # 普通的重新扫描没有这个资格（见 upsert 里的那条 CASE）。
        self.store.set_app_catalog_status(stored["app_id"], APP_READY, "")
        return {
            "row": self.store.get_app_catalog(stored["app_id"]),
            "captured": True,
            "publisher": str(snapshot.get("publisher") or ""),
            "signature_state": str(snapshot.get("signature_state") or ""),
            "signed": bool(snapshot.get("signed")),
            "identity_digest": str(snapshot.get("digest") or ""),
            "detail": str(snapshot.get("detail") or ""),
        }

    def recheck_identity(self, app_id) -> Dict:
        """启动前的复核：只重算摘要与发布者，不做 WinVerifyTrust。

        摘要变了（换过文件）或发布者换了 ⇒ 状态记 ``drifted`` 并说明原因，调用方据此
        拒绝启动、要求重新登记。没有身份基线（老数据或登记时没算成）同样拒绝——把当前
        文件当基线正是这一步要防的事。

        摘要只有秒级 mtime、大小和版本，所以"同一秒内换成一模一样大小的文件"看不出来。
        这是用户选"元信息摘要"而不是整文件哈希那一档时接受的代价，不是这里的疏漏。
        """
        stored = self._row(app_id)
        if stored["target_kind"] != APP_TARGET_EXE:
            return {"ok": True, "row": stored, "reason": "", "changed": False}
        target = str(stored.get("target") or "")
        if not os.path.isfile(target):
            self.store.set_app_catalog_status(
                stored["app_id"], APP_MISSING, "目标文件当前不在原位。"
            )
            return {
                "ok": False,
                "row": self.store.get_app_catalog(stored["app_id"]),
                "reason": "目标文件当前不在原位。",
                "changed": False,
            }
        baseline = str(stored.get("identity_digest") or "")
        if not baseline:
            return {
                "ok": False,
                "row": stored,
                "reason": "这一条还没有身份基线，请重新登记后再启动。",
                "changed": False,
            }
        values = app_identity.identity_values(target)
        values["publisher"] = app_identity.publisher_of(target)
        digest = app_identity.identity_digest(values)
        publisher = str(values.get("publisher") or "")
        stored_publisher = str(stored.get("publisher") or "")
        changed = digest != baseline or publisher != stored_publisher
        if changed:
            reason = (
                "程序文件与登记时不一致（发布者由 {} 变成 {}）".format(
                    stored_publisher or "无", publisher or "无"
                )
                if publisher != stored_publisher
                else "程序文件与登记时不一致，需要重新登记。"
            )
            self.store.set_app_catalog_status(stored["app_id"], APP_DRIFTED, reason)
            self.store.upsert_app_catalog(
                [
                    {
                        **stored,
                        "status": APP_DRIFTED,
                        "last_error": reason,
                        "file_version": str(values.get("file_version") or ""),
                        "size_bytes": int(values.get("size_bytes") or 0),
                        "mtime": str(int(values.get("mtime") or 0)),
                    }
                ]
            )
            return {
                "ok": False,
                "row": self.store.get_app_catalog(stored["app_id"]),
                "reason": reason,
                "changed": True,
            }
        if stored["status"] != APP_READY:
            # 上次复核留下的 missing/drifted 已经被这次比较否掉了，状态该跟着回来。
            self.store.set_app_catalog_status(stored["app_id"], APP_READY, "")
        return {
            "ok": True,
            "row": self.store.get_app_catalog(stored["app_id"]),
            "reason": "",
            "changed": False,
        }

    def stats(self) -> Dict:
        return {
            "total": self.store.count_app_catalog(),
            "ready": self.store.count_app_catalog(status=APP_READY),
            "missing": self.store.count_app_catalog(status=APP_MISSING),
        }
