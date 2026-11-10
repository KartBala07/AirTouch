"""Grid-quantised smoothing for the pointer.

Two stages: an exponential filter kills the high-frequency shake, then the
result snaps to a coarse cell so the cursor moves in whole steps instead of
vibrating one pixel at a time. A hysteresis band stops it flickering between
two neighbouring cells when the hand sits still.
"""

from __future__ import annotations

from typing import Optional, Tuple


class GridSmoother:
    def __init__(self, alpha: float = 0.30, cell: float = 6.0):
        self.alpha = max(0.05, min(1.0, float(alpha)))
        self.cell = max(1.0, float(cell))
        self._pos: Optional[Tuple[float, float]] = None
        self._out: Optional[Tuple[float, float]] = None

    def reset(self) -> None:
        self._pos = None
        self._out = None

    def update(self, x: float, y: float) -> Tuple[float, float]:
        cell = self.cell
        if self._pos is None:
            self._pos = (float(x), float(y))
            self._out = (round(x / cell) * cell, round(y / cell) * cell)
            return self._out

        a = self.alpha
        px, py = self._pos
        px += (x - px) * a
        py += (y - py) * a
        self._pos = (px, py)

        ox, oy = self._out  # type: ignore[misc]
        # only step to the next cell once the filtered point is clearly past it
        if abs(px - ox) > cell * 0.75:
            ox = round(px / cell) * cell
        if abs(py - oy) > cell * 0.75:
            oy = round(py / cell) * cell
        self._out = (ox, oy)
        return self._out
