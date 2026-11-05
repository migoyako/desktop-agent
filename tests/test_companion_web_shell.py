"""Shell geometry contracts for the resizeable 16:9 main window (S4).

The pure helpers are checked directly; the layout contract is checked on an
offscreen Qt window so a fixed-size WebView cannot slip back in.
"""

import ctypes
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_framework.companion.web.shell import (  # noqa: E402
    ASPECT_H,
    ASPECT_W,
    MAIN_SIZE,
    MIN_SIZE,
    WMSZ_BOTTOM,
    WMSZ_TOPRIGHT,
    fit_aspect,
    normalized_geometry,
    overlay_size_for,
    resize_edges,
)

try:
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtWidgets import QApplication

    from agent_framework.companion.web.shell import BaseWindow, MainWindow, OverlayWindow
except ImportError:
    QApplication = None
    QSize = None
    Qt = None
    BaseWindow = None
    MainWindow = None
    OverlayWindow = None

SHELL = (
    Path(__file__).resolve().parent.parent
    / "agent_framework"
    / "companion"
    / "web"
    / "shell.py"
)
APP_JS = SHELL.parent / "assets" / "app.js"


class ShellGeometryTests(unittest.TestCase):
    def test_main_window_is_sixteen_by_nine(self):
        for width, height in (MAIN_SIZE, MIN_SIZE):
            self.assertEqual(ASPECT_W * height, ASPECT_H * width)

    def test_fit_aspect_keeps_the_width_and_recomputes_the_height(self):
        self.assertEqual((1152, 648), fit_aspect(1152, 648))
        self.assertEqual((1600, 900), fit_aspect(1600, 600))
        self.assertEqual((1024, 576), fit_aspect(1000, 700))

    def test_overlay_height_tracks_the_main_window(self):
        self.assertEqual((363, 648), overlay_size_for(648))
        self.assertEqual((448, 800), overlay_size_for(800))
        self.assertEqual(900, overlay_size_for(900)[1])

    def test_saved_geometry_migrates_to_the_minimum(self):
        self.assertEqual(
            (406, 184, 1024, 576),
            normalized_geometry((406, 184, 1000, 700), [(0, 0, 1920, 1080)]),
        )

    def test_saved_geometry_clamps_into_the_screen(self):
        self.assertEqual(
            (0, 0, 1920, 1080),
            normalized_geometry((100, 100, 3840, 2160), [(0, 0, 1920, 1080)]),
        )
        self.assertEqual(
            (2500, 200, 1200, 675),
            normalized_geometry(
                (2500, 200, 1200, 675),
                [(0, 0, 1920, 1080), (1920, 0, 1920, 1080)],
            ),
        )

    def test_missing_geometry_stays_missing(self):
        self.assertIsNone(normalized_geometry(None, [(0, 0, 1920, 1080)]))
        self.assertIsNone(normalized_geometry((1, 2), [(0, 0, 1920, 1080)]))

    def test_edge_names_map_to_qt_edges(self):
        if Qt is None:
            self.skipTest("未安装 PySide6")
        self.assertEqual(Qt.LeftEdge | Qt.TopEdge, resize_edges("topleft"))
        self.assertEqual(Qt.RightEdge | Qt.BottomEdge, resize_edges("bottomright"))
        self.assertEqual(Qt.Edges(0), resize_edges(""))

    def test_shell_contract_has_no_fixed_view_or_grip(self):
        source = SHELL.read_text(encoding="utf-8")
        self.assertNotIn("QSizeGrip", source)
        self.assertNotIn("self.view.setFixedSize", source)
        self.assertIn("QVBoxLayout(self)", source)
        self.assertIn("setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)", source)
        self.assertIn("window.setFixedSize(*size)", source)
        self.assertIn("WM_NCHITTEST", source)
        self.assertIn("WM_SIZING", source)
        self.assertIn("startSystemResize(edges)", source)

    def test_page_reports_edges_for_non_windows(self):
        source = APP_JS.read_text(encoding="utf-8")
        self.assertIn("function bindWindowResize()", source)
        self.assertIn("beginWindowResize", source)
        self.assertIn("bindWindowResize();", source)
        # The edge band must not swallow the window buttons that live inside it.
        self.assertIn('closest("button, input, textarea, a")', source)


