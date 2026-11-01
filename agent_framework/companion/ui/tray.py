"""System tray menu for the Qt companion window."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

from ..models import AgentMode
from ..policy import mode_label


def app_icon(runtime=None, size=32):
    """The user's avatar when it exists, otherwise the stock application icon."""
    asset = None
    if runtime is not None:
        try:
            asset = runtime.character_assets().avatar
        except Exception:
            asset = None
    if asset is not None:
        pixmap = QPixmap(str(asset.path))
        if not pixmap.isNull():
            return QIcon(
                pixmap.scaled(
                    size,
                    size,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
    app = QApplication.instance()
    style = app.style() if app is not None else None
    if style is not None:
        return style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
    return QIcon()


class TrayController(QObject):
    def __init__(self, window, runtime):
        super().__init__(window)
        self.window = window
        self.runtime = runtime
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self.tray = QSystemTrayIcon(app_icon(runtime), self)
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
        self.unread_action = QAction("未读主动 0 条", menu)
        self.unread_action.setToolTip("打开主窗口查看主动消息")
        self.unread_action.triggered.connect(self.show_main)
        menu.addAction(self.unread_action)

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

    def show_main(self):
        opener = getattr(self.window, "show_main", None)
        if opener is not None:
            opener()
            return
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def toggle_proactive(self, checked):
        try:
            self.runtime.set_proactive_enabled(not checked)
        except Exception:
            pass
        self.refresh()

    def refresh(self):
        if self.tray is None:
            return
        if not self.window.isVisible():
            label = "显示主窗口"
        elif self.window.isMinimized():
            label = "还原主窗口"
        else:
            label = "隐藏主窗口"
        self.window_action.setText(label)
        supported = bool(getattr(self.window, "overlay_supported", False))
        available = supported and getattr(
            self.window, "pet_available", lambda: True
        )()
        self.overlay_action.setChecked(
            supported and self.window.overlay_visible()
        )
        self.overlay_action.setEnabled(available)
        if not supported:
            tooltip = "桌面浮窗需要 Web 界面（--ui web）"
        elif available:
            tooltip = ""
        else:
            tooltip = "缺少可用的 Live2D 模型或 .mavatar，桌面形象不会显示"
        self.overlay_action.setToolTip(tooltip)
        current = self.window.mode
        for mode, action in self.mode_actions.items():
            action.setChecked(mode == current)
        try:
            enabled = self.runtime.proactive_status()["enabled"]
        except Exception:
            enabled = False
        self.pause_action.setChecked(not enabled)
        try:
            unread = len(self.runtime.unread_proactive_events(99))
        except Exception:
            unread = 0
        self.unread_action.setText("未读主动 {} 条".format(unread))
        self.unread_action.setEnabled(unread > 0)
        self.tray.setToolTip("{} · {}".format(
            self.window.persona_name,
            mode_label(current),
        ))

    def close(self):
        if self.tray is not None:
            self.tray.hide()
