"""AirTouch core: camera capture, hand tracking, gestures, calibration, clicking.

No AppKit imports live here so this module can be exercised headlessly.
"""

from __future__ import annotations

import ctypes
import json
import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

from . import paths

ROOT = paths.resource_dir()
MODEL_PATH = paths.model_path()
CALIB_PATH = paths.calib_path()

# MediaPipe hand landmark indices we care about.
WRIST = 0
THUMB_IP, THUMB_TIP = 3, 4
INDEX_MCP, INDEX_PIP, INDEX_TIP = 5, 6, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP = 9, 10, 12
RING_TIP, PINKY_TIP = 16, 20

HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
]


# --------------------------------------------------------------------------
# camera
# --------------------------------------------------------------------------
class Camera:
    """Threaded capture. Frames are mirrored so the view matches the user's
    own left/right (moving your finger right moves the cursor right)."""

    def __init__(self, index: int = 0, width: int = 1280, height: int = 720):
        self.index = index
        self.width = width
        self.height = height
        self.cap = None
        self.frame = None
        self.ts = 0.0
        self.seq = 0
        self.error: Optional[str] = None
        self.fps = 0.0
        self._lock = threading.Lock()
        self._run = False
        self._stamps: deque = deque(maxlen=30)

    # ------------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return bool(self._run and self.cap is not None)

    def start(self, timeout: float = 4.0) -> bool:
        if self.is_running:
            return True
        for backend in (cv2.CAP_AVFOUNDATION, cv2.CAP_ANY):
            cap = cv2.VideoCapture(self.index, backend)
            if cap.isOpened():
                self.cap = cap
                break
            cap.release()
        if self.cap is None:
            self.error = "camera_unavailable"
            return False
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._run = True
        threading.Thread(target=self._loop, name="camera", daemon=True).start()
        deadline = time.time() + timeout
        while self.frame is None and time.time() < deadline:
            time.sleep(0.02)
        if self.frame is None:
            self.error = "no_frames"
            return False
        self.error = None
        return True

    def _loop(self) -> None:
        while self._run:
            try:
                ok, frame = self.cap.read()
            except Exception:
                ok, frame = False, None
            if not ok or frame is None:
                time.sleep(0.01)
                continue
            frame = cv2.flip(frame, 1)
            now = time.monotonic()
            with self._lock:
                self.frame = frame
                self.ts = now
                self.seq += 1
                self._stamps.append(now)
                if len(self._stamps) >= 8:
                    span = self._stamps[-1] - self._stamps[0]
                    if span > 0:
                        self.fps = min(120.0, (len(self._stamps) - 1) / span)

    # ------------------------------------------------------------------
    def read(self):
        with self._lock:
            return self.frame, self.ts, self.seq

    @property
    def size(self) -> Tuple[int, int]:
        with self._lock:
            f = self.frame
        if f is None:
            return (0, 0)
        return (f.shape[1], f.shape[0])

    def stop(self) -> None:
        self._run = False
        time.sleep(0.05)
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None
        with self._lock:
            self.frame = None
            self.seq = 0


# --------------------------------------------------------------------------
# hand tracking
# --------------------------------------------------------------------------
@dataclass
class Hand:
    tip_px: Tuple[float, float]
    tip: Tuple[float, float]          # normalized (0..1) in the mirrored frame
    scale: float                      # wrist->middle_MCP length / frame height
    pinch: float                      # thumb-tip to index-tip distance / hand size
    index_extended: bool
    points: np.ndarray                # (21, 2) pixel coordinates
    score: float = 0.0
    extended: int = 0                 # how many of index/middle/ring/pinky are out
    grip: bool = False                # thumb folded across the palm (holding something)
    pointing: bool = True             # index out, not a grip, not a flat palm
    wrist: Tuple[float, float] = (0.5, 0.75)   # normalized, for zone gating
    fingers: Tuple[bool, bool, bool, bool] = (True, False, False, False)
    posture: str = "point"            # point | pinch | fist | palm | two_finger | none


def finger_states(pts: np.ndarray) -> Tuple[bool, bool, bool, bool]:
    """Which of index / middle / ring / pinky are extended."""
    return tuple(  # type: ignore[return-value]
        bool(np.linalg.norm(pts[t] - pts[WRIST])
             > np.linalg.norm(pts[pp] - pts[WRIST]) * 1.05)
        for t, pp in ((INDEX_TIP, INDEX_PIP), (MIDDLE_TIP, MIDDLE_PIP),
                      (RING_TIP, 14), (PINKY_TIP, 18))
    )


