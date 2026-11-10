#!/usr/bin/env python3
"""Build the app icon from the hand artwork.

Takes the black-on-white mark, turns its darkness into alpha, and composites it
onto a macOS squircle. Two styles: dark tile with a white mark (matches the app
UI) and light tile with the original black mark.
"""

import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SS = 4
S = 1024
N = 4.6


def squircle_mask(size, inset=0.17):
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


def load_mark(path):
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    alpha = np.clip((255.0 - gray) / 255.0, 0.0, 1.0) ** 0.85   # black = solid
    ys, xs = np.where(alpha > 0.35)
    if len(xs) == 0:
        raise SystemExit("artwork appears blank")
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    return alpha[y0:y1 + 1, x0:x1 + 1]


def tile(size, light=False):
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    t = np.clip((xx / size) * 0.55 + (yy / size) * 0.45, 0.0, 1.0)[..., None]
    if light:
        top = np.array([250, 248, 245], np.float32)
        bot = np.array([226, 222, 216], np.float32)
    else:
        top = np.array([40, 30, 21], np.float32)
        bot = np.array([13, 10, 8], np.float32)
    return (top * (1 - t) + bot * t) * np.ones((1, 1, 3), np.float32)


def build(mark, size, light=False, mark_scale=0.62):
    canvas = np.zeros((size, size, 4), np.float32)
    mask = squircle_mask(size)

    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    body = tile(size, light)

    kernel = np.ones((max(3, int(size * 0.007)),) * 2, np.uint8)
    edge = cv2.erode((mask > 127).astype(np.uint8) * 255, kernel, iterations=1)
    rim_strength = (0.55 - 0.35 * (yy / size))[..., None]
    rim_col = np.array([120, 108, 94], np.float32) if not light else np.array([255, 255, 255], np.float32)
    body = body + (np.clip(mask - edge, 0, 255) / 255.0)[..., None] * rim_strength * rim_col

    canvas = alpha_over(canvas, np.clip(body, 0, 255), mask)

    # place the mark
    h, w = mark.shape
    target = int(size * mark_scale)
    scale = target / max(h, w)
    new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
    m = cv2.resize(mark, (new_w, new_h), interpolation=cv2.INTER_AREA)
    ox, oy = (size - new_w) // 2, (size - new_h) // 2

    # soft shadow under the mark for depth
    shadow = np.zeros((size, size), np.float32)
    shadow[oy + int(size * 0.012):oy + int(size * 0.012) + new_h, ox:ox + new_w] = m
    shadow = cv2.GaussianBlur(shadow, (0, 0), sigmaX=size * 0.012) * (0.35 if light else 0.5)
    canvas = alpha_over(canvas, np.zeros_like(shadow)[..., None].repeat(3, 2),
                        shadow * 255)

    ink = np.zeros((size, size, 3), np.float32)
    ink[oy:oy + new_h, ox:ox + new_w] = np.array([248, 250, 252] if not light else [12, 12, 14], np.float32)
    alpha = np.zeros((size, size), np.float32)
    alpha[oy:oy + new_h, ox:ox + new_w] = m
    canvas = alpha_over(canvas, ink, np.clip(alpha, 0, 1) * 255)
    return np.clip(canvas, 0, 255).astype(np.uint8)


def render(mark, px, light):
    return cv2.resize(build(mark, px * SS, light), (px, px), interpolation=cv2.INTER_AREA)


def write_icns(light, art):
    iconset = ROOT / "build" / "AirTouch.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 64, 128, 256, 512):
        for scale in (1, 2):
            px = size * scale
            name = f"icon_{size}x{size}" + ("@2x" if scale == 2 else "") + ".png"
            cv2.imwrite(str(iconset / name), render(art, px, light))
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ROOT / "icon.icns")], check=True)


if __name__ == "__main__":
    art_path = Path(sys.argv[1])
    art = load_mark(art_path)
    cv2.imwrite(str(ROOT / "icon_dark_preview.png"), build(art, S, light=False))
    cv2.imwrite(str(ROOT / "icon_light_preview.png"), build(art, S, light=True))
    style = sys.argv[2] if len(sys.argv) > 2 else "dark"
    if style in ("dark", "light"):
        write_icns(style == "light", art)
        print(f"wrote icon.icns ({style})")
    print("previews: icon_dark_preview.png, icon_light_preview.png")
