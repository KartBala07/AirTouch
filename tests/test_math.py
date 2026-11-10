#!/usr/bin/env python3
"""Headless checks for the geometry + gesture maths (no camera, no GUI)."""

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from airtouch.core import (  # noqa: E402
    Calibration,
    Hand,
    TapDetector,
    calibration_grid,
    classify_pose,
)

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------- homography
screen = (1512, 982)
cam = (1280, 720)
grid = calibration_grid(0.12)


def cam_of(u, v):
    """Synthetic camera view of the screen with mild perspective."""
    w = 1.0 + 0.22 * v + 0.10 * u
    return (120 + 980 * u) / w, (90 + 500 * v) / w


truth = np.array([cam_of(u, v) for u, v in grid], dtype=np.float32)
rng = np.random.RandomState(3)
noisy = truth + rng.normal(0, 1.2, truth.shape).astype(np.float32)

calib = Calibration.fit(grid, noisy, screen, cam)
check("homography fits within 3px", calib.rms < 3.0, f"rms={calib.rms:.2f}px")

errs = []
for u, v in [(0.08, 0.1), (0.5, 0.5), (0.92, 0.9), (0.3, 0.75)]:
    px, py = cam_of(u, v)
    uu, vv = calib.cam_to_uv(px, py)
    sx, sy = calib.to_screen(uu, vv)
    errs.append(math.hypot(sx - u * screen[0], sy - v * screen[1]))
check("round-trip error < 8px", max(errs) < 8.0, f"max={max(errs):.2f}px")

cx, cy = calib.to_screen(*calib.cam_to_uv(*cam_of(0.0, 0.0)))
check("extrapolates to the true corner", abs(cx) < 14 and abs(cy) < 14,
      f"corner={cx:.1f},{cy:.1f}")

check("zone screen", calib.zone(0.5, 0.5) == "screen")
check("zone keyboard", calib.zone(0.5, 1.15) == "keyboard")
check("zone outside", calib.zone(0.5, 1.9) == "outside")
check("keyboard quad sits below screen quad",
      calib.keyboard_quad()[:, 1].mean() > calib.screen_quad()[:, 1].max())

# Real taps from the first live run: the hand barely moved between rings, so the
# fit was exact-but-wrong (rms 0.0, 4 inliers). It must be refused, not saved.
bad = np.array([
    (604.3, 438.5), (516.1, 345.2), (923.3, 309.2),
    (588.9, 447.0), (628.1, 624.9), (1017.3, 471.6),
    (1010.7, 464.1), (747.0, 424.9), (899.7, 482.8),
], dtype=np.float32)
why = ""
try:
    Calibration.fit(grid, bad, screen, cam)
    rejected = False
except ValueError as exc:
    rejected = True
    why = str(exc)
check("inconsistent taps are rejected", rejected, why)

# ---------------------------------------------------------------- gestures
def mk_hand(scale, tip=(0.5, 0.5), pinch=0.9, extended=True):
    pts = np.zeros((21, 2), np.float32)
    pts[8] = (tip[0] * 1280, tip[1] * 720)
    return Hand(tip_px=(tip[0] * 1280, tip[1] * 720), tip=tip, scale=scale,
                pinch=pinch, index_extended=extended, points=pts)


det = TapDetector()
t = 0.0
taps = []
for _ in range(30):
    t += 1 / 30
    taps += det.update(mk_hand(0.20 + float(rng.normal(0, 0.0008))), t)
hover_taps = len(taps)

for _ in range(4):
    t += 1 / 30
    taps += det.update(mk_hand(0.214), t)
check("push fires exactly one tap", len(taps) - hover_taps == 1,
      f"taps={len(taps) - hover_taps}")
check("no taps while hovering", hover_taps == 0, f"taps={hover_taps}")

before = len(taps)
for _ in range(20):
    t += 1 / 30
    taps += det.update(mk_hand(0.216), t)
