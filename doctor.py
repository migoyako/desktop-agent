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


def _keep_stdout_writable():
    """Diagnostic text embeds third-party errors (Tcl/Tk, PySide6); on a GBK
    console one unencodable character would abort the whole report."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def check(name, passed, detail):
    status = "OK" if passed else "FAIL"
    print("[{}] {:<20} {}".format(status, name, detail))
    return passed


def main():
    _keep_stdout_writable()
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
        import sqlite3

        db_path = ROOT / "data" / "companion.db"
        proactive_detail = ""
        work_detail = ""
        if db_path.exists():
            connection = sqlite3.connect(
                "file:{}?mode=ro".format(db_path.as_posix()),
                uri=True,
            )
            try:
                names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master"
                    ).fetchall()
                }
                if {"proactive_state", "proactive_events"} <= names:
                    state_row = connection.execute(
                        """
                        SELECT enabled, daily_count, unanswered_count
                        FROM proactive_state WHERE session_id = 'main'
                        """
                    ).fetchone()
                    unread = connection.execute(
                        """
                        SELECT COUNT(*) FROM proactive_events
                        WHERE session_id = 'main' AND read_at IS NULL
                        """
                    ).fetchone()[0]
                    proactive_detail = (
                        "enabled={} daily={} unanswered={} unread={}".format(
                            (
                                "default"
                                if state_row is None
                                else ("yes" if state_row[0] else "no")
                            ),
                            state_row[1] if state_row else 0,
                            state_row[2] if state_row else 0,
                            unread,
                        )
                    )
                if {"work_tasks", "work_receipts"} <= names:
                    active = connection.execute(
                        """
                        SELECT COUNT(*) FROM work_tasks
                        WHERE session_id = 'main' AND state <> 'NORMAL'
                        """
                    ).fetchone()[0]
                    review = connection.execute(
                        """
                        SELECT COUNT(*) FROM work_tasks
                        WHERE session_id = 'main' AND state = 'PLAN_REVIEW'
                        """
                    ).fetchone()[0]
                    uncertain = connection.execute(
                        """
                        SELECT COUNT(*) FROM work_receipts
                        WHERE session_id = 'main' AND status = 'unknown'
                        """
                    ).fetchone()[0]
                    work_detail = "active={} review={} unknown={}".format(
                        active,
                        review,
                        uncertain,
                    )
            finally:
                connection.close()
            from agent_framework.companion.maintenance import integrity_report

            report = integrity_report(db_path)
            fts_state = (
                "{} rows".format(report["fts_rows"])
                if report["fts_present"]
                else "missing(启动时自愈)"
            )
            checks.append(
                check(
                    "Database",
                    report["ok"],
                    "{} user_version={}/{} fk={} fts={}".format(
                        report["integrity"],
                        report["user_version"],
                        report["expected_version"],
                        report["foreign_key_violations"],
                        fts_state,
                    ),
                )
            )
            if report["version_state"] != "current":
                print("[WARN] {:<20} v{} / expected v{}; next launch migrates".format(
                    "Schema version",
                    report["user_version"],
                    report["expected_version"],
                ))
            if report["missing_indexes"]:
                print("[WARN] {:<20} {}".format(
                    "Missing indexes",
                    ", ".join(report["missing_indexes"]),
                ))
            if (
                report["fts_present"]
                and report["fts_rows"] != report["memory_rows"]
            ):
                print("[WARN] {:<20} fts={} memories={}; rebuilt on launch".format(
                    "FTS parity",
                    report["fts_rows"],
                    report["memory_rows"],
                ))
        else:
            checks.append(check("Database", True, "尚未创建"))

        try:
            from agent_framework.companion.maintenance import list_backups

            backups = list_backups(ROOT / "data")
            if backups:
                latest = backups[0]
                checks.append(
                    check(
                        "Backups",
                        True,
                        "count={} latest={} v{}".format(
                            len(backups),
                            latest["name"],
                            latest["schema_version"],
                        ),
                    )
                )
            else:
                print("[WARN] {:<20} {}".format(
                    "Backups",
                    "none yet; created before migrations",
                ))
        except Exception as error:
            checks.append(check("Backups", False, str(error)))

        try:
            from agent_framework.companion.maintenance import lock_info

            holder = lock_info(ROOT / "data")
            if holder:
                print("[WARN] {:<20} {}".format(
                    "Instance lock",
                    "held by pid={} since {}".format(
                        holder.get("pid", "unknown"),
                        holder.get("started_at", "unknown"),
                    ),
                ))
            else:
                checks.append(check("Instance lock", True, "free"))
        except Exception as error:
            checks.append(check("Instance lock", False, str(error)))
        if proactive_detail:
            checks.append(check("Proactive state", True, proactive_detail))
        else:
            print("[WARN] {:<20} {}".format(
                "Proactive state",
                "not initialised; created on next launch",
            ))
        if work_detail:
            checks.append(check("Work state", True, work_detail))
        else:
            print("[WARN] {:<20} {}".format(
                "Work state",
                "not initialised; created on next launch",
            ))
    except Exception as error:
        checks.append(check("Database", False, str(error)))

    try:
        from agent_framework.companion.runtime import CORE_TOOL_RISKS
        from agent_framework.companion.skills import SkillLibrary

        skills_root = ROOT / "data" / "skills"
        if not skills_root.exists():
            print("[WARN] {:<20} {}".format(
                "Skills",
                "no skills directory; optional",
            ))
        else:
            library = SkillLibrary(skills_root)
            skills, issues = library.scan(CORE_TOOL_RISKS)
            checks.append(
                check(
                    "Skills",
                    not issues,
                    "valid={} invalid={} root={}".format(
                        len(skills),
                        len(issues),
                        skills_root,
                    ),
                )
            )
            for issue in issues:
                print("[WARN] {:<20} {}: {}".format(
                    "Skill rejected",
                    issue.skill_id or "<root>",
                    issue.reason,
                ))
    except Exception as error:
        checks.append(check("Skills", False, str(error)))

    try:
        from agent_framework.companion.assets import character_assets_check
        from agent_framework.companion.persona import DEFAULT_PERSONA_ID
        from agent_framework.companion.ui.state import UiStateStore

        data_dir = ROOT / "data"
        overlay_on = UiStateStore(data_dir).overlay_visible()
        level, detail = character_assets_check(
            data_dir, DEFAULT_PERSONA_ID, overlay_on
        )
        if level == "fail":
            checks.append(check("Character assets", False, detail))
        elif level == "warn":
            print("[WARN] {:<20} {}".format("Character assets", detail))
        else:
            checks.append(check("Character assets", True, detail))
    except Exception as error:
        checks.append(check("Character assets", False, str(error)))

    try:
        web_assets = ROOT / "agent_framework" / "companion" / "web" / "assets"
        vendored = (
            "vendor/pixi.min.js",
            "vendor/pixi-live2d-display-cubism4.min.js",
            "vendor/live2dcubismcore.min.js",
            "vendor/process-env.js",
            "vendor/mesh-avatar.min.js",
            "stage.js",
            "mesh-stage.js",
        )
        missing = [name for name in vendored if not (web_assets / name).is_file()]
        checks.append(
            check(
                "Pet runtime",
                not missing,
                "vendored" if not missing else "missing: " + ", ".join(missing),
            )
        )
    except Exception as error:
        checks.append(check("Pet runtime", False, str(error)))

    try:
        from agent_framework.companion.proactive import (
            WindowsActivityProbe,
            classify_activity,
        )

        snapshot = WindowsActivityProbe().snapshot()
        if snapshot.available:
            checks.append(
                check(
                    "Activity probe",
                    True,
                    "{} / idle={}s".format(
                        classify_activity(snapshot),
                        round(float(snapshot.idle_seconds or 0.0), 1),
                    ),
                )
            )
        else:
            print("[WARN] {:<20} {}".format(
                "Activity probe",
                "unavailable; proactive gating stays closed",
            ))
    except Exception as error:
        print("[WARN] {:<20} {}".format("Activity probe", error))

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
