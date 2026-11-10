"""Filesystem locations for AirTouch.

When frozen (PyInstaller .app) resources live inside the bundle and anything
writable must go to Application Support, since /Applications is read-only.
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_NAME = "AirTouch"
BUNDLE_ID = "com.bala.airtouch"


def resource_dir() -> Path:
    """Directory holding models/ and other read-only assets."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base)
        return Path(sys.executable).resolve().parent.parent / "Resources"
    return Path(__file__).resolve().parent.parent


def support_dir() -> Path:
    path = Path.home() / "Library" / "Application Support" / APP_NAME
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        path = Path.home() / "." + APP_NAME.lower()
        path.mkdir(parents=True, exist_ok=True)
    return path


def model_path() -> Path:
    return resource_dir() / "models" / "hand_landmarker.task"


def calib_path() -> Path:
    return support_dir() / "calibration.json"


def config_path() -> Path:
    return support_dir() / "config.json"


def log_path() -> Path:
    return support_dir() / "airtouch.log"
