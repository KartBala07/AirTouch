#!/usr/bin/env python3
"""Capture a few seconds from the camera, report hand detection, save a frame."""

import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from airtouch.core import Camera, HandTracker, build_preview  # noqa: E402

out = Path(__file__).resolve().parent.parent / "shots"
out.mkdir(exist_ok=True)

cam = Camera()
if not cam.start():
    print("camera failed:", cam.error)
    raise SystemExit(2)

tracker = HandTracker()
frames = 0
hands = 0
best = None
preview_rgb = None
t0 = time.time()
while time.time() - t0 < 4.0:
    frame, ts, seq = cam.read()
    if frame is None:
        time.sleep(0.02)
        continue
    frames += 1
    hand = tracker.process(frame, ts)
    if hand is not None:
        hands += 1
        if best is None or hand.score > best.score:
            best = hand
            preview_rgb = build_preview(frame, hand, None, 640)
    time.sleep(1 / 30)

frame, ts, seq = cam.read()
cv2.imwrite(str(out / "camera_raw.jpg"), frame)
if preview_rgb is None:
    preview_rgb = build_preview(frame, None, None, 640)
cv2.imwrite(str(out / "camera_preview.png"), cv2.cvtColor(preview_rgb, cv2.COLOR_RGB2BGR))

print(f"frames processed : {frames}")
print(f"hand detected    : {hands}  ({100.0 * hands / max(frames,1):.0f}%)")
if best is not None:
    print(f"best score       : {best.score:.2f}")
    print(f"index tip (norm) : {best.tip[0]:.3f}, {best.tip[1]:.3f}")
    print(f"hand scale       : {best.scale:.4f}")
    print(f"pinch ratio      : {best.pinch:.2f}")
    print(f"index extended   : {best.index_extended}")
cam.stop()
tracker.close()