def classify_posture(ext: Tuple[bool, bool, bool, bool], pinch: float,
                     grip: bool) -> str:
    """Map finger state to one of: pinch, fist, palm, two_finger, point, none.

    A pinch is the thumb tip touching the index tip while the thumb is *not*
    folded across the palm - that is what separates it from a fist, whose thumb
    lies over the curled fingers (grip) and whose fingers are all curled.
    """
    count = int(sum(ext))
    if pinch <= 0.25 and not grip:
        return "pinch"
    if count == 0 and grip:
        return "fist"
    if count == 0:
        return "none"
    if count == 4:
        return "palm"
    if ext[0] and ext[1] and not ext[2] and not ext[3]:
        return "two_finger"
    if ext[0] and count <= 2:
        return "point"
    return "none"


def classify_pose(pts: np.ndarray, size: float):
    """Return (index_extended, extended_count, grip, pointing) plus details."""
    ext = finger_states(pts)
    grip = bool(np.linalg.norm(pts[THUMB_TIP] - pts[INDEX_MCP]) / max(size, 1e-6) < 0.55)
    pointing = bool(ext[0] and not grip and not (ext[1] and ext[2] and ext[3]))
    return ext[0], int(sum(ext)), grip, pointing


class HandTracker:
    def __init__(self, model_path: Optional[Path] = None, num_hands: int = 1):
        path = Path(model_path or MODEL_PATH)
        if not path.exists():
            raise FileNotFoundError(f"hand model missing: {path}")
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python.vision import (
            HandLandmarker,
            HandLandmarkerOptions,
            RunningMode,
        )

        self._mp = __import__("mediapipe")
        opts = HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(path)),
            running_mode=RunningMode.VIDEO,
            num_hands=num_hands,
            min_hand_detection_confidence=0.65,
            min_hand_presence_confidence=0.65,
            min_tracking_confidence=0.60,
        )
        self._lm = HandLandmarker.create_from_options(opts)
        self._last_ts = 0.0

    def process(self, bgr: np.ndarray, ts: float) -> Optional[Hand]:
        if ts <= self._last_ts:
            ts = self._last_ts + 0.001
        self._last_ts = ts
        rgb = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._lm.detect_for_video(img, int(ts * 1000))
        if not result.hand_landmarks:
            return None
        lms = result.hand_landmarks[0]
        h, w = bgr.shape[:2]
        pts = np.array([[lm.x * w, lm.y * h] for lm in lms], dtype=np.float32)
        size = float(np.linalg.norm(pts[WRIST] - pts[MIDDLE_MCP])) or 1.0
        scale = size / h
        index_extended, extended, grip, pointing = classify_pose(pts, size)
        fingers = finger_states(pts)
        pinch = float(np.linalg.norm(pts[THUMB_TIP] - pts[INDEX_TIP]) / size)
        tip = pts[INDEX_TIP]
        score = 0.0
        try:
            if result.handedness:
                score = float(result.handedness[0][0].score)
        except Exception:
            pass
        return Hand(
            tip_px=(float(tip[0]), float(tip[1])),
            tip=(float(tip[0] / w), float(tip[1] / h)),
            scale=float(scale),
            pinch=pinch,
            index_extended=index_extended,
            points=pts,
            score=score,
            extended=extended,
            grip=grip,
            pointing=pointing,
            wrist=(float(pts[WRIST][0] / w), float(pts[WRIST][1] / h)),
            fingers=fingers,
            posture=classify_posture(fingers, pinch, grip),
        )

    def close(self) -> None:
        try:
            self._lm.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# gestures
# --------------------------------------------------------------------------
@dataclass
class Tap:
    x: float          # normalized camera x
    y: float          # normalized camera y
    t: float
    kind: str         # 'push' | 'pinch'


