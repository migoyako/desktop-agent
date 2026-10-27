"""Small persistent state for desktop chrome (overlay position and visibility)."""

from __future__ import annotations

import json
import os
from pathlib import Path


def clamp_position(x, y, width, height, screens):
    """Keep a window inside the nearest available screen rectangle."""
    screens = [tuple(int(value) for value in screen) for screen in screens]
    if not screens:
        return int(x), int(y)

    def overlap(screen):
        sx, sy, sw, sh = screen
        left = max(sx, x)
        top = max(sy, y)
        right = min(sx + sw, x + width)
        bottom = min(sy + sh, y + height)
        return max(0, right - left) * max(0, bottom - top)

    best = max(screens, key=overlap)
    if overlap(best) == 0:
        center_x = x + width / 2
        center_y = y + height / 2

        def distance(screen):
            sx, sy, sw, sh = screen
            return abs(sx + sw / 2 - center_x) + abs(sy + sh / 2 - center_y)

        best = min(screens, key=distance)

    sx, sy, sw, sh = best
    max_x = sx + max(0, sw - width)
    max_y = sy + max(0, sh - height)
    return max(sx, min(int(x), max_x)), max(sy, min(int(y), max_y))


class UiStateStore:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "ui_state.json"

    def load(self):
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp, self.path)

    def set(self, **values):
        state = self.load()
        state.update(values)
        self.save(state)
        return state

    def overlay_position(self):
        value = self.load().get("overlay_position")
        if (
            isinstance(value, (list, tuple))
            and len(value) == 2
            and all(isinstance(item, (int, float)) for item in value)
        ):
            return int(value[0]), int(value[1])
        return None

    def set_overlay_position(self, x, y):
        return self.set(overlay_position=[int(x), int(y)])

    def overlay_visible(self):
        return bool(self.load().get("overlay_visible", True))

    def set_overlay_visible(self, visible):
        return self.set(overlay_visible=bool(visible))
