"""R25 gate probe: WebGL inside the transparent, topmost, click-through overlay.

Run on a real desktop (not offscreen)::

    python -m agent_framework.companion.web.live2d_probe

It opens a 300x250 window matching the overlay shell, renders a WebGL quad,
then checks four things the Live2D pet depends on:

1. a WebGL2/WebGL1 context exists and actually rasterises (readPixels);
2. the window composites with the desktop (pixels outside the quad are
   unchanged between "hidden" and "shown" screen grabs);
3. ``WS_EX_TRANSPARENT`` makes transparent-area points hit the window below;
4. idle frame rate and process CPU stay in a sane range.

The probe never loads a character model; it only answers whether the shell can
host one. Results are printed as PASS/FAIL lines and the process exits 0/1.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

if sys.platform.startswith("win"):
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    os.environ.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--ignore-gpu-blocklist --enable-gpu-rasterization",
    )

from PySide6.QtCore import QCoreApplication, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QWidget

SIZE = (300, 250)
QUAD = (70, 45, 160, 160)  # left, top, width, height in CSS pixels

PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
html,body{margin:0;width:100%;height:100%;background:transparent;overflow:hidden}
canvas{display:block;width:100%;height:100%}
</style></head><body><canvas id="c"></canvas><script>
(function(){
  const canvas = document.getElementById("c");
  const dpr = window.devicePixelRatio || 1;
  // innerWidth is 0 while the shell is still hidden; the CSS box is the fallback.
  canvas.width = Math.max(1, Math.round((window.innerWidth || 300) * dpr));
  canvas.height = Math.max(1, Math.round((window.innerHeight || 250) * dpr));
  const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
  const out = {ok:false, version:"", renderer:"", frames:0, quad:"rgba(0,0,0,0)"};
  window.__probe = out;
  if (!gl) { out.error = "no webgl context"; return; }
  out.version = gl.getParameter(gl.VERSION) || "";
  const debug = gl.getExtension("WEBGL_debug_renderer_info");
  out.renderer = debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : "";
  const vs = gl.createShader(gl.VERTEX_SHADER);
  gl.shaderSource(vs, "attribute vec2 p; void main(){ gl_Position = vec4(p, 0.0, 1.0); }");
  gl.compileShader(vs);
  const fs = gl.createShader(gl.FRAGMENT_SHADER);
  gl.shaderSource(fs, "precision mediump float; void main(){ gl_FragColor = vec4(1.0, 0.0, 0.47, 1.0); }");
  gl.compileShader(fs);
  const program = gl.createProgram();
  gl.attachShader(program, vs); gl.attachShader(program, fs); gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    out.error = "shader link failed: " + gl.getProgramInfoLog(program); return;
  }
  gl.useProgram(program);
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
    -0.55, -0.55, 0.55, -0.55, -0.55, 0.55,
    -0.55, 0.55, 0.55, -0.55, 0.55, 0.55,
  ]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(program, "p");
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  gl.viewport(0, 0, canvas.width, canvas.height);
  gl.clearColor(0, 0, 0, 0);

  // Read the centre pixel from the default framebuffer once we have drawn.
  const pixel = new Uint8Array(4);
  function frame() {
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
    out.frames += 1;
    gl.readPixels(Math.round(canvas.width / 2), Math.round(canvas.height / 2),
                  1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
    out.quad = "rgba(" + pixel.join(",") + ")";
    out.ok = pixel[0] > 200 && pixel[1] < 60 && pixel[2] > 80;
    requestAnimationFrame(frame);
  }
  frame();
  out.width = canvas.width; out.height = canvas.height;
})();
</script></body></html>
"""


def _pair(flag):
    return "PASS" if flag else "FAIL"


def _user32():
    return ctypes.WinDLL("user32", use_last_error=True)


def _style(hwnd):
    user32 = _user32()
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    return int(user32.GetWindowLongPtrW(hwnd, -20))


def _set_click_through(hwnd, enabled):
    user32 = _user32()
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_uint,
    ]
    style = _style(hwnd)
    layered, transparent = 0x00080000, 0x00000020
    style = (style | layered | transparent) if enabled else (style & ~transparent)
    user32.SetWindowLongPtrW(hwnd, -20, style)
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
    return style


