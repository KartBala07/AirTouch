"""System-level side effects: mouse buttons, scroll, keys, screenshots."""

from __future__ import annotations

import subprocess
import sys
from typing import Optional, Tuple

CMD_FLAG = 1 << 20
SHIFT_FLAG = 1 << 17
CTRL_FLAG = 1 << 18

KEY_V = 9
KEY_GRAVE = 50      # ` / ~
KEY_UP = 126


class SystemActions:
    """Thin wrapper around Quartz + screencapture, safe to run headless."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        try:
            import Quartz

            self.q = Quartz
        except Exception:
            self.q = None

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self.q is not None

    def _mouse(self, event: int, x: float, y: float, clicks: int = 1) -> None:
        if self.dry_run or self.q is None:
            return
        q = self.q
        ev = q.CGEventCreateMouseEvent(None, event, (float(x), float(y)), 0)
        if ev is None:
            return
        q.CGEventSetIntegerValueField(ev, q.kCGMouseEventClickState, clicks)
        q.CGEventPost(q.kCGHIDEventTap, ev)

    def _key(self, code: int, flags: int = 0) -> None:
        if self.dry_run or self.q is None:
            return
        q = self.q
        src = q.CGEventSourceCreate(q.kCGEventSourceStateHIDSystemState)
        for down in (True, False):
            ev = q.CGEventCreateKeyboardEvent(src, code, down)
            if ev is None:
                continue
            q.CGEventSetFlags(ev, flags)
            q.CGEventPost(q.kCGHIDEventTap, ev)

    # ------------------------------------------------------------------
    def right_down(self, x: float, y: float) -> None:
        self._mouse(self.q.kCGEventRightMouseDown, x, y)

    def right_drag(self, x: float, y: float) -> None:
        self._mouse(self.q.kCGEventRightMouseDragged, x, y)

    def right_up(self, x: float, y: float) -> None:
        self._mouse(self.q.kCGEventRightMouseUp, x, y)

    def scroll(self, pixels: float) -> None:
        """Positive pixels scroll up (content down); negative scroll down."""
        if self.dry_run or self.q is None:
            return
        q = self.q
        ev = q.CGEventCreateScrollWheelEvent(
            None, q.kCGScrollEventUnitPixel, 1, int(round(pixels))
        )
        if ev is not None:
            q.CGEventPost(q.kCGHIDEventTap, ev)

    def switch_window(self, direction: float = 1.0) -> None:
        """cmd+` cycles forward, cmd+shift+` backwards."""
        self._key(KEY_GRAVE, CMD_FLAG | (SHIFT_FLAG if direction < 0 else 0))

    def mission_control(self) -> None:
        self._key(KEY_UP, CTRL_FLAG)

    def copy(self) -> None:
        self._key(KEY_V, CMD_FLAG)

    def paste(self) -> None:
        self._key(KEY_V, CMD_FLAG)

    def screenshot_to_clipboard(self) -> bool:
        """Silent full-screen capture straight into the pasteboard."""
        if self.dry_run:
            return True
        try:
            result = subprocess.run(
                ["screencapture", "-x", "-c"],
                timeout=15,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except Exception as exc:
            print(f"[airtouch] screenshot failed: {exc}", file=sys.stderr)
            return False
        if result.returncode != 0:
            print(
                f"[airtouch] screenshot failed: {result.stderr.decode(errors='replace').strip()[:200]}",
                file=sys.stderr,
            )
            return False
        return True
