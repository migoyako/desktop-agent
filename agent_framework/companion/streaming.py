"""Streaming helpers shared by the runtime and desktop UI."""

from __future__ import annotations

import re
from typing import Dict, List


CONTROL_MARKER_RE = re.compile(
    r"\[\[\s*(mood|action|sticker)\s*:\s*([^\]]*?)\s*\]\]",
    re.IGNORECASE,
)


class GenerationCancelled(RuntimeError):
    """Raised when the user stops a generation."""

    def __init__(self, partial: str = ""):
        super().__init__("已停止生成。")
        self.partial = partial or ""


def strip_control_markers(text: str) -> str:
    return CONTROL_MARKER_RE.sub("", text or "")


class MarkerFilter:
    """Removes inline control markers that may be split across stream chunks."""

    def __init__(self):
        self.buffer = ""
        self.markers: List[Dict[str, str]] = []

    def feed(self, chunk: str) -> str:
        self.buffer += chunk or ""
        return self._drain(final=False)

    def finish(self) -> str:
        return self._drain(final=True)

    def _drain(self, final: bool) -> str:
        pieces: List[str] = []
        while True:
            start = self.buffer.find("[[")
            if start < 0:
                if final:
                    pieces.append(self.buffer)
                    self.buffer = ""
                elif self.buffer.endswith("["):
                    pieces.append(self.buffer[:-1])
                    self.buffer = "["
                else:
                    pieces.append(self.buffer)
                    self.buffer = ""
                break

            pieces.append(self.buffer[:start])
            rest = self.buffer[start:]
            marker = CONTROL_MARKER_RE.match(rest)
            if marker is not None:
                self.markers.append(
                    {
                        "kind": marker.group(1).lower(),
                        "value": marker.group(2).strip(),
                    }
                )
                self.buffer = rest[marker.end() :]
                continue

            close = rest.find("]]")
            if close < 0:
                if not final:
                    self.buffer = rest
                    break
                pieces.append(rest)
                self.buffer = ""
                break
            pieces.append(rest[: close + 2])
            self.buffer = rest[close + 2 :]
        return "".join(pieces)
