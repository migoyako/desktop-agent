"""Report whether the local companion environment is ready."""

from __future__ import annotations

import importlib.util
import os
import platform
from pathlib import Path
import sys
import tempfile

SHARED_TCL = Path(r"C:\Users\Public\CompanionRuntime\tcl")
if (SHARED_TCL / "tcl8.6" / "init.tcl").is_file():
    os.environ.setdefault("TCL_LIBRARY", str(SHARED_TCL / "tcl8.6"))
    os.environ.setdefault("TK_LIBRARY", str(SHARED_TCL / "tk8.6"))

try:
    import tkinter as tk
except ImportError:
    tk = None


ROOT = Path(__file__).resolve().parent


def check(name, passed, detail):
    status = "OK" if passed else "FAIL"
    print("[{}] {:<20} {}".format(status, name, detail))
    return passed


def main():
    checks = []
    version = platform.python_version()
    checks.append(
        check(
            "Python",
            sys.version_info >= (3, 8),
            "{} at {}".format(version, sys.executable),
        )
    )

    try:
        import PySide6
        from PySide6.QtCore import qVersion

        checks.append(
            check(
                "PySide6",
                True,
                "{} / Qt {}".format(PySide6.__version__, qVersion()),
            )
        )
    except Exception as error:
        checks.append(check("PySide6", False, str(error)))

    try:
        import openai

        checks.append(check("OpenAI SDK", True, openai.__version__))
    except Exception as error:
        checks.append(check("OpenAI SDK", False, str(error)))

    try:
        from agent_framework.config import AgentConfig

        config = AgentConfig.from_env()
        checks.append(
            check(
                "LLM config",
                True,
                "{} / remote={}".format(
                    config.model,
                    "yes" if config.can_use_remote_llm else "no",
                ),
            )
        )
    except Exception as error:
        checks.append(check("LLM config", False, str(error)))

    try:
        with tempfile.TemporaryDirectory() as directory:
            probe = Path(directory) / "probe.txt"
            probe.write_text("ok", encoding="utf-8")
            passed = probe.read_text(encoding="utf-8") == "ok"
        checks.append(check("Write access", passed, str(ROOT)))
    except Exception as error:
        checks.append(check("Write access", False, str(error)))

    try:
        if tk is None:
            raise RuntimeError("tkinter is not installed")
        root = tk.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
        checks.append(check("Tk fallback", True, "available"))
    except Exception as error:
        print("[WARN] {:<20} {}".format(
            "Tk fallback",
            "optional; PySide6 is primary: {}".format(error),
        ))

    package_check = importlib.util.find_spec("agent_framework.companion")
    checks.append(
        check(
            "Companion package",
            package_check is not None,
            "agent_framework.companion",
        )
    )

    try:
        import pyautogui
        import pywinauto

        checks.append(
            check(
                "Windows automation",
                True,
                "PyAutoGUI {} / pywinauto {}".format(
                    pyautogui.__version__,
                    pywinauto.__version__,
                ),
            )
        )
    except Exception as error:
        checks.append(check("Windows automation", False, str(error)))

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser_path = Path(playwright.chromium.executable_path)
        checks.append(
            check(
                "Playwright",
                browser_path.exists(),
                str(browser_path),
            )
        )
    except Exception as error:
        checks.append(check("Playwright", False, str(error)))

    print()
    if all(checks):
        print("Environment check passed.")
        return 0
    print("Environment check failed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
