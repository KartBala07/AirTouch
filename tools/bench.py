#!/usr/bin/env python3
"""Measure where the frame budget goes at different capture sizes."""

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from airtouch.core import Camera, HandTracker, MotionGate  # noqa: E402

SECONDS = 8.0


def run(width, height):
    cam = Camera(width=width, height=height)
    if not cam.start():
        print(f"{width}x{height}: camera failed ({cam.error})")
        return
    tracker = HandTracker()
    gate = MotionGate()
    t_cam, t_gate, t_infer = [], [], []
    hands = 0
    start = time.monotonic()
    last_seq = -1
    while time.monotonic() - start < SECONDS:
        a = time.perf_counter()
        frame, ts, seq = cam.read()
        if frame is None or seq == last_seq:
            time.sleep(0.002)
            continue
        last_seq = seq
        b = time.perf_counter()
        gate.update(frame)
        c = time.perf_counter()
        hand = tracker.process(frame, ts)
        d = time.perf_counter()
        if hand is not None:
            hands += 1
        t_cam.append((b - a) * 1000)
        t_gate.append((c - b) * 1000)
        t_infer.append((d - c) * 1000)

    def p(v, q):
        return float(np.percentile(v, q)) if v else 0.0

    total = p(t_infer, 50) + p(t_gate, 50)
    print(
        f"{width}x{height}: frames {len(t_infer):3d}  "
        f"gate p50 {p(t_gate, 50):5.1f}ms  "
        f"infer p50 {p(t_infer, 50):5.1f} / p95 {p(t_infer, 95):5.1f}ms  "
        f"-> ~{1000.0 / max(total, 1e-6):4.1f} fps ceiling  hands {hands}"
    )
    cam.stop()
    tracker.close()


for w, h in ((640, 480), (960, 540), (1280, 720)):
    run(w, h)
