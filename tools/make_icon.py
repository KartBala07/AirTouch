#!/usr/bin/env python3
"""Render the AirTouch app icon.

A squircle tile with a graphite gradient and a precision reticle, drawn at 4x
and downsampled so the curves stay clean at every size.
"""

import subprocess
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SS = 4
S = 1024
N = 4.6  # superellipse exponent: Apple-ish squircle


def squircle_mask(size, inset=0.17):
    """Apple-style icon grid: the tile fills ~82% of the canvas."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    c = (size - 1) / 2.0
    half = c * (1.0 - inset)
    u = np.abs((xx - c) / half)
    v = np.abs((yy - c) / half)
    d = np.power(u, N) + np.power(v, N)
    return np.clip((1.0 - d) * 24.0, 0.0, 1.0) * 255.0


def alpha_over(dst, src_rgb, src_a):
    a = (src_a / 255.0)[..., None]
    dst[..., :3] = dst[..., :3] * (1 - a) + src_rgb * a
    dst[..., 3] = np.maximum(dst[..., 3], src_a)
    return dst


def draw(size, small=False):
    canvas = np.zeros((size, size, 4), np.float32)
    cx = cy = (size - 1) / 2.0
    mask = squircle_mask(size)

    # tile: diagonal gradient + top-left sheen
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    t = np.clip((xx / size) * 0.55 + (yy / size) * 0.45, 0.0, 1.0)[..., None]
    top = np.array([40, 30, 21], np.float32)     # BGR: graphite with a blue cast
    bot = np.array([13, 10, 8], np.float32)
    tile = (top * (1 - t) + bot * t) * np.ones((1, 1, 3), np.float32)

    sheen = np.clip(
        1.0 - ((xx - size * 0.28) ** 2 + (yy - size * 0.10) ** 2) / (size * 0.72) ** 2,
        0.0, 1.0,
    ) ** 2
    tile = tile + sheen[..., None] * np.array([30, 25, 20], np.float32)

    # rim light: bright along the top edge, fading down the sides
    kernel = np.ones((max(3, int(size * 0.007)),) * 2, np.uint8)
    edge = cv2.erode((mask > 127).astype(np.uint8) * 255, kernel, iterations=1)
    inner = np.clip(mask - edge, 0, 255) / 255.0
    rim = (0.95 - 0.55 * (yy / size))[..., None] * np.array([120, 108, 94], np.float32)
    tile = tile + inner[..., None] * rim

    # inner vignette: darker towards the bottom corners
    vig = np.clip(1.0 - (((xx - cx) ** 2 + (yy - cy) ** 2) / (size * 0.62) ** 2), 0, 1) ** 2
    tile = tile * (0.86 + 0.14 * vig[..., None])

    canvas = alpha_over(canvas, np.clip(tile, 0, 255), mask)

    # soft cyan glow behind the mark
    if not small:
        gy, gx = np.mgrid[0:size, 0:size].astype(np.float32)
        glow = np.clip(1.0 - np.sqrt((gx - cx) ** 2 + (gy - cy) ** 2) / (size * 0.34), 0, 1) ** 2.2
        canvas = alpha_over(canvas, np.array([255, 205, 70], np.float32), glow * 42)

    # reticle: four corner brackets
    r = size * (0.245 if small else 0.255)
    arm = size * (0.110 if small else 0.092)
    w = max(2, int(size * (0.036 if small else 0.030)))
    ink = (248, 250, 253, 255)
    layer = np.zeros((size, size, 4), np.float32)
    for sx in (-1, 1):
        for sy in (-1, 1):
            x0, y0 = cx + sx * r, cy + sy * r
            cv2.line(layer, (int(x0), int(y0)), (int(x0 - sx * arm), int(y0)),
                     ink, w, cv2.LINE_AA)
            cv2.line(layer, (int(x0), int(y0)), (int(x0), int(y0 - sy * arm)),
                     ink, w, cv2.LINE_AA)
            rr = max(1, w // 2)
            cv2.circle(layer, (int(x0 - sx * arm), int(y0)), rr, ink, -1, cv2.LINE_AA)
            cv2.circle(layer, (int(x0), int(y0 - sy * arm)), rr, ink, -1, cv2.LINE_AA)
    canvas = alpha_over(canvas, layer[..., :3], layer[..., 3])

    # centre dot: the fingertip
    dot = np.zeros((size, size, 4), np.float32)
    cv2.circle(dot, (int(cx), int(cy)), int(size * (0.075 if small else 0.056)),
               (255, 226, 92, 255), -1, cv2.LINE_AA)
    canvas = alpha_over(canvas, dot[..., :3], dot[..., 3])

    core = np.zeros((size, size, 4), np.float32)
    cv2.circle(core, (int(cx), int(cy)), int(size * (0.034 if small else 0.026)),
               (255, 255, 255, 255), -1, cv2.LINE_AA)
    canvas = alpha_over(canvas, core[..., :3], core[..., 3])

    return np.clip(canvas, 0, 255).astype(np.uint8)


def render(px: int, small: bool = False):
    big = draw(px * SS, small=small)
    return cv2.resize(big, (px, px), interpolation=cv2.INTER_AREA)


def main() -> int:
    png = ROOT / "icon_1024.png"
    cv2.imwrite(str(png), render(S))

    iconset = ROOT / "build" / "AirTouch.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 64, 128, 256, 512):
        for scale in (1, 2):
            px = size * scale
            name = f"icon_{size}x{size}" + ("@2x" if scale == 2 else "") + ".png"
            # small sizes get a chunkier mark so they survive downscaling
            cv2.imwrite(str(iconset / name), render(px, small=px <= 64))
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(ROOT / "icon.icns")],
        check=True,
    )
    print("wrote", png, "and", ROOT / "icon.icns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