class TapDetector:
    """Two independent tap triggers, because one alone is flaky in beta:

    * push: hand scale (wrist->middle-MCP) grows ~5% => finger moved toward
      the camera, i.e. you poked the screen.
    * pinch: thumb and index come together (very reliable in MediaPipe).
    """

    PUSH_ON = 1.050
    PUSH_OFF = 1.015
    PINCH_ON = 0.34
    PINCH_OFF = 0.58
    REFRACTORY = 0.20
    STABLE_STD = 0.014

    def __init__(self) -> None:
        self.baseline: Optional[float] = None
        self.pushed = False
        self.pinching = False
        self.last_tap_t = 0.0
        self.hist: deque = deque(maxlen=24)

    def reset(self) -> None:
        self.baseline = None
        self.pushed = False
        self.pinching = False
        self.hist.clear()

    PRE_POKE = 0.10  # samples newer than this are the poke itself

    def _aim_window(self, t: float):
        return [(x, y) for tt, x, y in self.hist if t - 0.70 <= tt <= t - self.PRE_POKE]

    def _stable(self, t: float) -> bool:
        """Steadiness of the *aim* pose: the poke moves the fingertip on purpose,
        so judging stability on the newest frames would veto every tap."""
        window = self._aim_window(t)[-6:]
        if len(window) < 3:
            return False
        a = np.asarray(window, dtype=np.float32)
        return bool(a[:, 0].std() < self.STABLE_STD and a[:, 1].std() < self.STABLE_STD)

    def _aim_point(self, t: float, fallback: Tuple[float, float]) -> Tuple[float, float]:
        """Where the finger was pointed *before* the poke started.

        A poke throws the fingertip toward the camera, which shifts its image
        position by more than the aiming does. Recording the pre-poke pose is
        what makes a 9-point calibration actually line up with the rings.
        """
        window = self._aim_window(t)[-6:]
        if len(window) < 2:
            return fallback
        arr = np.asarray(window, dtype=np.float32)
        return float(np.median(arr[:, 0])), float(np.median(arr[:, 1]))

    def update(self, hand: Hand, t: float) -> List[Tap]:
        taps: List[Tap] = []
        self.hist.append((t, hand.tip[0], hand.tip[1]))
        if self.baseline is None:
            self.baseline = hand.scale
        ratio = hand.scale / max(self.baseline, 1e-6)

        if not self.pushed:
            # Learn the resting hand size, but never from a half-pushed hand:
            # that creep used to swallow the next poke (taps stopped firing).
            if ratio <= 1.03:
                self.baseline = 0.985 * self.baseline + 0.015 * hand.scale
        elif t - self.last_tap_t > 1.2:
            # held forward for over a second = new posture, re-learn it
            self.baseline = 0.99 * self.baseline + 0.01 * hand.scale

        if (
            not self.pushed
            and ratio >= self.PUSH_ON
            and hand.index_extended
            and self._stable(t)
            and (t - self.last_tap_t) > self.REFRACTORY
        ):
            ax, ay = self._aim_point(t, hand.tip)
            taps.append(Tap(ax, ay, t, "push"))
            self.last_tap_t = t
            self.pushed = True
        elif self.pushed and ratio <= self.PUSH_OFF:
            self.pushed = False

        if (
            hand.pinch <= self.PINCH_ON
            and not self.pinching
            and (t - self.last_tap_t) > self.REFRACTORY
        ):
            taps.append(Tap(hand.tip[0], hand.tip[1], t, "pinch"))
            self.last_tap_t = t
            self.pinching = True
        elif hand.pinch >= self.PINCH_OFF:
            self.pinching = False
        return taps


class MotionGate:
    """Cheap "is anything moving?" pre-check in front of MediaPipe.

    Adapted from the MOG2 pipeline in
    github.com/mattheweisenberg6/Python-Movement-Tracker (background
    subtraction + morphological clean-up + contour area filter). Running the
    landmarker only when the frame actually changed keeps idle CPU low; a
    recently seen hand always bypasses the gate so tracking never freezes.
    """

    def __init__(self, var_threshold: float = 50, history: int = 400,
                 min_fraction: float = 0.006, scale: float = 0.25):
        self._make(var_threshold, history)
        self.kernel = np.ones((5, 5), np.uint8)
        self.min_fraction = min_fraction
        self.scale = scale
        self.energy = 0.0

    def _make(self, var_threshold: float, history: int) -> None:
        self.bg = cv2.createBackgroundSubtractorMOG2(
            detectShadows=True, varThreshold=var_threshold, history=history
        )

    def update(self, frame: np.ndarray) -> bool:
        small = cv2.resize(frame, None, fx=self.scale, fy=self.scale,
                           interpolation=cv2.INTER_AREA)
        mask = self.bg.apply(small)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self.kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self.kernel)
        mask[mask == 127] = 0  # drop shadow pixels
        self.energy = float(np.count_nonzero(mask)) / float(mask.size)
        return self.energy >= self.min_fraction

    def reset(self) -> None:
        self._make(50, 400)


