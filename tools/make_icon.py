#!/usr/bin/env python3
"""Generate the AirTouch app icon (rounded dark tile + cyan tracking ring)."""

import math
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
S = 1024
SS = 4  # supersampling factor


def rounded_mask(size, radius):
    m = np.zeros((size, size), np.uint8)
    r = int(radius)
    cv2.rectangle(m, (0, r), (size - 1, size - r), 255, -1)
    cv2.rectangle(m, (r, 0), (size - r, size - 1), 255, -1)
    for cx, cy in ((r, r), (size - r, r), (r, size - r), (size - r, size - r)):
        cv2.circle(m, (cx, cy), r, 255, -1)
    return m


def radial(size, cx, cy, radius, color, alpha=1.0):
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / max(radius, 1)
    a = np.clip(1.0 - d, 0.0, 1.0) ** 1.6 * alpha
    layer = np.zeros((size, size, 4), np.float32)
    layer[..., 0] = color[2]
    layer[..., 1] = color[1]
    layer[..., 2] = color[0]
    layer[..., 3] = a * 255
    return layer


def over(base, layer):
    a = layer[..., 3:4] / 255.0
    base[..., :3] = layer[..., :3] * a + base[..., :3] * (1 - a)
    base[..., 3:4] = np.maximum(base[..., 3:4], layer[..., 3:4])
    return base


def draw(size):
    canvas = np.zeros((size, size, 4), np.float32)

    # tile with vertical gradient
    mask = rounded_mask(size, size * 0.225)
    yy = np.linspace(0.0, 1.0, size, dtype=np.float32)[:, None]
    top = np.array([30, 20, 12], np.float32)      # BGR
    bot = np.array([58, 36, 18], np.float32)
    grad = (top * (1 - yy) + bot * yy)[..., None, :]
    grad = np.repeat(grad, size, axis=1)
    tile = np.zeros((size, size, 4), np.float32)
    tile[..., :3] = grad
    tile[..., 3] = mask
    canvas = over(canvas, tile)

    cx = cy = size / 2.0

    # soft glow behind the ring
    canvas = over(canvas, radial(size, cx, cy, size * 0.34, (255, 190, 60), 0.16))

    # outer tracking ring (cyan) with a gap at the lower right
    ring_r = size * 0.245
    thickness = int(size * 0.030)
    ring = np.zeros((size, size, 4), np.float32)
    cv2.ellipse(ring, (int(cx), int(cy)), (int(ring_r), int(ring_r)), 0, 18, 358,
                (255, 214, 60, 255), thickness, cv2.LINE_AA)
    canvas = over(canvas, ring)

    # inner white ring
    inner = np.zeros((size, size, 4), np.float32)
    cv2.circle(inner, (int(cx), int(cy)), int(size * 0.105), (255, 255, 255, 240),
               int(size * 0.021), cv2.LINE_AA)
    canvas = over(canvas, inner)

    # fingertip dot
    canvas = over(canvas, radial(size, cx, cy, size * 0.055, (255, 255, 255), 1.0))

    # three motion arcs to the left
    for i, r in enumerate((0.335, 0.395, 0.455)):
        arc = np.zeros((size, size, 4), np.float32)
        cv2.ellipse(arc, (int(cx), int(cy)), (int(size * r), int(size * r)), 0,
                    150, 205, (255, 200, 80, int(150 - i * 35)),
                    max(3, int(size * 0.016)), cv2.LINE_AA)
        canvas = over(canvas, arc)

    return np.clip(canvas, 0, 255).astype(np.uint8)


def main() -> int:
    big = draw(S * SS)
    icon = cv2.resize(big, (S, S), interpolation=cv2.INTER_AREA)
    png = ROOT / "icon_1024.png"
    cv2.imwrite(str(png), icon)

    iconset = ROOT / "build" / "AirTouch.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 64, 128, 256, 512):
        for scale in (1, 2):
            px = size * scale
            name = f"icon_{size}x{size}" + ("@2x" if scale == 2 else "") + ".png"
            subprocess.run(
                ["sips", "-z", str(px), str(px), str(png), "--out", str(iconset / name)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(ROOT / "icon.icns")],
        check=True,
    )
    print("wrote", png, "and", ROOT / "icon.icns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