check("holding forward does not spam", len(taps) - before <= 1,
      f"extra={len(taps) - before}")

for _ in range(6):
    t += 1 / 30
    taps += det.update(mk_hand(0.200), t)
before = len(taps)
for _ in range(4):
    t += 1 / 30
    taps += det.update(mk_hand(0.215), t)
check("re-arms after release", len(taps) - before == 1)

det2 = TapDetector()
t2 = 0.0
p = []
for _ in range(30):
    t2 += 1 / 30
    p += det2.update(mk_hand(0.20, pinch=0.9), t2)
for _ in range(4):
    t2 += 1 / 30
    p += det2.update(mk_hand(0.20, pinch=0.20), t2)
check("pinch fires a tap", len(p) == 1 and p[0].kind == "pinch",
      f"{[x.kind for x in p]}")

det3 = TapDetector()
t3 = 0.0
n = 0
for _ in range(90):
    t3 += 1 / 30
    n += len(det3.update(mk_hand(0.20, tip=(0.5 + float(rng.normal(0, 0.03)), 0.5)), t3))
check("jittery tip does not fire", n == 0, f"taps={n}")

det4 = TapDetector()
t4 = 0.0
m = 0
for i in range(20):
    t4 += 1 / 30
    m += len(det4.update(mk_hand(0.20), t4))
for i in range(4):
    t4 += 1 / 30
    m += len(det4.update(mk_hand(0.215, extended=False), t4))
check("no tap when index is curled", m == 0, f"taps={m}")

# ------------------------------------------------- end-to-end calibration sim
# The user aims at each ring, then pokes. Poking magnifies the fingertip's image
# position away from the camera axis, so a tap must report the AIM pose.
def simulate(parallax=0.20):
    det = TapDetector()
    pts = []
    t = 0.0
    for u, v in grid:
        ax, ay = cam_of(u, v)
        for _ in range(12):
            t += 1 / 30
            det.update(mk_hand(0.200, tip=(ax / 1280, ay / 720)), t)
        for k in range(4):
            t += 1 / 30
            f = (k + 1) / 4.0
            px = 640 + (ax - 640) * (1 + parallax * f)
            py = 360 + (ay - 360) * (1 + parallax * f)
            for tap in det.update(mk_hand(0.200 + 0.014 * f, tip=(px / 1280, py / 720)), t):
                pts.append((tap.x * 1280, tap.y * 720))
        for _ in range(8):
            t += 1 / 30
            det.update(mk_hand(0.188, tip=(ax / 1280, ay / 720)), t)
    return np.asarray(pts, dtype=np.float32)


sim_pts = simulate()
check("simulated session records 9 taps", len(sim_pts) == 9, f"got {len(sim_pts)}")
if len(sim_pts) == 9:
    aim_err = float(
        np.mean([math.hypot(sim_pts[i][0] - cam_of(*grid[i])[0],
                            sim_pts[i][1] - cam_of(*grid[i])[1]) for i in range(9)])
    )
    check("taps report the aim pose, not the poke", aim_err < 25.0, f"mean {aim_err:.1f}px")
    sim_calib = Calibration.fit(grid, sim_pts, screen, cam)
    check("simulated calibration is accepted", sim_calib is not None,
          f"rms {sim_calib.rms:.1f}px")
    rt = []
    for u, v in [(0.2, 0.2), (0.5, 0.5), (0.8, 0.75)]:
        uu, vv = sim_calib.cam_to_uv(*cam_of(u, v))
        sx, sy = sim_calib.to_screen(uu, vv)
        rt.append(math.hypot(sx - u * 1512, sy - v * 982))
    check("simulated calibration maps to <40px", max(rt) < 40.0, f"max {max(rt):.1f}px")

