"""Pointer filtering: One Euro + grid snapping.

The One Euro filter (Casiez, Roussel & Vogel, CHI 2012 -
https://github.com/casiez/OneEuroFilter) is adaptive: heavy smoothing when the
hand is still (jitter disappears) and almost none when it moves fast (no lag).
A plain EMA has to pick one or the other, which is exactly why the pointer felt
both shaky and sluggish.

On top of that the output snaps to a small grid with a hysteresis band, so the
cursor lands on whole pixels instead of twitching.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple


class _LowPass:
    def __init__(self) -> None:
        self.y: Optional[float] = None

    def __call__(self, x: float, alpha: float) -> float:
        if self.y is None:
            self.y = x
        else:
            self.y = alpha * x + (1.0 - alpha) * self.y
        return self.y


def _alpha(cutoff: float, dt: float) -> float:
    tau = 1.0 / (2.0 * math.pi * max(cutoff, 1e-3))
    return 1.0 / (1.0 + tau / max(dt, 1e-4))


class OneEuro2D:
    def __init__(self, min_cutoff: float = 1.2, beta: float = 0.03,
                 d_cutoff: float = 1.0, freq: float = 30.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.dt = 1.0 / max(freq, 1.0)
        self._x = _LowPass()
        self._y = _LowPass()
        self._dx = _LowPass()
        self._dy = _LowPass()
        self._prev: Optional[Tuple[float, float]] = None

    def reset(self) -> None:
        self._x = _LowPass()
        self._y = _LowPass()
        self._dx = _LowPass()
        self._dy = _LowPass()
        self._prev = None

    def update(self, x: float, y: float, dt: Optional[float] = None) -> Tuple[float, float]:
        dt = max(dt or self.dt, 1e-4)
        if self._prev is None:
            self._prev = (x, y)
            return self._x(x, 1.0), self._y(y, 1.0)

        px, py = self._prev
        self._prev = (x, y)
        dx = (x - px) / dt
        dy = (y - py) / dt

        ad = _alpha(self.d_cutoff, dt)
        dx_hat = self._dx(dx, ad)
        dy_hat = self._dy(dy, ad)

        cutoff_x = self.min_cutoff + self.beta * abs(dx_hat)
        cutoff_y = self.min_cutoff + self.beta * abs(dy_hat)
        return self._x(x, _alpha(cutoff_x, dt)), self._y(y, _alpha(cutoff_y, dt))


class PointerFilter:
    """One Euro followed by a hysteretic grid snap."""

    def __init__(self, min_cutoff: float = 1.2, beta: float = 0.03, cell: float = 4.0):
        self.filter = OneEuro2D(min_cutoff=min_cutoff, beta=beta)
        self.cell = max(1.0, float(cell))
        self._out: Optional[Tuple[float, float]] = None
        self._last_t: Optional[float] = None

    def reset(self) -> None:
        self.filter.reset()
        self._out = None
        self._last_t = None

    def update(self, x: float, y: float, now: Optional[float] = None) -> Tuple[float, float]:
        dt = None
        if now is not None:
            dt = (now - self._last_t) if self._last_t is not None else None
            self._last_t = now
        fx, fy = self.filter.update(x, y, dt)

        if self._out is None:
            self._out = (round(fx / self.cell) * self.cell, round(fy / self.cell) * self.cell)
            return self._out

        ox, oy = self._out
        # hysteresis: stay in the current cell until clearly past its edge
        if abs(fx - ox) > self.cell * 0.6:
            ox = round(fx / self.cell) * self.cell
        if abs(fy - oy) > self.cell * 0.6:
            oy = round(fy / self.cell) * self.cell
        self._out = (ox, oy)
        return self._out
