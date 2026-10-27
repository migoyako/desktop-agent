"""Activity state machine driven by a ctypes Windows probe."""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
from pathlib import Path
import re
import sys


ACTIVITY_ACTIVE = "active"
ACTIVITY_IDLE = "idle"
ACTIVITY_AWAY = "away"
ACTIVITY_PRIVATE = "private"
ACTIVITY_FOCUS = "focus"
ACTIVITY_GAME = "game"
ACTIVITY_UNKNOWN = "unknown"

IDLE_SECONDS = 300
AWAY_SECONDS = 900

PRIVATE_TITLE = re.compile(
    r"(无痕|隐身|隐私|私密|密码|银行|支付|"
    r"InPrivate|Incognito|Private Browsing)",
    re.IGNORECASE,
)
FOCUS_PROCESSES = {
    "code.exe",
    "devenv.exe",
    "winword.exe",
    "excel.exe",
    "powerpnt.exe",
    "wps.exe",
    "zoom.exe",
    "teams.exe",
    "ms-teams.exe",
    "notion.exe",
    "obsidian.exe",
}
GAME_PROCESSES = {
    "steam.exe",
    "epicgameslauncher.exe",
    "wegame.exe",
    "battle.net.exe",
    "dota2.exe",
    "cs2.exe",
    "league of legends.exe",
    "genshinimpact.exe",
    "starrail.exe",
}

ACTIVITY_LABELS = {
    ACTIVITY_ACTIVE: "活跃",
    ACTIVITY_IDLE: "短时无操作",
    ACTIVITY_AWAY: "离开",
    ACTIVITY_PRIVATE: "隐私界面",
    ACTIVITY_FOCUS: "专注或全屏",
    ACTIVITY_GAME: "游戏中",
    ACTIVITY_UNKNOWN: "未知",
}


@dataclass(frozen=True)
class ActivitySnapshot:
    idle_seconds: float
    foreground_title: str = ""
    process_name: str = ""
    fullscreen: bool = False
    locked: bool = False
    available: bool = True


def classify_activity(snapshot: ActivitySnapshot) -> str:
    if not snapshot.available or snapshot.idle_seconds is None:
        return ACTIVITY_UNKNOWN
    if snapshot.locked or snapshot.idle_seconds >= AWAY_SECONDS:
        return ACTIVITY_AWAY
    if snapshot.idle_seconds >= IDLE_SECONDS:
        return ACTIVITY_IDLE
    title = snapshot.foreground_title or ""
    process = (snapshot.process_name or "").lower()
    if title and PRIVATE_TITLE.search(title):
        return ACTIVITY_PRIVATE
    if process in GAME_PROCESSES:
        return ACTIVITY_GAME
    if snapshot.fullscreen or process in FOCUS_PROCESSES:
        return ACTIVITY_FOCUS
    return ACTIVITY_ACTIVE


def describe_activity(state: str) -> str:
    return ACTIVITY_LABELS.get(state, ACTIVITY_LABELS[ACTIVITY_UNKNOWN])


class _LastInputInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwTime", ctypes.c_uint),
    ]


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class WindowsActivityProbe:
    """Read idle time and the foreground window without extra dependencies."""

    def __init__(self):
        self.available = sys.platform == "win32"

    def snapshot(self) -> ActivitySnapshot:
        if not self.available:
            return ActivitySnapshot(idle_seconds=0.0, available=False)
        try:
            return self._snapshot()
        except Exception:
            return ActivitySnapshot(idle_seconds=0.0, available=False)

    def state(self) -> str:
        return classify_activity(self.snapshot())

    def _snapshot(self) -> ActivitySnapshot:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        info = _LastInputInfo()
        info.cbSize = ctypes.sizeof(_LastInputInfo)
        idle_seconds = 0.0
        if user32.GetLastInputInfo(ctypes.byref(info)):
            kernel32.GetTickCount64.restype = ctypes.c_ulonglong
            tick = kernel32.GetTickCount64()
            idle_seconds = max(0.0, (tick - info.dwTime) / 1000.0)

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ActivitySnapshot(
                idle_seconds=idle_seconds,
                locked=True,
            )

        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value

        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_name = self._process_name(pid.value)
        fullscreen = self._is_fullscreen(hwnd, user32)
        return ActivitySnapshot(
            idle_seconds=idle_seconds,
            foreground_title=title,
            process_name=process_name,
            fullscreen=fullscreen,
        )

    @staticmethod
    def _process_name(pid_value: int) -> str:
        if not pid_value:
            return ""
        kernel32 = ctypes.windll.kernel32
        process_query_limited_information = 0x1000
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid_value,
        )
        if not handle:
            return ""
        try:
            size = ctypes.c_ulong(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(
                handle,
                0,
                buffer,
                ctypes.byref(size),
            ):
                return ""
            return Path(buffer.value).name.lower()
        finally:
            kernel32.CloseHandle(handle)

    @staticmethod
    def _is_fullscreen(hwnd, user32) -> bool:
        rect = _Rect()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        screen_width = user32.GetSystemMetrics(0)
        screen_height = user32.GetSystemMetrics(1)
        return width >= screen_width and height >= screen_height