def _root(hwnd):
    user32 = _user32()
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
    return int(user32.GetAncestor(hwnd, 2) or 0)  # GA_ROOT


def _window_from_point(x, y):
    user32 = _user32()
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    return int(_root(user32.WindowFromPoint(wintypes.POINT(x, y))) or 0)


def _window_rect(hwnd):
    user32 = _user32()
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def _foreground_class():
    user32 = _user32()
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    handle = user32.GetForegroundWindow()
    if not handle:
        return ""
    buffer = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(handle, buffer, 128)
    return buffer.value


def _cpu_seconds():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    times = [wintypes.FILETIME() for _ in range(4)]
    kernel32.GetProcessTimes(kernel32.GetCurrentProcess(), *[ctypes.byref(t) for t in times])
    user = (times[2].dwHighDateTime << 32) | times[2].dwLowDateTime
    kernel = (times[3].dwHighDateTime << 32) | times[3].dwLowDateTime
    return (user + kernel) / 10_000_000.0


def _grab(rect):
    from PIL import ImageGrab

    left, top, width, height = rect
    return ImageGrab.grab(bbox=(left, top, left + width, top + height), all_screens=True)


def _diff_outside(before, after, quad):
    left, top, width, height = quad
    changed = total = 0
    for y in range(after.height):
        inside_y = top <= y < top + height
        for x in range(after.width):
            if inside_y and left <= x < left + width:
                continue
            total += 1
            if before.getpixel((x, y)) != after.getpixel((x, y)):
                changed += 1
    return changed / max(1, total)


def _diff_inside(before, after, quad):
    left, top, width, height = quad
    changed = total = 0
    for y in range(top, min(top + height, after.height)):
        for x in range(left, min(left + width, after.width)):
            total += 1
            if before.getpixel((x, y)) != after.getpixel((x, y)):
                changed += 1
    return changed / max(1, total)


class ProbeWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(*SIZE)
        self.view = QWebEngineView(self)
        self.view.page().setBackgroundColor(QColor(Qt.transparent))
        self.view.setAttribute(Qt.WA_TranslucentBackground)
        self.view.resize(*SIZE)
        self.view.loadFinished.connect(self._loaded)
        self.result = None
        self.load_ok = None
        self.terminated = ""
        self.view.page().renderProcessTerminated.connect(self._terminated)
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(
            screen.x() + (screen.width() - SIZE[0]) // 2,
            screen.y() + (screen.height() - SIZE[1]) // 3,
        )
        self.view.setHtml(PAGE, QUrl("http://127.0.0.1/"))

    def _loaded(self, ok):
        self.load_ok = bool(ok)

    def _terminated(self, status, code):
        self.terminated = "{}:{}".format(status, code)


def _physical(rect):
    screen = QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio()
    x, y, width, height = rect
    return (
        round(x * dpr),
        round(y * dpr),
        round(width * dpr),
        round(height * dpr),
    )


