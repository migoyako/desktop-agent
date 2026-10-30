"""Pixso-designed web front end hosted by the companion app."""

from __future__ import annotations

import threading
import webbrowser

from .server import WebAdapter

__all__ = ["WebAdapter", "launch_web"]


def _browser_loop(adapter):
    print("Web UI: {}".format(adapter.url))
    webbrowser.open(adapter.url)
    event = threading.Event()
    try:
        event.wait()
    except KeyboardInterrupt:
        return 0
    return 0


def launch_web(service, data_dir=None, open_browser=False):
    """Start the local adapter and hand it to the desktop shell.

    Falls back to the default browser when Qt WebEngine is unavailable.
    """
    from ..activation import clear_endpoint, write_endpoint

    adapter = WebAdapter(service, data_dir=data_dir).start()
    if data_dir is not None:
        write_endpoint(data_dir, adapter.port, adapter.token)
    try:
        if open_browser:
            return _browser_loop(adapter)
        try:
            from .shell import launch_shell
        except ImportError:
            print("未安装 Qt WebEngine，改用浏览器打开。")
            return _browser_loop(adapter)
        try:
            return launch_shell(adapter)
        except KeyboardInterrupt:
            return 0
    finally:
        if data_dir is not None:
            clear_endpoint(data_dir)
        adapter.stop()