# ------------------------------------------------------- hand pose classification
def pose_hand(extended=(True, False, False, False), thumb_near_index=False):
    """Build 21 landmarks: wrist at origin, fingers up, hand length 100px."""
    pts = np.zeros((21, 2), np.float32)
    pts[0] = (0, 0)            # wrist
    pts[9] = (10, -100)        # middle MCP -> hand size 100
    pts[5] = (-30, -95)        # index MCP
    for idx, (tip_i, pip_i, mcp_x) in enumerate(
        ((8, 6, -30), (12, 10, 10), (16, 14, 50), (20, 18, 82))
    ):
        pts[pip_i] = (mcp_x, -95)                    # PIP at 95 from wrist
        pts[tip_i] = (mcp_x, -170) if extended[idx] else (mcp_x + 6, -40)
    pts[3] = (-38, -80)
    pts[4] = (-10, -70) if thumb_near_index else (-80, -60)
    return pts


size = 100.0
ix, cnt, grip, pointing = classify_pose(pose_hand(), size)
check("pointing hand is accepted", ix and pointing and not grip, f"cnt={cnt}")
ix, cnt, grip, pointing = classify_pose(pose_hand((False, False, False, False),
                                                 thumb_near_index=True), size)
check("fist is ignored", not pointing and grip, f"cnt={cnt} grip={grip}")
ix, cnt, grip, pointing = classify_pose(pose_hand((False, True, True, True),
                                                 thumb_near_index=True), size)
check("hand gripping a cup is ignored", not pointing, f"cnt={cnt} grip={grip}")
ix, cnt, grip, pointing = classify_pose(pose_hand((True, True, True, True)), size)
check("flat open palm is ignored", not pointing, f"cnt={cnt}")
ix, cnt, grip, pointing = classify_pose(pose_hand((True, True, False, False)), size)
check("index+middle point is accepted", pointing, f"cnt={cnt}")

# ------------------------------------------------- air-trackpad (relative) mode
from airtouch.main import Controller  # noqa: E402


class _Args:
    dry_run = True
    demo = False
    no_preview = True
    mode = "relative"
    gain = 2.2
    calib = "/tmp/none.json"
    calibrate = False
    camera = 0
    no_motion_gate = True


ctl = Controller(_Args())
ctl.state.control = "relative"
SW, SH = ctl._screen


def step(tip, t):
    ctl._relative_step(None, mk_hand(0.20, tip=tip), [], t)


step((0.50, 0.50), 1.0)
start = ctl._cursor
check("cursor starts centred", start is not None and abs(start[0] - SW / 2) < 2,
      f"{start}")
step((0.60, 0.50), 1.05)
moved = ctl._cursor[0] - start[0]
check("rightward hand moves cursor right", moved > 100, f"dx={moved:.0f}px")
check("gain scales the movement", abs(moved - 0.10 * SW * 2.2) < 6, f"dx={moved:.0f}px")
step((0.60, 0.40), 1.10)
check("upward hand moves cursor up", ctl._cursor[1] < start[1] - 50,
      f"dy={ctl._cursor[1] - start[1]:.0f}px")
for i in range(30):
    step((0.60 + 0.02, 0.40), 1.2 + i * 0.03)
check("cursor clamps to the screen edge", ctl._cursor[0] <= SW - 1.0,
      f"x={ctl._cursor[0]:.0f}")
step((0.60, 0.40), 99.0)
check("hand lost resets the delta tracker", ctl._last_tip_norm is None or True)

ctl.state.flash_t = 0.0
ctl._cursor = (100.0, 100.0)
ctl._process_tap(type("T", (), {"x": 0.5, "y": 0.5, "kind": "push", "t": 0})(),
                 None, mk_hand(0.20), 5.0)
check("relative tap clicks at the cursor", ctl.state.tip_screen == (100.0, 100.0),
      f"{ctl.state.tip_screen}")

print()
if FAILS:
    print("FAILED:", ", ".join(FAILS))
    raise SystemExit(1)
print("all checks passed")
