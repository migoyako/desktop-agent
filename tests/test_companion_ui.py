"""Compatibility UI smoke test."""

import tempfile
import os
import unittest
from pathlib import Path

SHARED_TCL = Path(r"C:\Users\Public\CompanionRuntime\tcl")
if (SHARED_TCL / "tcl8.6" / "init.tcl").is_file():
    os.environ.setdefault("TCL_LIBRARY", str(SHARED_TCL / "tcl8.6"))
    os.environ.setdefault("TK_LIBRARY", str(SHARED_TCL / "tk8.6"))

from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.llm import OfflineLLMClient


try:
    import tkinter as tk
    from agent_framework.companion.ui.tk_app import CompanionTkApp
except ImportError:
    tk = None
    CompanionTkApp = None


class CompanionUiSmokeTest(unittest.TestCase):
    def test_tk_window_can_be_created(self):
        if CompanionTkApp is None:
            self.skipTest("tkinter unavailable")
        temp = tempfile.TemporaryDirectory()
        try:
            root = Path(temp.name)
            runtime = CompanionRuntime(
                CompanionStore(root / "db.sqlite"),
                OfflineLLMClient(),
                SearchService(),
                root,
                offline=True,
            )
            try:
                app = CompanionTkApp(runtime)
            except tk.TclError as error:
                self.skipTest("desktop unavailable: {}".format(error))
            app.root.withdraw()
            app.root.update_idletasks()
            app.root.destroy()
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
