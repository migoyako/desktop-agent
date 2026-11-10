"""Windows 回收站删除：只走 shell32 的撤销式删除，代码里没有永久删除这条路。"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from typing import Dict, Sequence


FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400
_DELETE_FLAGS = FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI

ERROR_FILE_NOT_FOUND = 2
ERROR_PATH_NOT_FOUND = 3
ERROR_DIRECTORY = 267
ERROR_FILENAME_EXCED_RANGE = 124
ERROR_CANCELLED = 1223

# shell32 的老式路径长度上限；超过它 SHFileOperationW 直接回 124，而且不回"哪个文件"。
# 与其让系统给一个没有解释的错误码，不如在这里就把话说清楚。
MAX_SHELL_PATH_CHARS = 260

_SHELL_HINTS = {
    ERROR_FILE_NOT_FOUND: "文件已经不在了，回收站没有收到任何东西。",
    ERROR_PATH_NOT_FOUND: "文件所在的目录已经不存在，回收站没有收到任何东西。",
    ERROR_DIRECTORY: "路径里有无效的目录名。",
    ERROR_FILENAME_EXCED_RANGE: "路径太长，系统回收站拒绝了这次删除。",
    ERROR_CANCELLED: "系统取消了这次删除。",
}


class RecycleBinError(RuntimeError):
    def __init__(self, message, code="recycle_bin_error", shell_code=0):
        super().__init__(message)
        self.code = code
        self.shell_code = int(shell_code or 0)


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperations", wintypes.BOOL),
        ("aborted", wintypes.BOOL),
        ("lpNameMapping", ctypes.c_void_p),
    ]


def is_available() -> bool:
    return os.name == "nt"


def delete_arguments(paths: Sequence[str]) -> Dict:
    """打包成 SHFileOperationW 要的 DOUBLE_NULL 宽字符列表，顺便挡掉它解释不了的路径。"""
    items = [str(path) for path in paths]
    if not items:
        raise RecycleBinError("没有要放进回收站的路径。", "recycle_no_paths")
    for item in items:
        if not os.path.isabs(item):
            raise RecycleBinError("回收站删除只接受绝对路径。", "recycle_bad_path")
        if item.endswith(("\\", "/")):
            raise RecycleBinError("回收站删除的路径不能以分隔符结尾。", "recycle_bad_path")
        if item.startswith(("\\\\", "//")) and not item.startswith("\\\\?\\"):
            raise RecycleBinError(
                "不允许把网络共享路径放进回收站。", "recycle_bad_path"
            )
        if len(item) > MAX_SHELL_PATH_CHARS:
            raise RecycleBinError(
                "路径超过 {} 个字符，系统回收站不接受，已停止删除。".format(
                    MAX_SHELL_PATH_CHARS
                ),
                "recycle_path_too_long",
            )
    return {
        "paths": items,
        "block": "\0".join(items) + "\0\0",
        "flags": _DELETE_FLAGS,
    }


def send_to_recycle_bin(paths: Sequence[str]) -> Dict:
    """把文件交给系统回收站；返回里带系统给的原始错误码，供回执如实记录。

    任何一步失败都不回退成"直接删掉"——永久删除不在这个模块的能力范围里。
    """
    if not is_available():
        raise RecycleBinError(
            "当前系统没有 Windows 回收站，删除动作不会执行，文件仍在原处。",
            "recycle_unsupported",
        )
    prepared = delete_arguments(paths)
    operation = _SHFILEOPSTRUCTW()
    operation.hwnd = None
    operation.wFunc = FO_DELETE
    operation.pFrom = prepared["block"]
    operation.pTo = None
    operation.fFlags = prepared["flags"]
    operation.fAnyOperations = False
    operation.aborted = False
    operation.lpNameMapping = None
    try:
        shell32 = ctypes.WinDLL("shell32")
        shell_code = int(shell32.SHFileOperationW(ctypes.byref(operation)))
    except OSError as error:
        raise RecycleBinError(
            "调用系统回收站失败：{}".format(error), "recycle_call_failed"
        ) from error
    if shell_code == 0 and not operation.aborted:
        return {
            "paths": prepared["paths"],
            "shell_code": 0,
            "flags": prepared["flags"],
            "aborted": False,
        }
    if operation.aborted:
        raise RecycleBinError(
            "系统回收站中途放弃了这次删除。", "recycle_aborted", shell_code
        )
    if shell_code == ERROR_CANCELLED:
        raise RecycleBinError(
            _SHELL_HINTS[shell_code] + "文件仍在原处。",
            "recycle_cancelled",
            shell_code,
        )
    hint = _SHELL_HINTS.get(
        shell_code, "系统回收站返回错误码 {}。".format(shell_code)
    )
    raise RecycleBinError(hint, "recycle_failed", shell_code)
