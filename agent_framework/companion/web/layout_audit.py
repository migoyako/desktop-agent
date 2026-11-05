"""Browser regression for the M12 scaling and typography contract.

Renders every page at each font scale in a real Chromium viewport, fails when a
control overflows or clips its own text, and keeps the screenshots for review.
Playwright is an optional dependency; the probe itself is plain DOM geometry.
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import tempfile
import time
import zlib
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ...llm import OfflineLLMClient
from ..app import create_application_service
from ..runtime import CompanionRuntime
from ..search import SearchService
from ..store import CompanionStore
from .server import WebAdapter

SCALES = (100, 125, 150, 200)
PAGES = (
    ("main", "/", (".msg", ((1152, 648), (1024, 576)))),
    ("settings", "/settings", (".nav button", ((680, 560),))),
    ("console", "/console", (".tabs button", ((1040, 720),))),
    ("overlay", "/overlay", (".pet", ((363, 648),))),
)
CONTROLS = {
    "main": ".win-actions [data-cmd]",
    "settings": "#closeBtn",
    "console": "#closeTop",
    "overlay": "#close",
}
SEEDS = (
    ("chat", "帮我把缩放整改讲清楚：字体放大时按钮高度、右栏标签和窗口控制都要跟得上。"),
    ("chat", "给个例子：`inline code` 与超长标识 companion_layout_audit_identifier_without_breaks"),
)

BACKGROUND_PROBE = """
() => ({
  on: document.documentElement.dataset.bg || "",
  scrim: getComputedStyle(document.documentElement)
    .getPropertyValue('--bg-scrim').trim(),
  image: getComputedStyle(document.querySelector('.shell') || document.querySelector('.win') || document.body)
    .backgroundImage
})
"""

PROBE = """
(selector) => {
  document.body.classList.remove("browser");
  const label = (node) => node.tagName.toLowerCase()
    + (node.id ? "#" + node.id : "")
    + (typeof node.className === "string" && node.className.trim()
      ? "." + node.className.trim().split(/\\s+/).slice(0, 2).join(".")
      : "");
  const problems = [];
  const controls = [];
  for (const node of document.querySelectorAll("body *")) {
    if (!node.getClientRects().length) continue;
    const style = getComputedStyle(node);
    if (style.visibility === "hidden") continue;
    const box = node.getBoundingClientRect();
    const clipX = ["auto", "scroll", "hidden"].includes(style.overflowX);
    const clipY = ["auto", "scroll", "hidden"].includes(style.overflowY);
    if (!clipX && node.scrollWidth - node.clientWidth > 1) {
      problems.push({ axis: "x", node: label(node), delta: node.scrollWidth - node.clientWidth });
    }
    if (!clipY && node.scrollHeight - node.clientHeight > 1) {
      problems.push({ axis: "y", node: label(node), delta: node.scrollHeight - node.clientHeight });
    }
    if (box.right > window.innerWidth + 1 || box.left < -1) {
      problems.push({ axis: "outside", node: label(node),
        delta: Math.round(Math.max(box.right - window.innerWidth, -box.left)) });
    }
  }
  for (const button of document.querySelectorAll(selector)) {
    const box = button.getBoundingClientRect();
    controls.push({
      name: button.dataset.cmd || button.id || button.textContent.trim(),
      width: Math.round(box.width),
      height: Math.round(box.height),
      iconOnly: getComputedStyle(button.querySelector(".lbl") || button).display === "none",
      hit: box.width > 0 && box.height >= 16
    });
  }
  // The reading measure is capacity in characters, not the line actually typed:
  // one CJK glyph advances by roughly the font size.
  const measures = [];
  for (const node of document.querySelectorAll(".bubble .body")) {
    const size = parseFloat(getComputedStyle(node).fontSize);
    if (!size) continue;
    measures.push({
      chars: Math.round((node.clientWidth / size) * 10) / 10,
      width: Math.round(node.clientWidth),
      font: size
    });
  }
  const chat = document.querySelector(".chat");
  const bar = document.querySelector(".topbar");
  const chrome = {
    topbar: bar ? Math.round(bar.getBoundingClientRect().height) : 0,
    chat: chat ? Math.round(chat.getBoundingClientRect().height) : 0,
    viewport: window.innerHeight
  };
  return {
    scale: getComputedStyle(document.documentElement).getPropertyValue("--scale").trim(),
    problems,
    controls,
    measures,
    chrome
  };
}
"""


def _adapter(data_dir):
    store = CompanionStore(data_dir / "companion.db")
    runtime = CompanionRuntime(
        store=store,
        llm_client=OfflineLLMClient(),
        search_service=SearchService(_NoSearch()),
        data_dir=data_dir,
        session_id="main",
        offline=True,
    )
    runtime.select_workspace(str(data_dir))
    return WebAdapter(create_application_service(runtime), data_dir=data_dir).start()


class _NoSearch:
    def search(self, query, limit=5):
        return []


def _url(adapter, path):
    return "http://{}{}?{}".format(adapter.address, path, urlencode({"t": adapter.token}))


def _post_ui(adapter, payload):
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        _url(adapter, "/api/ui"),
        data=body,
        headers={"X-Companion-Token": adapter.token, "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10):
        pass


def _set_scale(adapter, scale):
    _post_ui(adapter, {"font_scale": scale})


def _sample_png(size=8):
    """A small coloured PNG so the background pass has a real image to load."""
    raw = b"".join(
        b"\x00" + bytes((198, 74, 108)) * size for _ in range(size)
    )

    def chunk(tag, data):
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _install_background(data_dir, persona_id):
    folder = Path(data_dir) / "assets" / str(persona_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "background.png").write_bytes(_sample_png())


def _wait_reply(adapter, before, limit=20.0):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        data = adapter.bootstrap()
        if len(data["messages"]) > before and not data["run"]:
            return len(data["messages"])
        time.sleep(0.1)
    raise RuntimeError("离线回合未在 %.0f 秒内落库" % limit)


def _settle(page, expected, timeout=5.0):
    """Pages paint the scale only after their first bootstrap fetch, so give the
    root style a bounded chance to land before probing."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if page.evaluate("getComputedStyle(document.documentElement)"
                         ".getPropertyValue('--scale').trim()") == expected:
            return
        page.wait_for_timeout(50)


