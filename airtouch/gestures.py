"""Turn hand postures into discrete actions.

Postures come from ``core.classify_posture`` and are stabilised here, so a
single noisy frame cannot trigger anything.

    point       cursor follows the finger, poke = click
    pinch       quick = click, two quick = select, long hold = right click / drag
    fist        hold 0.4 s = screenshot to clipboard; opening the hand = paste
    two_finger  vertical swipe = scroll (up = scroll down)
    palm        horizontal swipe = switch window, swipe up = Mission Control
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .core import Hand


@dataclass
class Action:
    kind: str          # click | double_click | right_down | right_up | scroll
                       # | screenshot | paste | switch_window | mission_control
    value: float = 0.0


class GestureEngine:
    STABLE = 0.10        # a posture must hold this long before it takes over
    HOLD_PINCH = 0.75    # pinch this long = right button down (drag/right click)
    DOUBLE = 0.55        # two pinches inside this = double click
    FIST_HOLD = 0.40     # fist this long = screenshot
    PASTE_WINDOW = 12.0  # opening the hand within this pastes
    SWIPE = 0.16         # normalised travel that counts as a swipe
    SWIPE_TIME = 0.60
    COOLDOWN = 0.90
    SCROLL_STEP = 0.0035  # normalised travel per wheel emission
    SCROLL_GAIN = 1400.0  # normalised travel -> wheel pixels

    def __init__(self) -> None:
        self.mode = "none"
        self.right_active = False
        self._raw = "none"
        self._raw_since = 0.0
        self._mode_since = 0.0

        self._pinch_start = 0.0
        self._last_pinch_t = 0.0
        self._fist_shot = False
        self._paste_armed_at = 0.0

        self._palm_start: Optional[Tuple[float, float, float]] = None
        self._last_swipe = -1e9
        self._scroll_prev: Optional[float] = None
        self._scroll_acc = 0.0

    # ------------------------------------------------------------------
    @property
    def cursor_active(self) -> bool:
        """Only these modes may move the pointer."""
        return self.mode == "point" or (self.mode == "pinch" and self.right_active)

    def reset(self) -> None:
        self.mode = "none"
        self.right_active = False
        self._raw = "none"
        self._raw_since = 0.0
        self._pinch_start = 0.0
        self._fist_shot = False
        self._palm_start = None
        self._last_swipe = -1e9
        self._scroll_prev = None
        self._scroll_acc = 0.0

    # ------------------------------------------------------------------
    def update(self, hand: Optional[Hand], now: float,
               allow_input: bool = True, allow_gestures: bool = True) -> List[Action]:
        if hand is None or not allow_input:
            if self.mode != "none":
                self.reset()
            return []

        out: List[Action] = []
        raw = hand.posture or "none"
        # big gestures can be switched off (or gated by hand position) without
        # losing the pointer and the clicks
        if not allow_gestures and raw in ("fist", "palm", "two_finger"):
            raw = "none"

        if raw != self._raw:
            self._raw = raw
            self._raw_since = now

        # hysteresis: ignore postures that flash by for less than STABLE
        if raw != self.mode and (now - self._raw_since) >= self.STABLE:
            out.extend(self._leave(now))
            self._enter(raw, now)

        out.extend(self._tick(hand, now))
        return out

    # ------------------------------------------------------------------
    def _enter(self, mode: str, now: float) -> None:
        self.mode = mode
        self._mode_since = now
        self.right_active = False
        self._palm_start = None
        self._scroll_prev = None
        self._scroll_acc = 0.0
        if mode == "pinch":
            self._pinch_start = now
        elif mode == "fist":
            self._fist_shot = False

    def _leave(self, now: float) -> List[Action]:
        out: List[Action] = []
        if self.mode == "pinch":
            out.extend(self._end_pinch(now))
        elif self.mode == "fist":
            if self._paste_armed_at and (now - self._paste_armed_at) < self.PASTE_WINDOW:
                self._paste_armed_at = 0.0
                out.append(Action("paste"))
            self._fist_shot = False
        return out

    def _end_pinch(self, now: float) -> List[Action]:
        if self.right_active:
            self.right_active = False
            return [Action("right_up")]
        if self._pinch_start and (now - self._pinch_start) < self.HOLD_PINCH:
            # a quick pinch is a click; two in a row is a double (select)
            if self._last_pinch_t and (now - self._last_pinch_t) < self.DOUBLE:
                self._last_pinch_t = 0.0
                return [Action("double_click")]
            self._last_pinch_t = now
            return [Action("click")]
        return []

    # ------------------------------------------------------------------
    def _tick(self, hand: Hand, now: float) -> List[Action]:
        if self.mode == "pinch":
            if not self.right_active and (now - self._pinch_start) >= self.HOLD_PINCH:
                self.right_active = True
                return [Action("right_down")]
            return []

        if self.mode == "fist":
            if not self._fist_shot and (now - self._mode_since) >= self.FIST_HOLD:
                self._fist_shot = True
                self._paste_armed_at = now
                return [Action("screenshot")]
            return []

        if self.mode == "two_finger":
            return self._scroll(hand, now)

        if self.mode == "palm":
            return self._swipe(hand, now)

        return []

    # ------------------------------------------------------------------
    def _scroll(self, hand: Hand, now: float) -> List[Action]:
        y = hand.tip[1]
        if self._scroll_prev is None:
            self._scroll_prev = y
            return []
        dy = y - self._scroll_prev
        self._scroll_prev = y
        if abs(dy) < 0.0015:
            return []
        self._scroll_acc += dy
        if abs(self._scroll_acc) < self.SCROLL_STEP:
            return []
        pixels = self._scroll_acc * self.SCROLL_GAIN
        self._scroll_acc = 0.0
        return [Action("scroll", pixels)]

    def _swipe(self, hand: Hand, now: float) -> List[Action]:
        x, y = hand.tip
        if self._palm_start is None:
            self._palm_start = (now, x, y)
            return []
        t0, x0, y0 = self._palm_start
        if (now - t0) > self.SWIPE_TIME:
            self._palm_start = (now, x, y)
            return []
        if (now - self._last_swipe) < self.COOLDOWN:
            return []

        dx = x - x0
        dy = y - y0
        if abs(dx) > self.SWIPE and abs(dx) > abs(dy) * 1.4:
            self._last_swipe = now
            self._palm_start = None
            return [Action("switch_window", 1.0 if dx > 0 else -1.0)]
        if dy < -self.SWIPE and abs(dy) > abs(dx) * 1.4:
            self._last_swipe = now
            self._palm_start = None
            return [Action("mission_control")]
        return []