# --------------------------------------------------------------------------
# calibration / geometry
# --------------------------------------------------------------------------
def calibration_grid(inset: float = 0.12) -> np.ndarray:
    """3x3 target positions in screen-relative (u, v), row-major."""
    vals = [inset, 0.5, 1.0 - inset]
    pts = []
    for v in vals:
        for u in vals:
            pts.append((u, v))
    return np.asarray(pts, dtype=np.float32)


def calibration_grid5(inset: float = 0.07) -> np.ndarray:
    """Four corners then the centre - a homography plus a sanity point."""
    return np.asarray(
        [
            (inset, inset),
            (1.0 - inset, inset),
            (1.0 - inset, 1.0 - inset),
            (inset, 1.0 - inset),
            (0.5, 0.5),
        ],
        dtype=np.float32,
    )


@dataclass
class Calibration:
    """Homography between screen-relative coordinates and camera pixels.

    `M` maps (u, v) -> camera pixel; the inverse maps camera pixel -> (u, v).
    """

    screen_w: int
    screen_h: int
    cam_w: int
    cam_h: int
    M: np.ndarray
    created: float = 0.0
    rms: float = 0.0
    inliers: int = 0

    def __post_init__(self) -> None:
        self.M = np.asarray(self.M, dtype=np.float64).reshape(3, 3)
        self.Minv = np.linalg.inv(self.M)

    # -- geometry -------------------------------------------------------
    def cam_to_uv(self, x: float, y: float) -> Tuple[float, float]:
        p = self.Minv @ np.array([x, y, 1.0])
        if abs(p[2]) < 1e-9:
            return (float("nan"), float("nan"))
        return (float(p[0] / p[2]), float(p[1] / p[2]))

    def uv_to_cam(self, u: float, v: float) -> Tuple[float, float]:
        p = self.M @ np.array([u, v, 1.0])
        if abs(p[2]) < 1e-9:
            return (float("nan"), float("nan"))
        return (float(p[0] / p[2]), float(p[1] / p[2]))

    def to_screen(self, u: float, v: float) -> Tuple[float, float]:
        return (u * self.screen_w, v * self.screen_h)

    def screen_quad(self) -> np.ndarray:
        corners = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        return np.asarray([self.uv_to_cam(u, v) for u, v in corners], dtype=np.float32)

    def keyboard_quad(self) -> np.ndarray:
        """Where the keyboard sits relative to the calibrated screen, in the
        camera image. The laptop camera never sees the physical keys (it faces
        you), so this is really the 'hand is down on the keyboard' band that
        extends below the bottom edge of the screen homography."""
        keys = [(0.02, 1.02), (0.98, 1.02), (0.98, 1.30), (0.02, 1.30)]
        return np.asarray([self.uv_to_cam(u, v) for u, v in keys], dtype=np.float32)

    def zone(self, u: float, v: float) -> str:
        if not (math.isfinite(u) and math.isfinite(v)):
            return "outside"
        if -0.02 <= u <= 1.02 and -0.02 <= v <= 1.02:
            return "screen"
        if 1.02 < v <= 1.34 and -0.06 <= u <= 1.06:
            return "keyboard"
        return "outside"

    # -- fitting --------------------------------------------------------
    @classmethod
    def fit(
        cls,
        uv: np.ndarray,
        cam: np.ndarray,
        screen_size: Tuple[int, int],
        cam_size: Tuple[int, int],
    ) -> "Calibration":
        uv = np.asarray(uv, dtype=np.float32).reshape(-1, 2)
        cam = np.asarray(cam, dtype=np.float32).reshape(-1, 2)
        if len(uv) < 4:
            raise ValueError("need at least 4 calibration points")
        M, mask = cv2.findHomography(uv, cam, cv2.RANSAC, 6.0)
        if M is None:
            raise ValueError("homography failed - points are degenerate")

        used = int(mask.sum()) if mask is not None else len(uv)
        need = max(4, int(math.ceil(0.7 * len(uv))))
        if used < need:
            raise ValueError(
                f"only {used} of {len(uv)} taps agreed - redo them, tapping closer to each ring"
            )

        # honest error over every point, not just the inliers
        proj = cv2.perspectiveTransform(uv.reshape(-1, 1, 2), M).reshape(-1, 2)
        err = float(np.sqrt(((proj - cam) ** 2).sum(axis=1).mean()))
        if not math.isfinite(err) or err > 30.0:
            raise ValueError(f"taps are too scattered ({err:.0f}px) - aim at the ring and tap")

        calib = cls(
            screen_w=int(screen_size[0]),
            screen_h=int(screen_size[1]),
            cam_w=int(cam_size[0]),
            cam_h=int(cam_size[1]),
            M=M,
            created=time.time(),
            rms=err,
        )
        ok, why = calib.validate()
        if not ok:
            raise ValueError(why)
        calib.inliers = used
        return calib

    # -- quality --------------------------------------------------------
    def validate(self) -> Tuple[bool, str]:
        """Reject homographies that cannot be a real screen (flipped, collapsed,
        or with the horizon running through the target area)."""
        try:
            if not np.all(np.isfinite(self.M)):
                return False, "calibration matrix is not finite"
            corners = np.asarray(
                [self.uv_to_cam(u, v) for u, v in ((0, 0), (1, 0), (1, 1), (0, 1))],
                dtype=np.float64,
            )
            if not np.all(np.isfinite(corners)):
                return False, "calibration maps the screen to infinity"
            area = 0.5 * abs(
                np.dot(corners[:, 0], np.roll(corners[:, 1], -1))
                - np.dot(corners[:, 1], np.roll(corners[:, 0], -1))
            )
            if area < 0.05 * self.cam_w * self.cam_h:
                return False, "calibrated screen area is too small - the taps are inconsistent"
            if area > 12.0 * self.cam_w * self.cam_h:
                return False, "calibrated screen area is implausibly large"

            # orientation must match the camera image (u right, v down)
            j = corners[1] - corners[0]
            k = corners[3] - corners[0]
            if j[0] * k[1] - j[1] * k[0] <= 0:
                return False, "calibration is mirrored - redo the taps in order"

            # the horizon (w = 0) must stay outside the screen area
            ws = []
            for u in (0.0, 0.5, 1.0):
                for v in (0.0, 0.5, 1.0):
                    w = float(self.M[2, 0] * u + self.M[2, 1] * v + self.M[2, 2])
                    ws.append(w)
            if min(ws) <= 1e-6 or max(ws) / min(ws) > 8.0:
                return False, "calibration is too skewed - keep your hand flat and redo the taps"
            return True, ""
        except Exception as exc:  # pragma: no cover - defensive
            return False, f"calibration check failed: {exc}"

    # -- persistence ----------------------------------------------------
    def save(self, path: Optional[Path] = None) -> None:
        path = Path(path) if path else CALIB_PATH
        path.write_text(
            json.dumps(
                {
                    "screen_w": self.screen_w,
                    "screen_h": self.screen_h,
                    "cam_w": self.cam_w,
                    "cam_h": self.cam_h,
                    "M": self.M.tolist(),
                    "created": self.created,
                    "rms": self.rms,
                    "inliers": self.inliers,
                },
                indent=2,
            )
        )

    @classmethod
    def load(cls, path: Optional[Path] = None) -> Optional["Calibration"]:
        try:
            d = json.loads(Path(path or CALIB_PATH).read_text())
            calib = cls(
                screen_w=int(d["screen_w"]),
                screen_h=int(d["screen_h"]),
                cam_w=int(d["cam_w"]),
                cam_h=int(d["cam_h"]),
                M=np.asarray(d["M"], dtype=np.float64),
                created=float(d.get("created", 0.0)),
                rms=float(d.get("rms", 0.0)),
                inliers=int(d.get("inliers", 0)),
            )
            ok, why = calib.validate()
            if not ok:
                print(f"[airtouch] ignoring saved calibration: {why}")
                return None
            return calib
        except Exception:
            return None


