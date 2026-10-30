"""QWebEngine desktop shell that hosts the Pixso-designed web UI."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Chromium's renderer sandbox cannot acquire a GPU context on some Windows
# setups and the renderer dies before the first paint (blank window).
if sys.platform.startswith("win"):
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QSizeGrip, QWidget

from ..models import AgentMode
from ..policy import mode_label
from ..ui.state import UiStateStore, clamp_position, snap_to_edges
from ..ui.theme import THEMES
from ..ui.tray import TrayController, build_avatar_icon

MAIN_WIDTH = 1000
MAIN_HEIGHT = 700
MIN_WIDTH = 780
MIN_HEIGHT = 560
SETTINGS_SIZE = (680, 560)
CONSOLE_SIZE = (1040, 720)
OVERLAY_SIZE = (300, 250)
FULLSCREEN_POLL_MS = 3000


def _screens():
    return [
        (screen.availableGeometry().x(), screen.availableGeometry().y(),
         screen.availableGeometry().width(), screen.availableGeometry().height())
        for screen in QGuiApplication.screens()
    ]


def _rect_matches(rect, monitor, tolerance=2):
    """True when a window rectangle covers a monitor rectangle."""
    return all(abs(int(a) - int(b)) <= tolerance for a, b in zip(rect, monitor))


def foreground_is_fullscreen():
    """True when another process owns a borderless full-screen window.

    Comparing against the monitor's own rectangle (not Qt's logical geometry)
    keeps the check correct across DPI scaling; maximized windows cover the
    work area instead, so they are explicitly excluded.
    """
    if not sys.platform.startswith("win"):
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    # Without explicit signatures the 64-bit handles are truncated to c_int.
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsZoomed.argtypes = [wintypes.HWND]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if int(pid.value) == os.getpid():
        return False
    if not user32.IsWindowVisible(hwnd) or user32.IsZoomed(hwnd):
        return False
    name = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, name, 64)
    if name.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        return False
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    if not monitor:
        return False
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    return _rect_matches(
        (rect.left, rect.top, rect.right, rect.bottom),
        (
            info.rcMonitor.left,
            info.rcMonitor.top,
            info.rcMonitor.right,
            info.rcMonitor.bottom,
        ),
    )


class ShellHost(QObject):
    """Bridge the page calls into the native window."""

    def __init__(self, window, adapter):
        super().__init__(window)
        self.window = window
        self.adapter = adapter

    @Slot()
    def ready(self):
        return None

    @Slot()
    def minimize(self):
        self.window.showMinimized()

    @Slot()
    def hide(self):
        self.window.hide_to_tray()

    @Slot()
    def toggleMaximize(self):
        if self.window.isMaximized():
            self.window.showNormal()
        else:
            self.window.showMaximized()

    @Slot(int, int)
    def moveWindow(self, x, y):
        self.window.move(int(x), int(y))

    @Slot()
    def openSettings(self):
        self.window.owner.open_settings()

    @Slot()
    def openConsole(self):
        self.window.owner.open_console()

    @Slot()
    def openMain(self):
        self.window.owner.show_main()

    @Slot()
    def toggleMain(self):
        self.window.owner.toggle_window()

    @Slot(int, int)
    def moveOverlay(self, x, y):
        self.window.owner.move_overlay(int(x), int(y))

    @Slot()
    def snapOverlay(self):
        self.window.owner.snap_overlay()

    @Slot()
    def hideOverlay(self):
        self.window.owner.set_overlay_visible(False)

    @Slot(bool)
    def setOverlayVisible(self, visible):
        self.window.owner.set_overlay_visible(bool(visible))


class WebView(QWebEngineView):
    def __init__(self, window, parent=None, transparent=False):
        super().__init__(parent)
        self.window_ref = window
        self.transparent = transparent
        if transparent:
            self.page().setBackgroundColor(QColor(Qt.transparent))
            self.setAttribute(Qt.WA_TranslucentBackground)
        self.setContextMenuPolicy(Qt.NoContextMenu)

    def createWindow(self, _type):
        return None


class BaseWindow(QWidget):
    def __init__(self, owner, adapter, url, size, title, frameless=True, transparent=False):
        super().__init__()
        self.owner = owner
        self.adapter = adapter
        self._allow_close = False
        self.setWindowTitle(title)
        if frameless:
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        if transparent:
            self.setAttribute(Qt.WA_TranslucentBackground)
            self.setWindowFlags(
                Qt.FramelessWindowHint
                | Qt.WindowStaysOnTopHint
                | Qt.Tool
            )
            self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(*size)
        self.view = WebView(self, self, transparent=transparent)
        self.view.setFixedSize(size[0], size[1])
        self.layout_ = None
        container = QWidget(self)
        container.setGeometry(0, 0, size[0], size[1])
        from PySide6.QtWidgets import QVBoxLayout

        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        self._container = container
        self._layout = layout
        layout.addWidget(self.view)
        self.channel = QWebChannel(self.view.page())
        self.host = ShellHost(self, adapter)
        self.channel.registerObject("host", self.host)
        self.view.page().setWebChannel(self.channel)
        self.view.load(QUrl(url))
        if frameless and not transparent:
            self.grip = QSizeGrip(self)
            self.grip.setFixedSize(14, 14)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "grip"):
            self.grip.move(
                self.width() - self.grip.width(),
                self.height() - self.grip.height(),
            )

    def closeEvent(self, event):
        if self._allow_close:
            event.accept()
            return
        if not self._can_restore():
            event.accept()
            QTimer.singleShot(0, self.owner.quit_application)
            return
        event.ignore()
        self.hide_to_tray()

    def _can_restore(self):
        tray = getattr(self.owner, "tray", None)
        return tray is not None and tray.tray is not None

    def hide_to_tray(self):
        self.hide()


class MainWindow(BaseWindow):
    def __init__(self, owner, adapter, url):
        super().__init__(
            owner,
            adapter,
            url,
            (MAIN_WIDTH, MAIN_HEIGHT),
            "数字分身",
        )
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
        self.setWindowIcon(build_avatar_icon(32))
        self._restore_geometry()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(350)
        self._save_timer.timeout.connect(self._save_geometry)

    def _restore_geometry(self):
        geometry = self.adapter.ui_state.window_geometry()
        if geometry:
            x, y, width, height = geometry
            width = max(MIN_WIDTH, width)
            height = max(MIN_HEIGHT, height)
            x, y = clamp_position(x, y, width, height, _screens())
            self.setGeometry(x, y, width, height)
            return
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(
            screen.x() + (screen.width() - MAIN_WIDTH) // 2,
            screen.y() + (screen.height() - MAIN_HEIGHT) // 2,
        )

    def _save_geometry(self):
        if self.isMaximized() or self.isMinimized():
            return
        self.adapter.ui_state.set_window_geometry(
            self.x(), self.y(), self.width(), self.height()
        )

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, "_save_timer"):
            self._save_timer.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_save_timer"):
            self._save_timer.start()

    def hide_to_tray(self):
        self.hide()
        self.owner.show_tray_hint()

    def set_theme_background(self):
        tokens = THEMES.get(self.adapter.ui_state.theme()) or THEMES["charcoal-pink"]
        self.view.page().setBackgroundColor(QColor(tokens["page"]))

    def showEvent(self, event):
        super().showEvent(event)
        self.set_theme_background()


class OverlayWindow(BaseWindow):
    def __init__(self, owner, adapter, url):
        super().__init__(
            owner,
            adapter,
            url,
            OVERLAY_SIZE,
            "桌面形象",
            transparent=True,
        )
        self.setFixedSize(*OVERLAY_SIZE)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(350)
        self._save_timer.timeout.connect(self._save_position)
        self._restore_position()

    def _restore_position(self):
        screens = _screens()
        position = self.adapter.ui_state.overlay_position()
        if position:
            x, y = clamp_position(position[0], position[1], *OVERLAY_SIZE, screens)
        else:
            x, y = clamp_position(
                screens[0][0] + screens[0][2] - OVERLAY_SIZE[0] - 32,
                screens[0][1] + screens[0][3] - OVERLAY_SIZE[1] - 48,
                *OVERLAY_SIZE,
                screens,
            )
        self.move(x, y)

    def _save_position(self):
        self.adapter.ui_state.set_overlay_position(self.x(), self.y())

    def snap_overlay(self):
        x, y = snap_to_edges(self.x(), self.y(), self.width(), self.height(), _screens())
        self.move(x, y)
        self._save_position()

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, "_save_timer"):
            self._save_timer.start()


class _ActivateBridge(QObject):
    fired = Signal()


class Shell(QObject):
    def __init__(self, adapter):
        super().__init__()
        self.adapter = adapter
        self.service = adapter.service
        self.runtime = adapter.runtime
        self.windows = {}
        self._tray_hint_shown = False

        base = adapter.url
        self.main = MainWindow(self, adapter, base)
        self.overlay = OverlayWindow(
            self,
            adapter,
            "http://{}/overlay?t={}".format(adapter.address, adapter.token),
        )
        self.mode = self.runtime.current_mode()
        self._overlay_visible = adapter.ui_state.overlay_visible()
        self._auto_hidden = False
        self.tray = TrayController(self, self.runtime)

        self._activate = _ActivateBridge(self)
        self._activate.fired.connect(self.show_main, Qt.QueuedConnection)
        adapter.set_activate_handler(self._activate.fired.emit)

        if self._overlay_visible:
            self.overlay.show()
        self.main.show()

        self.proactive_timer = QTimer(self)
        self.proactive_timer.setInterval(30000)
        self.proactive_timer.timeout.connect(self.poll_proactive)
        self.proactive_timer.start()

        self.fullscreen_timer = QTimer(self)
        self.fullscreen_timer.setInterval(FULLSCREEN_POLL_MS)
        self.fullscreen_timer.timeout.connect(self.watch_fullscreen)
        self.fullscreen_timer.start()

    # -- tray contract -------------------------------------------------

    def isVisible(self):
        return self.main.isVisible()

    def isMinimized(self):
        return self.main.isMinimized()

    def overlay_visible(self):
        return self._overlay_visible

    def toggle_window(self):
        if self.main.isVisible() and not self.main.isMinimized():
            self.main.hide()
        else:
            self.main.showNormal()
            self.main.raise_()
            self.main.activateWindow()

    def set_overlay_visible(self, visible):
        self._overlay_visible = bool(visible)
        self._auto_hidden = False
        self.adapter.ui_state.set_overlay_visible(self._overlay_visible)
        if self._overlay_visible:
            self.overlay.show()
        else:
            self.overlay.hide()
        try:
            self.tray.refresh()
        except Exception:
            pass

    def watch_fullscreen(self):
        """Hide the pet while another app owns the screen, then bring it back."""
        if not self._overlay_visible:
            return
        try:
            covered = foreground_is_fullscreen()
        except Exception:
            return
        if covered and self.overlay.isVisible():
            self.overlay.hide()
            self._auto_hidden = True
        elif not covered and self._auto_hidden:
            self._auto_hidden = False
            self.overlay.show()

    def set_mode(self, mode):
        try:
            self.service.set_mode(mode)
        except Exception:
            return
        self.mode = AgentMode(mode)
        self.adapter.broadcast({"type": "refresh"})
        self.tray.refresh()

    def poll_proactive(self):
        try:
            self.runtime.proactive_tick()
        except Exception:
            pass
        self.adapter.broadcast({"type": "refresh"})

    def show_tray_hint(self):
        if self._tray_hint_shown:
            return
        self._tray_hint_shown = True
        tray = getattr(self.tray, "tray", None)
        if tray is None:
            return
        try:
            tray.showMessage(
                "数字分身仍在运行",
                "双击托盘图标，或再次启动程序即可打开窗口。",
            )
        except Exception:
            pass

    def quit_application(self):
        self.proactive_timer.stop()
        self.fullscreen_timer.stop()
        try:
            self.tray.close()
        except Exception:
            pass
        self.overlay._allow_close = True
        self.main._allow_close = True
        for window in list(self.windows.values()):
            window._allow_close = True
            window.close()
        self.overlay.close()
        self.main.close()
        self.adapter.stop()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    # -- secondary windows ---------------------------------------------

    def show_main(self):
        self.main.showNormal()
        self.main.raise_()
        self.main.activateWindow()

    def open_settings(self):
        self._open_window(
            "settings",
            "设置",
            SETTINGS_SIZE,
            "http://{}/settings?t={}".format(self.adapter.address, self.adapter.token),
        )

    def open_console(self):
        self._open_window(
            "console",
            "管理台",
            CONSOLE_SIZE,
            "http://{}/console?t={}".format(self.adapter.address, self.adapter.token),
        )

    def _open_window(self, key, title, size, url):
        window = self.windows.get(key)
        if window is None:
            window = BaseWindow(self, self.adapter, url, size, title)
            self.windows[key] = window
        window.show()
        window.raise_()
        window.activateWindow()

    def move_overlay(self, x, y):
        x, y = clamp_position(x, y, *OVERLAY_SIZE, _screens())
        self.overlay.move(x, y)

    def snap_overlay(self):
        self.overlay.snap_overlay()

    @property
    def persona_name(self):
        try:
            return self.service.runtime.persona_info().get("name") or "数字分身"
        except Exception:
            return "数字分身"


def launch_shell(adapter):
    app = QApplication.instance()
    created = False
    if app is None:
        app = QApplication(sys.argv)
        created = True
    app.setApplicationName("Companion")
    app.setQuitOnLastWindowClosed(False)

    shell = Shell(adapter)
    app._companion_shell = shell
    code = app.exec()
    if created:
        return code
    return 0