class _State:
    def window_geometry(self):
        return (406, 184, 1000, 700)

    def set_window_geometry(self, x, y, width, height):
        self.saved = (x, y, width, height)

    def overlay_position(self):
        return None

    def set_overlay_position(self, x, y):
        self.overlay_saved = (x, y)

    def theme(self):
        return "charcoal-pink"


class _Runtime:
    pass


class _Adapter:
    def __init__(self):
        self.ui_state = _State()
        self.runtime = _Runtime()


class _Owner:
    tray = None

    def quit_application(self):
        pass


@unittest.skipIf(QApplication is None, "未安装 PySide6")
class ShellWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_main(self):
        window = MainWindow(_Owner(), _Adapter(), "about:blank")
        window.show()
        self.app.processEvents()
        self.addCleanup(self.close_window, window)
        return window

    def close_window(self, window):
        window._allow_close = True
        window.close()
        self.app.processEvents()

    def test_old_geometry_is_restored_at_sixteen_by_nine(self):
        window = self.make_main()
        self.assertEqual(MIN_SIZE, (window.width(), window.height()))
        self.assertEqual(QSize(*MIN_SIZE), window.minimumSize())

    def test_the_view_fills_the_resized_window(self):
        window = self.make_main()
        self.assertEqual((window.width(), window.height()),
                         (window.view.width(), window.view.height()))
        window.resize(1600, 900)
        self.app.processEvents()
        self.assertEqual((1600, 900), (window.view.width(), window.view.height()))

    def test_no_size_grip_is_installed(self):
        window = self.make_main()
        self.assertFalse(hasattr(window, "grip"))

    def test_overlay_follows_main_window_height(self):
        main = self.make_main()
        owner = _Owner()
        owner.main = main
        overlay = OverlayWindow(owner, _Adapter(), "about:blank")
        overlay.show()
        self.app.processEvents()
        self.addCleanup(self.close_window, overlay)

        self.assertEqual(main.height(), overlay.height())
        self.assertEqual(overlay_size_for(main.height()), (
            overlay.width(), overlay.height()
        ))

        main.resize(1600, 900)
        self.app.processEvents()
        self.assertEqual(900, overlay.height())
        self.assertEqual(overlay_size_for(900), (
            overlay.width(), overlay.height()
        ))

    def test_tool_windows_stay_fixed_but_fill_their_size(self):
        window = BaseWindow(_Owner(), _Adapter(), "about:blank", (680, 560), "设置")
        window.setFixedSize(680, 560)
        window.show()
        self.app.processEvents()
        self.addCleanup(self.close_window, window)
        window.resize(900, 700)
        self.app.processEvents()
        self.assertEqual((680, 560), (window.width(), window.height()))
        self.assertEqual((680, 560), (window.view.width(), window.view.height()))

    @unittest.skipUnless(sys.platform.startswith("win"), "Win32 消息测试")
    def test_sizing_rect_locks_the_aspect_and_anchors_the_fixed_edges(self):
        from ctypes import wintypes

        window = self.make_main()

        class Message:
            pass

        rect = wintypes.RECT(100, 100, 1300, 800)
        message = Message()
        message.lParam = ctypes.addressof(rect)
        message.wParam = WMSZ_TOPRIGHT
        self.assertTrue(window._lock_aspect(message))
        self.assertEqual((100, 125, 1300, 800),
                         (rect.left, rect.top, rect.right, rect.bottom))
        self.assertEqual(ASPECT_W * (rect.bottom - rect.top),
                         ASPECT_H * (rect.right - rect.left))

        small = wintypes.RECT(100, 100, 300, 100)
        message.lParam = ctypes.addressof(small)
        message.wParam = WMSZ_BOTTOM
        self.assertTrue(window._lock_aspect(message))
        self.assertEqual(MIN_SIZE, (small.right - small.left, small.bottom - small.top))


if __name__ == "__main__":
    unittest.main()
