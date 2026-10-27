"""System tray menu for the Qt companion window."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..models import AgentMode
from ..policy import mode_label


def build_avatar_icon(size=32):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    scale = size / 32.0
    painter.setPen(QPen(QColor("#59c3c3"), max(1, int(2 * scale))))
    painter.setBrush(QColor("#edf7f7"))
    painter.drawEllipse(
        int(5 * scale),
        int(4 * scale),
        int(22 * scale),
        int(20 * scale),
    )
    painter.setBrush(QColor("#14272d"))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(
        int(10 * scale),
        int(11 * scale),
        int(3 * scale),
        int(4 * scale),
    )
    painter.drawEllipse(
        int(19 * scale),
        int(11 * scale),
        int(3 * scale),
        int(4 * scale),
    )
    painter.end()
    return QIcon(pixmap)


class TrayController(QObject):
    def __init__(self, window, runtime):
        super().__init__(window)
        self.window = window
        self.runtime = runtime
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self.tray = QSystemTrayIcon(build_avatar_icon(), self)
        menu = QMenu()
        self.window_action = QAction("隐藏主窗口", menu)
        self.window_action.triggered.connect(window.toggle_window)
        menu.addAction(self.window_action)

        self.overlay_action = QAction("显示桌面形象", menu)
        self.overlay_action.setCheckable(True)
        self.overlay_action.triggered.connect(window.set_overlay_visible)
        menu.addAction(self.overlay_action)

        mode_menu = menu.addMenu("模式")
        self.mode_actions = {}
        for mode in (AgentMode.CHAT, AgentMode.WORK):
            action = QAction(mode_label(mode), mode_menu)
            action.setCheckable(True)
            action.triggered.connect(
                lambda checked=False, value=mode: window.set_mode(value)
            )
            mode_menu.addAction(action)
            self.mode_actions[mode] = action

        menu.addSeparator()
        self.pause_action = QAction("暂停主动", menu)
        self.pause_action.setCheckable(True)
        self.pause_action.triggered.connect(self.toggle_proactive)
        menu.addAction(self.pause_action)

        self.check_action = QAction("现在检查主动", menu)
        self.check_action.triggered.connect(window.poll_proactive)
        menu.addAction(self.check_action)

        console_action = QAction("管理台", menu)
        console_action.triggered.connect(window.open_console)
        menu.addAction(console_action)

        menu.addSeparator()
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(window.quit_application)
        menu.addAction(quit_action)

        self.menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._activated)
        self.tray.show()
        self.refresh()

    def _activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.window.toggle_window()

    def toggle_proactive(self, checked):
        try:
            self.runtime.set_proactive_enabled(not checked)
        except Exception:
            pass
        self.refresh()

    def refresh(self):
        if self.tray is None:
            return
        self.window_action.setText(
            "隐藏主窗口" if self.window.isVisible() else "显示主窗口"
        )
        self.overlay_action.setChecked(self.window.overlay_visible())
        current = self.window.mode
        for mode, action in self.mode_actions.items():
            action.setChecked(mode == current)
        try:
            enabled = self.runtime.proactive_status()["enabled"]
        except Exception:
            enabled = False
        self.pause_action.setChecked(not enabled)
        self.tray.setToolTip("{} · {}".format(
            self.window.persona_name,
            mode_label(current),
        ))

    def close(self):
        if self.tray is not None:
            self.tray.hide()