# --------------------------------------------------------------------------
# mouse injection
# --------------------------------------------------------------------------
class Clicker:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.q = None
        try:
            import Quartz  # noqa: F401

            self.q = Quartz
        except Exception:
            self.q = None
        self.available = self.q is not None

    # ------------------------------------------------------------------
    def request_trust(self) -> bool:
        """Trigger the macOS "grant Accessibility" prompt (adds the app to the
        Privacy > Accessibility list, which cannot be done silently)."""
        try:
            import objc
            from CoreFoundation import (
                CFDictionaryCreate,
                CFStringCreateWithCString,
                kCFBooleanTrue,
                kCFStringEncodingUTF8,
                kCFTypeDictionaryKeyCallBacks,
                kCFTypeDictionaryValueCallBacks,
            )

            lib = ctypes.CDLL(
                "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
            )
            lib.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
            lib.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]

            key = CFStringCreateWithCString(
                None, b"AXTrustedCheckOptionPrompt", kCFStringEncodingUTF8
            )
            options = CFDictionaryCreate(
                None, [key], [kCFBooleanTrue], 1,
                kCFTypeDictionaryKeyCallBacks, kCFTypeDictionaryValueCallBacks,
            )
            return bool(lib.AXIsProcessTrustedWithOptions(objc.pyobjc_id(options)))
        except Exception as exc:
            print(f"[airtouch] accessibility prompt failed: {exc}")
            return self.trusted()

    def trusted(self) -> bool:
        try:
            lib = ctypes.CDLL(
                "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
            )
            lib.AXIsProcessTrusted.restype = ctypes.c_bool
            return bool(lib.AXIsProcessTrusted())
        except Exception:
            return False

    def _post(self, kind, x, y, click_state=1):
        q = self.q
        ev = q.CGEventCreateMouseEvent(None, kind, (float(x), float(y)), 0)
        if ev is None:
            return
        q.CGEventSetIntegerValueField(ev, q.kCGMouseEventClickState, int(click_state))
        q.CGEventPost(q.kCGHIDEventTap, ev)

    def move(self, x: float, y: float) -> None:
        if self.dry_run or not self.available:
            return
        self._post(self.q.kCGEventMouseMoved, x, y)

    def click(self, x: float, y: float, double: bool = False) -> None:
        if self.dry_run or not self.available or not self.trusted():
            return
        q = self.q
        self.move(x, y)
        if double:
            for state in (1, 2):
                self._post(q.kCGEventLeftMouseDown, x, y, state)
                self._post(q.kCGEventLeftMouseUp, x, y, state)
        else:
            self._post(q.kCGEventLeftMouseDown, x, y, 1)
            self._post(q.kCGEventLeftMouseUp, x, y, 1)


