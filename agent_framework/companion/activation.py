"""Hand a second launch to the instance that already owns the data directory."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ENDPOINT_FILE = ".companion.endpoint"


def write_endpoint(data_dir, port, token):
    path = Path(data_dir) / ENDPOINT_FILE
    try:
        path.write_text(
            json.dumps({"port": int(port), "token": token}),
            encoding="utf-8",
        )
    except OSError:
        return None
    return path


def clear_endpoint(data_dir):
    try:
        (Path(data_dir) / ENDPOINT_FILE).unlink()
    except OSError:
        pass


def read_endpoint(data_dir):
    try:
        payload = json.loads(
            (Path(data_dir) / ENDPOINT_FILE).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        port = int(payload.get("port") or 0)
    except (TypeError, ValueError):
        return None
    token = payload.get("token")
    if not port or not isinstance(token, str) or not token:
        return None
    return {"port": port, "token": token}


def activate(data_dir, pid=None, timeout=1.5):
    """Bring the running instance forward; True when the user can see it.

    The HTTP endpoint covers web-shell and browser instances. When the first
    instance still runs an older build (no endpoint file), fall back to
    showing its top-level window directly on Windows.
    """
    endpoint = read_endpoint(data_dir)
    if endpoint is not None and _request_activation(endpoint, timeout):
        return True
    if sys.platform.startswith("win") and pid:
        return _show_windows(int(pid))
    return False


def _request_activation(endpoint, timeout):
    url = "http://127.0.0.1:{}/api/activate".format(endpoint["port"])
    request = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Companion-Token": endpoint["token"],
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except (OSError, ValueError, urllib.error.URLError):
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("shown"):
        return True
    target = payload.get("url")
    if isinstance(target, str) and target:
        try:
            return bool(webbrowser.open(target))
        except Exception:
            return False
    return False


def _show_windows(pid):
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def collect(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid:
            return True
        if user32.GetWindowLongW(hwnd, -20) & 0x00000080:  # WS_EX_TOOLWINDOW
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        found.append((title.value, hwnd))
        return True

    user32.EnumWindows(collect, 0)
    if not found:
        return False
    preferred = [hwnd for title, hwnd in found if title == "数字分身"]
    target = (preferred or [hwnd for _title, hwnd in found])[0]
    user32.ShowWindow(target, 9)  # SW_RESTORE
    user32.SetForegroundWindow(target)
    return True