def main():
    if not sys.platform.startswith("win"):
        print("FAIL: the R25 probe is Windows-only (overlay shell is Windows-only).")
        return 2
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    window = ProbeWindow()
    geom = window.frameGeometry()
    logical = (geom.x(), geom.y(), SIZE[0], SIZE[1])
    rect = _physical(logical)
    dpr = QGuiApplication.primaryScreen().devicePixelRatio()
    quad = tuple(round(value * dpr) for value in QUAD)
    state = {}

    def grab_hidden():
        print("step: hidden grab", flush=True)
        window.show()
        window.raise_()
        app.processEvents()
        time.sleep(0.25)
        actual = _window_rect(int(window.winId()))
        window.hide()
        app.processEvents()
        time.sleep(0.45)
        state["hidden"] = _grab(actual)
        state["actual"] = actual
        window.show()
        window.raise_()
        print("window visible={} rect={} foreground={}".format(
            window.isVisible(), actual, _foreground_class() or "-"), flush=True)

    def begin():
        print("step: collecting WebGL result", flush=True)
        script = (
            "JSON.stringify({frames: window.__probe ? window.__probe.frames : null, "
            "ok: window.__probe ? window.__probe.ok : false, "
            "version: window.__probe ? window.__probe.version : '', "
            "renderer: window.__probe ? window.__probe.renderer : '', "
            "quad: window.__probe ? window.__probe.quad : '', "
            "error: (window.__probe && window.__probe.error) || '', "
            "state: document.readyState, "
            "type: typeof window.__probe})"
        )

        def got(value):
            try:
                state["js"] = json.loads(value) if value else {}
            except (TypeError, ValueError):
                state["js"] = {}
            state["cpu0"] = _cpu_seconds()
            state["t0"] = time.perf_counter()
            QTimer.singleShot(2500, finish)

        window.view.page().runJavaScript(script, 0, got)

    def finish():
        state["wall"] = time.perf_counter() - state["t0"]
        state["cpu"] = _cpu_seconds() - state["cpu0"]

        def got_frames(value):
            try:
                state["frames1"] = int(value or 0)
            except (TypeError, ValueError):
                state["frames1"] = 0
            report()

        window.view.page().runJavaScript(
            "window.__probe ? window.__probe.frames : 0", 0, got_frames
        )

    def report():
        print("step: checks", flush=True)
        js = state.get("js") or {}
        wall = max(0.001, state.get("wall") or 0.001)
        fps = (state.get("frames1", 0) - int(js.get("frames") or 0)) / wall
        app.processEvents()
        time.sleep(0.2)
        hwnd = int(window.winId())
        actual = _window_rect(hwnd)
        shown = _grab(actual)
        quad_actual = tuple(round(value * dpr) for value in QUAD)
        outside = _diff_outside(state["hidden"], shown, quad_actual)
        inside = _diff_inside(state["hidden"], shown, quad_actual)

        image = window.view.grab().toImage()
        center = image.pixelColor(image.width() // 2, image.height() // 2).alpha()
        corner = image.pixelColor(2, 2).alpha()
        alpha_ok = corner == 0 and center > 250
        point = (actual[0] + 8, actual[1] + actual[3] - 8)
        _set_click_through(hwnd, False)
        app.processEvents()
        hit_normal = _window_from_point(*point) == hwnd
        _set_click_through(hwnd, True)
        app.processEvents()
        hit_through = _window_from_point(*point) != hwnd
        styled = _style(hwnd)
        _set_click_through(hwnd, False)

        print("load_ok={} terminated={} dpr={}".format(
            window.load_ok, window.terminated or "-", dpr))
        print("WebGL:", json.dumps(js, ensure_ascii=False))
        if window.load_ok is False:
            print("page diagnostics: view={}x{} visible={}".format(
                window.view.width(), window.view.height(), window.isVisible()))
        ok_gl = bool(js.get("ok"))
        print(_pair(ok_gl), "WebGL renders a quad (readPixels at centre)")
        print(
            _pair(alpha_ok),
            "page alpha outside={} centre={} (0=transparent, 255=opaque)".format(
                corner, center
            ),
        )
        print(
            _pair(outside < 0.25 and inside > 0.10),
            "desktop composition outside={:.1%} inside={:.1%}".format(outside, inside),
        )
        print(_pair(hit_normal), "transparent point hits the overlay while interactive")
        print(
            _pair(hit_through and bool(styled & 0x20)),
            "transparent point falls through when WS_EX_TRANSPARENT",
        )
        cpu_ratio = state["cpu"] / wall
        print(
            _pair(fps >= 20 and cpu_ratio < 1.0),
            "idle fps={:.1f} cpu={:.1%}".format(fps, cpu_ratio),
        )
        passed = (
            ok_gl
            and alpha_ok
            and outside < 0.25
            and inside > 0.10
            and hit_normal
            and hit_through
            and fps >= 20
            and cpu_ratio < 1.0
        )
        out_dir = Path(__file__).resolve().parents[3] / "data" / "_m133_probe"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            state["hidden"].save(out_dir / "hidden.png")
            shown.save(out_dir / "shown.png")
            print("screenshots:", out_dir)
        except OSError:
            pass
        window.close()
        app.processEvents()
        app.exit(0 if passed else 1)

    QTimer.singleShot(1200, grab_hidden)
    QTimer.singleShot(2000, begin)
    QTimer.singleShot(20000, lambda: app.exit(2))
    return int(app.exec() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
