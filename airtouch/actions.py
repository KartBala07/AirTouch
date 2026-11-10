"""System-level side effects: mouse buttons, scroll, keys, screenshots."""

from __future__ import annotations

import subprocess
import sys
import time
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
        self.last_posted = False
        self._warned = False
        try:
            import Quartz

            self.q = Quartz
        except Exception:
            self.q = None

    @property
    def trusted(self) -> bool:
        """False when macOS will silently drop every event we post."""
        if self.dry_run:
            return True
        try:
            import ctypes

            lib = ctypes.CDLL(
                "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
            )
            lib.AXIsProcessTrusted.restype = ctypes.c_bool
            return bool(lib.AXIsProcessTrusted())
        except Exception:
            return False

    def _post(self, ev) -> bool:
        """Post one event, refusing (loudly, once) when we are not trusted."""
        if ev is None or self.q is None:
            self.last_posted = False
            return False
        if self.dry_run:
            self.last_posted = True
            return True
        if not self.trusted:
            if not self._warned:
                self._warned = True
                print(
                    "[airtouch] BLOCKED: Accessibility is off for this app, so macOS\n"
                    "           drops every click / scroll / key we send.\n"
                    "           Fix: menu bar icon -> Enable Accessibility...",
                    file=sys.stderr,
                )
            self.last_posted = False
            return False
        self.q.CGEventPost(self.q.kCGHIDEventTap, ev)
        self.last_posted = True
        return True

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self.q is not None

    def _mouse(self, event: int, x: float, y: float, clicks: int = 1) -> bool:
        if self.q is None:
            return False
        q = self.q
        ev = q.CGEventCreateMouseEvent(None, event, (float(x), float(y)), 0)
        if ev is None:
            return False
        q.CGEventSetIntegerValueField(ev, q.kCGMouseEventClickState, clicks)
        return self._post(ev)

    def _key(self, code: int, flags: int = 0) -> bool:
        if self.q is None:
            return False
        q = self.q
        ok = False
        src = q.CGEventSourceCreate(q.kCGEventSourceStateHIDSystemState)
        for down in (True, False):
            ev = q.CGEventCreateKeyboardEvent(src, code, down)
            if ev is None:
                continue
            q.CGEventSetFlags(ev, flags)
            ok = self._post(ev) or ok
        return ok

    # ------------------------------------------------------------------
    def right_down(self, x: float, y: float) -> bool:
        return self._mouse(self.q.kCGEventRightMouseDown, x, y)

    def right_drag(self, x: float, y: float) -> bool:
        return self._mouse(self.q.kCGEventRightMouseDragged, x, y)

    def right_up(self, x: float, y: float) -> bool:
        return self._mouse(self.q.kCGEventRightMouseUp, x, y)

    def click_test(self) -> Tuple[bool, str]:
        """Nudge the cursor and read it back: proof that events land."""
        if self.q is None:
            return False, "Quartz unavailable"
        if self.dry_run:
            return True, "dry run"
        q = self.q
        before = q.CGEventGetLocation(q.CGEventCreate(None))
        target = (before.x + 40.0, before.y)
        if not self._post(q.CGEventCreateMouseEvent(None, q.kCGEventMouseMoved, target, 0)):
            return False, "blocked - Accessibility is off"
        time.sleep(0.06)
        after = q.CGEventGetLocation(q.CGEventCreate(None))
        if abs(after.x - target[0]) < 1.0 and abs(after.y - target[1]) < 1.0:
            return True, f"cursor moved to {int(after.x)},{int(after.y)}"
        # put it back
        q.CGEventPost(q.kCGHIDEventTap, q.CGEventCreateMouseEvent(None, q.kCGEventMouseMoved, before, 0))
        return False, "events dropped - Accessibility is off"

    def scroll(self, pixels: float) -> bool:
        """Positive pixels scroll up (content down); negative scroll down."""
        if self.q is None:
            return False
        q = self.q
        ev = q.CGEventCreateScrollWheelEvent(
            None, q.kCGScrollEventUnitPixel, 1, int(round(pixels))
        )
        return self._post(ev)

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