def _seed(adapter):
    count = len(adapter.bootstrap()["messages"])
    for mode, text in SEEDS:
        adapter.run_turn(text, mode)
        count = _wait_reply(adapter, count)


@contextmanager
def _scratch_temp(root):
    """Keep the browser driver inside the run's own directory; shared Windows Temp
    is not always writable and EPERM on mkdtemp is not a product failure."""
    scratch = root / "browser"
    scratch.mkdir(parents=True, exist_ok=True)
    previous = {key: os.environ.get(key) for key in ("TEMP", "TMP", "TMPDIR")}
    for key in previous:
        os.environ[key] = str(scratch)
    try:
        yield scratch
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run_audit(out_dir=None, scales=SCALES, background=False):
    """Render every page/scale pair and return problems, control geometry and shots.

    ``background=True`` adds the M13.2 combo: a user image is placed and enabled
    before the pages load, and the overlay must stay transparent.
    """
    from playwright.sync_api import sync_playwright
    shots = []
    problems = []
    controls = []
    measures = []
    chrome = []
    background_checks = []
    output = Path(out_dir) if out_dir else None
    if output is not None:
        output.mkdir(parents=True, exist_ok=True)
    temp = tempfile.TemporaryDirectory(prefix="companion-layout-")
    adapter = _adapter(Path(temp.name))
    try:
        _seed(adapter)
        if background:
            _install_background(Path(temp.name), adapter.runtime.persona_id)
            _post_ui(adapter, {"background_enabled": True})
        with _scratch_temp(Path(temp.name)), sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1152, "height": 648})
            try:
                for scale in scales:
                    _set_scale(adapter, scale)
                    for name, path, (ready, sizes) in PAGES:
                        for width, height in sizes:
                            page.set_viewport_size({"width": width, "height": height})
                            page.goto(_url(adapter, path), wait_until="load")
                            page.wait_for_selector(ready, timeout=10000)
                            expected = "{:g}".format(scale / 100)
                            _settle(page, expected)
                            page.wait_for_timeout(120)
                            result = page.evaluate(PROBE, CONTROLS[name])
                            if result["scale"] != expected:
                                problems.append(
                                    {
                                        "page": name,
                                        "scale": scale,
                                        "size": [width, height],
                                        "axis": "scale",
                                        "node": ":root",
                                        "delta": "{}!={}".format(result["scale"], expected),
                                    }
                                )
                            for problem in result["problems"]:
                                problem.update({"page": name, "scale": scale, "size": [width, height]})
                                problems.append(problem)
                            for control in result["controls"]:
                                control.update({"page": name, "scale": scale, "size": [width, height]})
                                controls.append(control)
                            for measure in result["measures"]:
                                measure.update({"page": name, "scale": scale, "size": [width, height]})
                                measures.append(measure)
                            chrome.append(
                                {"page": name, "scale": scale, "size": [width, height],
                                 **result["chrome"]}
                            )
                            if background:
                                state = page.evaluate(BACKGROUND_PROBE)
                                wants_image = name != "overlay"
                                washed = state["scrim"] not in ("", "rgba(0, 0, 0, 0)")
                                check = {
                                    "page": name,
                                    "scale": scale,
                                    "size": [width, height],
                                    "on": state["on"] == "on",
                                    "washed": washed,
                                    "image": "url(" in state["image"],
                                }
                                background_checks.append(check)
                                if (
                                    check["on"] != wants_image
                                    or check["washed"] != wants_image
                                    or check["image"] != wants_image
                                ):
                                    problems.append(
                                        {
                                            "page": name,
                                            "scale": scale,
                                            "size": [width, height],
                                            "axis": "background",
                                            "node": "html",
                                            "delta": state,
                                        }
                                    )
                            if output is not None:
                                shot = output / "{}-{}x{}-{}.png".format(name, width, height, scale)
                                page.screenshot(path=str(shot))
                                shots.append(str(shot))
            finally:
                browser.close()
    finally:
        adapter.stop()
        temp.cleanup()
    unreachable = [item for item in controls if not item["hit"]]
    for item in unreachable:
        problems.append(
            {
                "page": item["page"],
                "scale": item["scale"],
                "size": item["size"],
                "axis": "control",
                "node": item["name"],
                "delta": item["height"],
            }
        )
    return {"problems": problems, "controls": controls, "measures": measures, "chrome": chrome,
            "background": background_checks, "shots": shots}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Web 界面字号缩放回归")
    parser.add_argument("--out", help="截图输出目录，留空则不保存截图")
    parser.add_argument("--scale", type=int, action="append", help="只跑指定档位，可重复")
    parser.add_argument(
        "--background",
        action="store_true",
        help="先放入并启用一张背景图，再跑一遍（浮窗必须保持透明）",
    )
    args = parser.parse_args(argv)
    scales = tuple(args.scale) if args.scale else SCALES
    report = run_audit(out_dir=args.out, scales=scales, background=args.background)
    print("页面 × 档位：{} 档，问题 {} 项，控件 {} 个，截图 {} 张".format(
        len(scales), len(report["problems"]), len(report["controls"]), len(report["shots"])))
    if args.background:
        print("背景检查：{} 项（主窗/设置/管理台应有图与遮罩，浮窗应保持透明）".format(
            len(report["background"])))
    for problem in report["problems"]:
        merged = {"size": "-", "scale": "?", **problem}
        print("[FAIL] {page} {size} {scale}% {axis} {node} +{delta}".format(**merged))
    for scale in scales:
        for bar in [item for item in report["chrome"]
                    if item["page"] == "main" and item["scale"] == scale]:
            rows = [item for item in report["measures"]
                    if item["scale"] == scale and item["size"] == bar["size"]]
            print("  {:>3}% {}  每行字数 {}  顶栏 {}px  聊天区 {:.0f}%".format(
                scale,
                bar["size"],
                "{}–{}".format(min(round(r["chars"]) for r in rows),
                               max(round(r["chars"]) for r in rows)) if rows else "-",
                bar["topbar"],
                100.0 * bar["chat"] / bar["viewport"],
            ))
    if report["problems"]:
        return 1
    print("Layout audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
