"""Windows UI Automation adapter for launch-chain triggers.

The launch-chain service stores a frozen, small control locator. This module
is the only place that knows how to turn that locator into a real UI action.
Coordinates are deliberately not part of the contract: a trigger must match a
window owned by the registered starter process and then a control inside it.
"""

from __future__ import annotations

import os
import time
from typing import Callable, Dict, Optional, Tuple


UIA_WINDOW_TIMEOUT_SECONDS = 10.0
UIA_CONTROL_TIMEOUT_SECONDS = 8.0


class UiAutomationError(RuntimeError):
    def __init__(self, message, code="uia_error"):
        super().__init__(message)
        self.code = code


def _value_or_call(value, default=None):
    if value is None:
        return default
    if callable(value):
        try:
            return value()
        except Exception:
            return default
    return value


class UiAutomationAdapter:
    """Version-independent wrapper around pywinauto's UIA backend.

    ``desktop_factory`` and ``availability_check`` are injectable so the
    locator and safety rules can be tested without opening a real desktop.
    Production imports pywinauto lazily and reports a readable reason when the
    optional dependency is unavailable.
    """

    def __init__(
        self,
        desktop_factory: Optional[Callable[[], object]] = None,
        availability_check: Optional[Callable[[], Tuple[bool, str]]] = None,
        sleep: Optional[Callable[[float], None]] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        self._desktop_factory = desktop_factory
        self._availability_check = availability_check
        self._availability: Optional[Tuple[bool, str]] = None
        self._version = ""
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic

    def available(self) -> Tuple[bool, str]:
        if self._availability is not None:
            return self._availability
        if self._availability_check is not None:
            try:
                result = self._availability_check()
                if (
                    isinstance(result, tuple)
                    and len(result) == 2
                ):
                    self._availability = (bool(result[0]), str(result[1] or ""))
                    return self._availability
                self._availability = (False, "UIA 可用性检查返回格式无效。")
            except Exception as error:
                self._availability = (
                    False,
                    "UIA 可用性检查失败：{}".format(error),
                )
            return self._availability
        if os.name != "nt":
            self._availability = (False, "UI Automation 只在 Windows 上可用。")
            return self._availability
        try:
            import pywinauto
        except Exception as error:
            self._availability = (
                False,
                "Windows UI Automation 依赖不可用：{}".format(error),
            )
            return self._availability
        self._version = str(getattr(pywinauto, "__version__", "") or "")
        self._availability = (True, "")
        return self._availability

    def version(self) -> str:
        if not self._version:
            self.available()
        return self._version

    def _desktop(self):
        if self._desktop_factory is not None:
            return self._desktop_factory()
        ready, reason = self.available()
        if not ready:
            raise UiAutomationError(reason, "uia_unavailable")
        from pywinauto import Desktop

        return Desktop(backend="uia")

    @staticmethod
    def _window_row(window: Dict) -> Dict:
        return {
            "pid": int(window.get("pid") or 0),
            "hwnd": int(window.get("hwnd") or 0),
            "title": str(window.get("title") or ""),
            "class_name": str(window.get("class_name") or ""),
        }

    @staticmethod
    def _matches_window(window: Dict, config: Dict) -> bool:
        title = str(window.get("title") or "").casefold()
        class_name = str(window.get("class_name") or "").casefold()
        wanted_title = str(config.get("window_title_contains") or "").casefold()
        wanted_class = str(config.get("window_class") or "").casefold()
        if wanted_title and wanted_title not in title:
            return False
        if wanted_class and wanted_class != class_name:
            return False
        return True

    def _starter_windows(
        self, desktop, starter_pid: int, config: Dict
    ) -> list:
        if starter_pid <= 0:
            raise UiAutomationError(
                "启动器没有返回进程号，不能定位它的 UIA 窗口。",
                "uia_starter_pid_missing",
            )
        deadline = self._clock() + UIA_WINDOW_TIMEOUT_SECONDS
        last_error = ""
        while True:
            matches = []
            try:
                windows = list(desktop.windows())
            except Exception as error:
                last_error = str(error)
                windows = []
            for window in windows:
                info = getattr(window, "element_info", None)
                pid = _value_or_call(
                    getattr(info, "process_id", None),
                    _value_or_call(getattr(window, "process_id", None), 0),
                )
                try:
                    pid = int(pid or 0)
                except (TypeError, ValueError):
                    pid = 0
                if pid != starter_pid:
                    continue
                row = {
                    "pid": pid,
                    "hwnd": int(
                        _value_or_call(getattr(info, "handle", None), 0) or 0
                    ),
                    "title": str(
                        _value_or_call(
                            getattr(info, "name", None),
                            _value_or_call(
                                getattr(window, "window_text", None), ""
                            ),
                        )
                        or ""
                    ),
                    "class_name": str(
                        _value_or_call(
                            getattr(info, "class_name", None),
                            _value_or_call(
                                getattr(window, "class_name", None), ""
                            ),
                        )
                        or ""
                    ),
                }
                if self._matches_window(row, config):
                    matches.append((window, row))
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise UiAutomationError(
                    "启动器有多个符合冻结条件的窗口，无法确认该操作哪一个。",
                    "uia_window_ambiguous",
                )
            if self._clock() >= deadline:
                detail = "：{}".format(last_error) if last_error else ""
                raise UiAutomationError(
                    "在 {} 秒内没有找到启动器进程中符合冻结条件的窗口{}。".format(
                        int(UIA_WINDOW_TIMEOUT_SECONDS), detail
                    ),
                    "uia_window_not_found",
                )
            self._sleep(0.1)

    @staticmethod
    def _control_spec(config: Dict) -> Dict:
        spec = {}
        automation_id = str(config.get("automation_id") or "").strip()
        control_name = str(config.get("control_name") or "").strip()
        control_type = str(config.get("control_type") or "").strip()
        if automation_id:
            spec["auto_id"] = automation_id
        if control_name:
            spec["title"] = control_name
        if control_type:
            spec["control_type"] = control_type
        if not spec:
            raise UiAutomationError(
                "UIA 配置没有可定位的控件条件。", "uia_control_missing"
            )
        return spec

    @staticmethod
    def _control_evidence(control) -> Dict:
        info = getattr(control, "element_info", None)
        return {
            "automation_id": str(
                _value_or_call(getattr(info, "automation_id", None), "") or ""
            ),
            "name": str(
                _value_or_call(getattr(info, "name", None), "") or ""
            ),
            "control_type": str(
                _value_or_call(getattr(info, "control_type", None), "") or ""
            ),
            "handle": int(
                _value_or_call(getattr(info, "handle", None), 0) or 0
            ),
        }

    def trigger(
        self,
        kind: str,
        _starter: Dict,
        _target: Dict,
        starter_pid: int,
        config: Dict,
    ) -> Dict:
        if kind != "uia":
            raise UiAutomationError(
                "这个适配器只处理 UIA 触发。", "uia_trigger_kind"
            )
        desktop = self._desktop()
        window, window_row = self._starter_windows(
            desktop, int(starter_pid or 0), config
        )
        spec = self._control_spec(config)
        try:
            control_spec = window.child_window(**spec)
            control_spec.wait("ready", timeout=UIA_CONTROL_TIMEOUT_SECONDS)
            control = control_spec.wrapper_object()
        except Exception as error:
            raise UiAutomationError(
                "启动器窗口中没有找到可点击的 UIA 控件：{}".format(error),
                "uia_control_not_found",
            ) from error
        action = "invoke"
        try:
            invoke = getattr(control, "invoke", None)
            if callable(invoke):
                invoke()
            else:
                action = "click_input"
                click = getattr(control, "click_input", None)
                if not callable(click):
                    raise UiAutomationError(
                        "找到控件，但它不提供 invoke 或 click_input。",
                        "uia_control_not_actionable",
                    )
                click()
        except UiAutomationError:
            raise
        except Exception as error:
            raise UiAutomationError(
                "UIA 控件操作失败：{}".format(error),
                "uia_control_action_failed",
            ) from error
        return {
            "kind": "uia",
            "automatic": True,
            "adapter": "pywinauto-uia",
            "adapter_version": self.version(),
            "window": window_row,
            "control": self._control_evidence(control),
            "action": action,
        }

    def __call__(
        self,
        kind: str,
        starter: Dict,
        target: Dict,
        starter_pid: int,
        config: Dict,
    ) -> Dict:
        return self.trigger(kind, starter, target, starter_pid, config)
