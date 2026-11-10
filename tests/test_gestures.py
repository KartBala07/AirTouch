#!/usr/bin/env python3
"""Headless checks for the gesture engine and the pointer smoother."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from airtouch.core import Hand  # noqa: E402
from airtouch.gestures import GestureEngine  # noqa: E402
from airtouch.smooth import GridSmoother  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def hand(posture="point", tip=(0.5, 0.5), pinch=0.9):
    return Hand(
        tip_px=(tip[0] * 1280, tip[1] * 720),
        tip=tip,
        scale=0.2,
        pinch=pinch,
        index_extended=posture in ("point", "pinch"),
        points=np.zeros((21, 2), np.float32),
        posture=posture,
    )


def run(engine, posture, start, duration, step=1 / 30.0, tip=(0.5, 0.5), pinch=0.9):
    """Feed a posture for `duration` seconds; return (actions, end time)."""
    out = []
    t = start
    while t < start + duration:
        out.extend(engine.update(hand(posture, tip, pinch), t))
        t += step
    return out, t


def kinds(actions):
    return [a.kind for a in actions]


# ------------------------------------------------------------------ pinch click
eng = GestureEngine()
acts, t = run(eng, "pinch", 0.0, 0.20, pinch=0.12)
acts_rel, t = run(eng, "point", t, 0.20)
check("quick pinch is a click", kinds(acts + acts_rel) == ["click"],
      str(kinds(acts + acts_rel)))
acts2, t = run(eng, "pinch", t, 0.20, pinch=0.12)
acts_rel2, t = run(eng, "point", t, 0.20)
check("two quick pinches = select",
      kinds(acts2 + acts_rel2) == ["double_click"], str(kinds(acts2 + acts_rel2)))

# ------------------------------------------------------------------ right drag
eng = GestureEngine()
acts, t = run(eng, "pinch", 0.0, 0.6, pinch=0.12)
check("held pinch presses the right button", "right_down" in kinds(acts), str(kinds(acts)))
check("right-drag moves the pointer", eng.cursor_active)
acts, t = run(eng, "point", t, 0.2)
check("releasing the pinch releases the right button",
      kinds(acts)[-1:] == ["right_up"], str(kinds(acts)))
check("right button is up afterwards", eng.right_active is False)

# ------------------------------------------------------------------ fist / paste
eng = GestureEngine()
acts, t = run(eng, "fist", 0.0, 0.2)
check("short fist does nothing", "screenshot" not in kinds(acts), str(kinds(acts)))
acts, t = run(eng, "fist", t, 0.35)
check("held fist takes a screenshot", "screenshot" in kinds(acts), str(kinds(acts)))
acts, t = run(eng, "palm", t, 0.3)
check("opening the hand pastes", "paste" in kinds(acts), str(kinds(acts)))

eng2 = GestureEngine()
acts, t = run(eng2, "point", 0.0, 0.3)
acts, t = run(eng2, "palm", t, 0.3)
check("no paste without a screenshot", "paste" not in kinds(acts), str(kinds(acts)))

# ------------------------------------------------------------------ scroll
eng = GestureEngine()
acts, t = run(eng, "two_finger", 0.0, 0.2, tip=(0.5, 0.5))
for i in range(10):  # swipe upward (y decreasing)
    acts.extend(eng.update(hand("two_finger", (0.5, 0.5 - 0.02 * i)), t))
    t += 1 / 30.0
scrolls = [a.value for a in acts if a.kind == "scroll"]
check("upward two-finger swipe scrolls", len(scrolls) >= 1, f"{len(scrolls)} events")
check("swipe up = scroll down (negative wheel)", all(v < 0 for v in scrolls),
      f"{[round(v) for v in scrolls][:4]}")

eng = GestureEngine()
acts, t = run(eng, "two_finger", 0.0, 0.2, tip=(0.5, 0.3))
for i in range(10):
    acts.extend(eng.update(hand("two_finger", (0.5, 0.3 + 0.02 * i)), t))
    t += 1 / 30.0
down = [a.value for a in acts if a.kind == "scroll"]
check("swipe down = scroll up (positive wheel)", all(v > 0 for v in down),
      f"{[round(v) for v in down][:4]}")

# ------------------------------------------------------------------ swipes
eng = GestureEngine()
acts, t = run(eng, "palm", 0.0, 0.2, tip=(0.35, 0.5))
for i in range(8):
    acts.extend(eng.update(hand("palm", (0.35 + 0.03 * i, 0.5)), t))
    t += 1 / 30.0
switch = [a for a in acts if a.kind == "switch_window"]
check("palm swipe right switches window", len(switch) == 1 and switch[0].value > 0,
      str(kinds(acts)))
acts, t = run(eng, "point", t, 0.5)
acts, t = run(eng, "palm", t, 0.2, tip=(0.6, 0.55))
for i in range(8):
    acts.extend(eng.update(hand("palm", (0.6 - 0.03 * i, 0.55)), t))
    t += 1 / 30.0
switch = [a for a in acts if a.kind == "switch_window"]
check("palm swipe left switches back", len(switch) == 1 and switch[0].value < 0,
      str(kinds(acts)))

eng = GestureEngine()
acts, t = run(eng, "palm", 0.0, 0.2, tip=(0.5, 0.6))
for i in range(8):
    acts.extend(eng.update(hand("palm", (0.5, 0.6 - 0.03 * i)), t))
    t += 1 / 30.0
check("palm swipe up = mission control", "mission_control" in kinds(acts), str(kinds(acts)))

# ------------------------------------------------------------------ stability
eng = GestureEngine()
acts = eng.update(hand("pinch", pinch=0.1), 0.0)
acts += eng.update(hand("point"), 1 / 30)
acts += eng.update(hand("pinch", pinch=0.1), 2 / 30)
check("one-frame posture flicker is ignored", kinds(acts) == [], str(kinds(acts)))
check("scroll mode freezes the pointer", GestureEngine().cursor_active is False)

# ------------------------------------------------------------------ smoother
sm = GridSmoother(alpha=0.3, cell=8.0)
sm.update(100, 100)
rnd = np.random.RandomState(7)
raw = [(100 + rnd.normal(0, 6), 100 + rnd.normal(0, 6)) for _ in range(120)]
outs = [sm.update(*p) for p in raw]
xs = {round(o[0]) for o in outs}
spread_raw = max(p[0] for p in raw) - min(p[0] for p in raw)
spread_out = max(o[0] for o in outs) - min(o[0] for o in outs)
check("shake collapses to a few cells", len(xs) <= 4, f"{sorted(xs)}")
check("shake is damped hard", spread_out <= spread_raw / 2,
      f"raw {spread_raw:.0f}px -> {spread_out:.0f}px")
check("output is on the grid", all(abs(o[0] / 8 - round(o[0] / 8)) < 1e-9 for o in outs))

sm2 = GridSmoother(alpha=0.3, cell=8.0)
sm2.update(0, 0)
for i in range(60):
    x, y = sm2.update(600, 0)
check("large moves still track", x > 500, f"x={x}")
check("steps are whole cells", abs((x / 8) - round(x / 8)) < 1e-9, f"x={x}")

print()
if FAILS:
    print("FAILED:", ", ".join(FAILS))
    raise SystemExit(1)
print("all gesture checks passed")