# --------------------------------------------------------------------------
# preview rendering (numpy only, no UI)
# --------------------------------------------------------------------------
def build_preview(
    frame: np.ndarray,
    hand: Optional[Hand],
    calib: Optional[Calibration],
    out_w: int = 384,
) -> np.ndarray:
    h, w = frame.shape[:2]
    out_h = int(round(out_w * h / float(w)))
    img = cv2.resize(frame, (out_w, out_h), interpolation=cv2.INTER_AREA)
    sx = out_w / float(w)
    sy = out_h / float(h)

    if calib is not None:
        quad = calib.screen_quad() * np.array([sx, sy], dtype=np.float32)
        cv2.polylines(img, [quad.astype(np.int32)], True, (90, 220, 120), 1, cv2.LINE_AA)
        kb = calib.keyboard_quad() * np.array([sx, sy], dtype=np.float32)
        cv2.polylines(img, [kb.astype(np.int32)], True, (255, 140, 90), 1, cv2.LINE_AA)

    if hand is not None:
        pts = (hand.points * np.array([sx, sy], dtype=np.float32)).astype(np.int32)
        for a, b in HAND_CONNECTIONS:
            cv2.line(img, tuple(pts[a]), tuple(pts[b]), (60, 200, 255), 1, cv2.LINE_AA)
        for i, p in enumerate(pts):
            r = 3 if i == INDEX_TIP else 1
            cv2.circle(img, tuple(p), r, (255, 255, 255), -1, cv2.LINE_AA)
        cv2.circle(img, tuple(pts[INDEX_TIP]), 6, (60, 255, 140), 1, cv2.LINE_AA)

    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
