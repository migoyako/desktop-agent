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
from PySide6.QtGui import QColor, QCursor, QGuiApplication
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QSizePolicy, QVBoxLayout, QWidget

from ..models import AgentMode
from ..policy import mode_label
from ..ui.state import UiStateStore, clamp_position, snap_to_edges
from ..ui.theme import THEMES
from ..ui.tray import TrayController, app_icon

ASPECT_W = 16
ASPECT_H = 9
MAIN_SIZE = (1152, 648)
MIN_SIZE = (1024, 576)
SETTINGS_SIZE = (680, 560)
CONSOLE_SIZE = (1040, 720)
OVERLAY_SIZE = (300, 250)
FULLSCREEN_POLL_MS = 3000
HIT_POLL_MS = 120
RESIZE_BORDER = 8
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
VK_LBUTTON = 0x01
WM_NCLBUTTONDOWN = 0x00A1
WM_NCHITTEST = 0x0084
WM_SIZING = 0x0214
HTCAPTION = 2
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17
WMSZ_LEFT = 1
WMSZ_RIGHT = 2
WMSZ_TOP = 3
WMSZ_TOPLEFT = 4
WMSZ_TOPRIGHT = 5
WMSZ_BOTTOM = 6
WMSZ_BOTTOMLEFT = 7
WMSZ_BOTTOMRIGHT = 8


def fit_aspect(width, height=None):
    """Normalize a requested size to the main window's 16:9 shape.

    Width drives the result because the resize loop derives height from the
    horizontal edge the user is pulling; the incoming height is recomputed
    rather than trusted. Widths below the design minimum are raised first so
    the derived height also clears ``MIN_SIZE``.
    """
    try:
        width = int(round(float(width)))
    except (TypeError, ValueError):
        width = MIN_SIZE[0]
    width = max(MIN_SIZE[0], width)
    return width, max(MIN_SIZE[1], int(round(width * ASPECT_H / ASPECT_W)))


def normalized_geometry(geometry, screens):
    """Map a saved ``(x, y, width, height)`` onto a 16:9 window inside a screen."""
    if not geometry or len(geometry) != 4:
        return None
    x, y, width, _height = geometry
    available = [max(MIN_SIZE[0], int(screen[2])) for screen in screens]
    if available:
        width = min(max(MIN_SIZE[0], int(width)), max(available))
    width, height = fit_aspect(width)
    x, y = clamp_position(x, y, width, height, screens)
    return x, y, width, height


def resize_edges(name):
    """Translate the page's ``topleft``-style edge name into Qt edges."""
    name = str(name or "").lower()
    edges = Qt.Edges(0)
    if "left" in name:
        edges |= Qt.LeftEdge
    if "right" in name:
        edges |= Qt.RightEdge
    if "top" in name:
        edges |= Qt.TopEdge
    if "bottom" in name:
        edges |= Qt.BottomEdge
    return edges


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


def _set_click_through(hwnd, enabled):
    """Toggle WS_EX_TRANSPARENT so empty overlay pixels reach the desktop."""
    if not sys.platform.startswith("win") or not hwnd:
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_long = user32.GetWindowLongPtrW
    get_long.restype = ctypes.c_ssize_t
    get_long.argtypes = [wintypes.HWND, ctypes.c_int]
    set_long = user32.SetWindowLongPtrW
    set_long.restype = ctypes.c_ssize_t
    set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_uint,
    ]
    style = int(get_long(hwnd, GWL_EXSTYLE))
    if enabled:
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
    else:
        style &= ~WS_EX_TRANSPARENT
    set_long(hwnd, GWL_EXSTYLE, style)
    # SWP_NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED: reapply hit testing
    # without touching geometry or stealing focus.
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x37)


def _left_button_down():
    if not sys.platform.startswith("win"):
        return False
    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    return bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)


