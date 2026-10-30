"""Small persistent state for desktop chrome (overlay, theme, window geometry)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .theme import normalize_font_scale, normalize_theme


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
        # Windows can briefly lock the target while another handle (antivirus,
        # indexer) closes it, so retry the atomic swap instead of losing state.
        for attempt in range(5):
            try:
                os.replace(temp, self.path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.01)

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

    def active_session(self):
        value = self.load().get("active_session")
        return value.strip() if isinstance(value, str) and value.strip() else ""

    def set_active_session(self, session_id):
        return self.set(active_session=str(session_id or ""))

    def theme(self):
        return normalize_theme(self.load().get("theme"))

    def set_theme(self, name):
        return self.set(theme=normalize_theme(name))

    def font_scale(self):
        return normalize_font_scale(self.load().get("font_scale", 100))

    def set_font_scale(self, percent):
        return self.set(font_scale=normalize_font_scale(percent))

    def reduce_motion(self):
        return bool(self.load().get("reduce_motion", False))

    def set_reduce_motion(self, enabled):
        return self.set(reduce_motion=bool(enabled))

    def inspector_visible(self):
        return bool(self.load().get("inspector_visible", True))

    def set_inspector_visible(self, visible):
        return self.set(inspector_visible=bool(visible))

    def inspector_width(self):
        value = self.load().get("inspector_width")
        try:
            width = int(value)
        except (TypeError, ValueError):
            return 268
        return max(220, min(width, 420))

    def set_inspector_width(self, width):
        try:
            value = int(width)
        except (TypeError, ValueError):
            return self.load()
        return self.set(inspector_width=max(220, min(value, 420)))

    def window_geometry(self):
        value = self.load().get("window_geometry")
        if (
            isinstance(value, (list, tuple))
            and len(value) == 4
            and all(isinstance(item, (int, float)) for item in value)
            and int(value[2]) >= 200
            and int(value[3]) >= 200
        ):
            return tuple(int(item) for item in value)
        return None

    def set_window_geometry(self, x, y, width, height):
        return self.set(
            window_geometry=[int(x), int(y), int(width), int(height)]
        )
