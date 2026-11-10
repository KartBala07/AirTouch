"""Persistent user settings, stored in ~/Library/Application Support/AirTouch."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from . import paths

CONFIG_VERSION = 3

DEFAULTS: Dict[str, Any] = {
    "config_version": 3,
    "mode": "auto",            # auto | relative | absolute
    "gain": 2.2,               # air-trackpad cursor speed
    "push_threshold": 1.050,   # hand-size growth that counts as a poke
    "pinch_threshold": 0.34,   # thumb/index distance ratio that counts as a pinch
    "motion_gate": True,       # only run MediaPipe when the frame changed
    "show_preview": True,      # camera thumbnail window
    "show_hud": True,          # status HUD window
    "click_on_single": True,   # False = only a double-tap clicks
    "dry_run": False,          # never post real mouse events
    "camera_index": 0,
    "capture_width": 1280,     # 720p: sharper landmarks and a 16:9 preview
    "capture_height": 720,
    "process_fps": 30.0,       # inference cap (frames above this are skipped)
    "show_cursor": True,       # pointer ring overlay
    "smoothing": 0.30,         # One Euro cutoff (lower = steadier, higher = snappier)
    "grid_cell": 6.0,          # pointer snaps to this pixel grid
    "min_hand_scale": 0.085,   # ignore tiny/far hands
    "gesture_wrist_y": 0.42,   # gestures need the wrist below this line
    "gestures": True,          # fist/palm/two-finger gestures
    "check_updates": True,     # look for a newer release at launch
    "last_update_check": 0.0,
    "require_pointing": True,  # ignore a hand that is closed / holding something
    "overlays_collapsed": False,
    "ax_prompted": False,      # we already asked for Accessibility once
}

NUMBERS = ("gain", "push_threshold", "pinch_threshold", "process_fps", "last_update_check",
           "smoothing", "grid_cell", "min_hand_scale", "gesture_wrist_y")
INTS = ("camera_index", "capture_width", "capture_height")
BOOLS = ("motion_gate", "show_preview", "show_hud", "show_cursor", "click_on_single",
         "dry_run", "require_pointing", "overlays_collapsed", "ax_prompted", "check_updates",
         "gestures")


class Config:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else paths.config_path()
        self.data: Dict[str, Any] = dict(DEFAULTS)
        self.load()

    # ------------------------------------------------------------------
    def load(self) -> "Config":
        try:
            raw = json.loads(Path(self.path).read_text())
        except Exception:
            return self
        if not isinstance(raw, dict):
            return self
        version = int(raw.get("config_version") or 1)
        for key, default in DEFAULTS.items():
            if key not in raw:
                continue
            value = raw[key]
            try:
                if key in BOOLS:
                    self.data[key] = bool(value)
                elif key in NUMBERS:
                    self.data[key] = float(value)
                elif key in INTS:
                    self.data[key] = int(value)
                elif key == "mode" and value in ("auto", "relative", "absolute"):
                    self.data[key] = str(value)
            except (TypeError, ValueError):
                pass

        if version < 3:
            # 0.6.0: One Euro filter at 30 fps, 6 px grid, gestures on
            self.data.update({
                "process_fps": 30.0,
                "grid_cell": 6.0,
                "smoothing": 0.30,
                "gestures": True,
                "min_hand_scale": 0.085,
                "gesture_wrist_y": 0.42,
                "config_version": CONFIG_VERSION,
            })
            self.save()
        if version < 2:
            # 0.5.0 raised the capture to 720p and added grid smoothing; older
            # config files would otherwise pin everyone to the blurry defaults.
            self.data.update({
                "capture_width": 1280,
                "capture_height": 720,
                "smoothing": 0.30,
                "grid_cell": 8.0,
                "config_version": CONFIG_VERSION,
            })
            self.save()
        return self

    def save(self) -> None:
        try:
            Path(self.path).write_text(json.dumps(self.data, indent=2))
        except Exception:
            pass

    # ------------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        self.save()

    def update(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            if key in DEFAULTS:
                self.data[key] = value
        self.save()

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)