def _send_caption_drag(hwnd):
    """Let the OS caption loop own the move until the button is released.

    Returns False when the platform has no Win32 message loop to delegate to;
    Qt's startSystemMove is the fallback in that case.
    """
    if not sys.platform.startswith("win") or not hwnd:
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.ReleaseCapture.restype = wintypes.BOOL
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.SendMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.ReleaseCapture()
    user32.SendMessageW(wintypes.HWND(hwnd), WM_NCLBUTTONDOWN, HTCAPTION, 0)
    return True


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
    def beginWindowDrag(self):
        self.window.begin_drag()

    @Slot(str)
    def beginWindowResize(self, edge):
        self.window.begin_resize(edge)

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
        self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.view)
        self._layout = layout
        self.channel = QWebChannel(self.view.page())
        self.host = ShellHost(self, adapter)
        self.channel.registerObject("host", self.host)
        self.view.page().setWebChannel(self.channel)
        self.view.load(QUrl(url))

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

    def begin_drag(self):
        handle = self.windowHandle()
        if sys.platform.startswith("win") and _send_caption_drag(int(self.winId())):
            return
        if handle is not None:
            handle.startSystemMove()

    def begin_resize(self, edge):
        """Non-Windows fallback: hand an edge drag to the platform."""
        if self.isMaximized() or self.isFullScreen():
            return
        handle = self.windowHandle()
        edges = resize_edges(edge)
        if handle is None or not edges:
            return
        try:
            handle.startSystemResize(edges)
        except Exception:
            pass

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
            MAIN_SIZE,
            "数字分身",
        )
        self.setMinimumSize(*MIN_SIZE)
        self.setWindowIcon(app_icon(self.adapter.runtime, 32))
        self._restore_geometry()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(350)
        self._save_timer.timeout.connect(self._save_geometry)

    def _restore_geometry(self):
        geometry = normalized_geometry(self.adapter.ui_state.window_geometry(), _screens())
        if geometry:
            self.setGeometry(*geometry)
            return
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(
            screen.x() + (screen.width() - MAIN_SIZE[0]) // 2,
            screen.y() + (screen.height() - MAIN_SIZE[1]) // 2,
        )

    def nativeEvent(self, event_type, message):
        if sys.platform.startswith("win"):
            try:
                from ctypes import wintypes

                msg = wintypes.MSG.from_address(int(message))
            except Exception:
                msg = None
            if msg is not None:
                if msg.message == WM_NCHITTEST:
                    hit = self._resize_hit(msg)
                    if hit:
                        return True, hit
                elif msg.message == WM_SIZING and self._lock_aspect(msg):
                    return True, 1
        return super().nativeEvent(event_type, message)

    def _resize_hit(self, msg):
        """Return the native resize border under the cursor, or 0."""
        if self.isMaximized() or self.isFullScreen():
            return 0
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetWindowRect.argtypes = [
                wintypes.HWND,
                ctypes.POINTER(wintypes.RECT),
            ]
            user32.GetWindowRect.restype = wintypes.BOOL
            rect = wintypes.RECT()
            if not user32.GetWindowRect(
                wintypes.HWND(int(self.winId())), ctypes.byref(rect)
            ):
                return 0
        except Exception:
            return 0
        # WM_NCHITTEST and GetWindowRect both speak physical pixels; only the
        # border width is logical, so scale it by the window's DPR.
        x = ctypes.c_short(msg.lParam & 0xFFFF).value
        y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
        left = x - rect.left
        right = rect.right - x
        top = y - rect.top
        bottom = rect.bottom - y
        if min(left, right, top, bottom) < 0:
            return 0
        border = max(4, int(round(RESIZE_BORDER * float(self.devicePixelRatioF()))))
        near_left = left <= border
        near_right = right <= border
        near_top = top <= border
        near_bottom = bottom <= border
        if near_top and near_left:
            return HTTOPLEFT
        if near_top and near_right:
            return HTTOPRIGHT
        if near_bottom and near_left:
            return HTBOTTOMLEFT
        if near_bottom and near_right:
            return HTBOTTOMRIGHT
        if near_left:
            return HTLEFT
        if near_right:
            return HTRIGHT
        if near_top:
            return HTTOP
        if near_bottom:
            return HTBOTTOM
        return 0

    def _lock_aspect(self, msg):
        """Rewrite the WM_SIZING rectangle to 16:9, anchored on the fixed edges."""
        if self.isMaximized() or self.isFullScreen():
            return False
        try:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT.from_address(int(msg.lParam))
            edge = int(msg.wParam)
            scale = max(1.0, float(self.devicePixelRatioF()))
            min_width = int(round(MIN_SIZE[0] * scale))
            min_height = int(round(MIN_SIZE[1] * scale))
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            if edge in (WMSZ_TOP, WMSZ_BOTTOM):
                # A vertical edge leaves width untouched; derive width from the
                # dragged height instead so top/bottom drags still move.
                height = max(min_height, height)
                width = int(round(height * ASPECT_W / ASPECT_H))
            width = max(min_width, width)
            height = max(min_height, int(round(width * ASPECT_H / ASPECT_W)))
            if edge == WMSZ_LEFT:
                rect.left = rect.right - width
            elif edge == WMSZ_RIGHT:
                rect.right = rect.left + width
            elif edge == WMSZ_TOP:
                rect.top = rect.bottom - height
                rect.right = rect.left + width
            elif edge == WMSZ_BOTTOM:
                rect.bottom = rect.top + height
                rect.right = rect.left + width
            elif edge == WMSZ_TOPLEFT:
                rect.left = rect.right - width
                rect.top = rect.bottom - height
            elif edge == WMSZ_TOPRIGHT:
                rect.right = rect.left + width
                rect.top = rect.bottom - height
            elif edge == WMSZ_BOTTOMLEFT:
                rect.left = rect.right - width
                rect.bottom = rect.top + height
            elif edge == WMSZ_BOTTOMRIGHT:
                rect.right = rect.left + width
                rect.bottom = rect.top + height
            else:
                return False
            return True
        except Exception:
            return False

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
        self._click_through = None
        self._hit_seq = 0
        self._hit_timer = QTimer(self)
        self._hit_timer.setInterval(HIT_POLL_MS)
        self._hit_timer.timeout.connect(self._poll_hit)
        self._restore_position()

    # A translucent window swallows every click inside its rectangle, so the
    # empty pixels must be made click-through and only the page's hit region
    # (figure, bubble, menu) opts back in. Qt's per-pixel mask is not enough
    # because the stage canvas keeps its own alpha.

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_click_through(True)
        self._hit_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._hit_timer.stop()

    def _poll_hit(self):
        if not self.isVisible():
            return
        if not self._click_through and _left_button_down():
            # Keep the drag alive when the cursor outruns the figure.
            return
        local = self.mapFromGlobal(QCursor.pos())
        if not self.rect().contains(local):
            self._apply_click_through(True)
            return
        self._hit_test(local)

    def _hit_test(self, local):
        self._hit_seq += 1
        seq = self._hit_seq
        self.view.page().runJavaScript(
            "window.__companionHit ? window.__companionHit({}, {}) : false".format(
                local.x(), local.y()
            ),
            lambda hit: self._on_hit(seq, hit),
        )

    def _on_hit(self, seq, hit):
        if seq != self._hit_seq or not self.isVisible():
            return
        self._apply_click_through(not bool(hit))

    def _apply_click_through(self, enabled):
        if enabled == self._click_through:
            return
        self._click_through = enabled
        try:
            _set_click_through(int(self.winId()), enabled)
        except Exception:
            pass

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
    overlay_supported = True

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
        self._overlay_offline = not self.pet_available()
        self.tray = TrayController(self, self.runtime)

        self._activate = _ActivateBridge(self)
        self._activate.fired.connect(self.show_main, Qt.QueuedConnection)
        adapter.set_activate_handler(self._activate.fired.emit)

        if self._overlay_visible and self.pet_available():
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

    def pet_available(self):
        """Whether the pet may be shown at all: carrier valid and runtime good.

        There is no fallback figure; when this is false the overlay window is
        never started, and messages stay reachable in the main window.
        """
        try:
            pet = self.adapter.assets().get("pet") or {}
        except Exception:
            return False
        return bool(pet.get("available"))

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
        if self._overlay_visible and self.pet_available():
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
        if not self.pet_available():
            self._auto_hidden = False
            if not self._overlay_offline:
                self._overlay_offline = True
                self.overlay.hide()
                try:
                    self.tray.refresh()
                except Exception:
                    pass
            return
        self._overlay_offline = False
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
        try:
            self.tray.refresh()
        except Exception:
            pass

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
            window.setFixedSize(*size)
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
