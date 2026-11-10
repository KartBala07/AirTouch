"""AirTouch beta - camera-driven virtual touchscreen for macOS.

Run:  ./run.sh            normal start (loads calibration.json if present)
      ./run.sh --calibrate   redo the 9-point calibration
      ./run.sh --dry-run     detect + show the cursor, never click
      ./run.sh --check       diagnostics only
"""

from __future__ import annotations

import argparse
import math
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from . import core, paths, updater
from .actions import SystemActions
from .config import Config
from .gestures import Action, GestureEngine
from .smooth import PointerFilter
from .core import (
    Calibration,
    Camera,
    Clicker,
    Hand,
    HandTracker,
    MotionGate,
    TapDetector,
    build_preview,
    calibration_grid,
    calibration_grid5,
)

CALIB_INSET = 0.07
CALIB_HOLD = 0.9   # seconds the finger must rest on a target


def request_camera_access(timeout: float = 10.0):
    """Ask AVFoundation for camera access and wait for the user's answer.

    OpenCV's AVFoundation backend fires the TCC request asynchronously and then
    gives up immediately, so a first launch from a bundle fails before the user
    can click Allow. Asking here first makes the prompt usable.
    Returns True/False, or None when the answer is unknown.
    """
    try:
        import threading as _t

        import AVFoundation as AVF

        status = AVF.AVCaptureDevice.authorizationStatusForMediaType_(AVF.AVMediaTypeVideo)
        if status == 3:
            return True
        if status == 2:
            return False
        done = _t.Event()
        box = {}

        def handler(granted):
            box["granted"] = bool(granted)
            done.set()

        AVF.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVF.AVMediaTypeVideo, handler
        )
        done.wait(timeout)
        return box.get("granted")
    except Exception:
        return None


# --------------------------------------------------------------------------
# shared state (written by the tracking thread, read by the UI timer)
# --------------------------------------------------------------------------
class State:
    def __init__(self, show_preview: bool = False, dry_run: bool = False):
        self.mode = "live"                 # 'live' | 'calibrate'
        self.control = "relative"          # 'relative' | 'absolute'
        self.gain = 2.2
        self.click_single = True
        self.pointing = False
        self.posture = "none"
        self.right_drag = False
        self.rejected = ""
        self.gestures_enabled = True
        self.overlays_collapsed = False
        self.paused = False
        self.quit = False
        self.show_preview = show_preview   # camera thumbnail, calibration only
        self.show_cursor = True
        self.show_hud = False
        self.open_settings = False
        self.dry_run = dry_run

        self.tip_screen: Optional[Tuple[float, float]] = None
        self.zone: Optional[str] = None
        self.cursor_mode = "idle"
        self.flash_t = 0.0
        self.hand = False
        self.ratio = 1.0

        self.cam_fps = 0.0
        self.det_fps = 0.0
        self.accessibility = False
        self.has_calib = False
        self.calib_rms = 0.0

        self.taps = 0
        self.clicks = 0
        self.doubles = 0
        self.kb_taps = 0
        self.last_event = ""

        self.status_txt = "starting up"
        self.status_col = (0.9, 0.9, 0.9)

        self.preview_rgb = None
        self.preview_seq = 0
        self.preview_caption = "camera"

        # calibration
        self.calib_total = 5
        self.calib_progress = 0.0
        self.calib_index = 0
        self.calib_target = (0.5, 0.5)
        self.calib_hint = ""
        self.calib_error = ""
        self.calib_done = False
        self.calib_dirty = False
        self.calib_tip: Optional[Tuple[float, float]] = None
        self.calib_register = False
        self.calib_map = []

# --------------------------------------------------------------------------
# controller
# --------------------------------------------------------------------------
class Controller:
    def __init__(self, args):
        self.args = args
        self.config = Config()
        self.shell = None
        self.state = State(show_preview=not args.no_preview, dry_run=args.dry_run)
        self.state.click_single = bool(self.config.get("click_on_single"))
        self.state.paused = False
        self.overlay = None
        self.cam: Optional[Camera] = None
        self.tracker: Optional[HandTracker] = None
        self.overlay = None
        self.clicker = Clicker(dry_run=args.dry_run)
        self.calib: Optional[Calibration] = None
        self.detector = TapDetector()
        # pinch is owned by the gesture engine (click/select/right-drag), so the
        # detector must not also fire a click on the same pinch
        self.detector.PINCH_ON = -1.0
        self.detector.PINCH_OFF = -1.0
        self.gate: Optional[MotionGate] = None if args.no_motion_gate else MotionGate()
        self.gestures = GestureEngine()
        self.actions_ = SystemActions(dry_run=args.dry_run)
        self.smoother = PointerFilter(
            min_cutoff=0.15 + 1.2 * float(self.config.get("smoothing")),
            beta=0.02,
            cell=float(self.config.get("grid_cell")),
        )
        self.min_hand_scale = float(self.config.get("min_hand_scale"))
        self.gesture_wrist_y = float(self.config.get("gesture_wrist_y"))
        self.gestures_enabled = bool(self.config.get("gestures"))
        self._calib_buffer: List[Tuple[float, float, float]] = []
        self._right_down = False
        self._last_hand_t = 0.0
        self._last_track_t = 0.0
        self.grid = calibration_grid5(CALIB_INSET)
        self.calib_points: List[Tuple[float, float]] = []
        self._smooth: Optional[Tuple[float, float]] = None
        self._cursor: Optional[Tuple[float, float]] = None
        self._last_tip_norm: Optional[Tuple[float, float]] = None
        self.gain = float(getattr(args, "gain", 2.2))
        self.require_pointing = bool(self.config.get("require_pointing"))
        fps = float(self.config.get("process_fps")) or 20.0
        self._min_frame_gap = 1.0 / max(fps, 5.0)
        self._last_tap: Optional[Tuple[float, float, float]] = None
        self._calib_fit_at = 0.0
        self._calib_last_tap = 0.0
        self._calib_recorded_at = 0.0
        self._seq = -1
        self._running = False
        self._screen = (1512, 982)
        self._trace: List[Tuple[float, float, float]] = []

    # ------------------------------------------------------------------
    def calib_path(self) -> Path:
        explicit = getattr(self.args, "calib", None)
        return Path(explicit) if explicit else paths.calib_path()

    def screen_size(self) -> Tuple[int, int]:
        return self._screen

    def apply_settings(self) -> None:
        """Push the config into the live objects (settings window calls this)."""
        cfg = self.config
        st = self.state
        st.gain = float(cfg.get("gain"))
        if self.detector is not None:
            self.detector.PUSH_ON = float(cfg.get("push_threshold"))
        st.click_single = bool(cfg.get("click_on_single"))
        st.show_cursor = bool(cfg.get("show_cursor"))
        self.clicker.dry_run = bool(cfg.get("dry_run")) or self.args.dry_run
        st.dry_run = self.clicker.dry_run

        want_gate = bool(cfg.get("motion_gate")) and not self.args.no_motion_gate
        if want_gate and self.gate is None:
            self.gate = MotionGate()
        elif not want_gate:
            self.gate = None

        mode = cfg.get("mode") or "auto"
        if mode == "auto":
            mode = "absolute" if self.calib is not None else "relative"
        st.control = "absolute" if mode == "absolute" else "relative"
        if st.control == "absolute" and self.calib is None:
            st.control = "relative"
            print("[airtouch] absolute mode needs calibration - staying in air-trackpad")

        if self.overlay is not None:
            self.overlay.set_cursor_visible(st.show_cursor)

    def setup(self) -> bool:
        from .ui import main_screen_size

        sw, sh = main_screen_size()
        self._screen = (int(sw), int(sh))
        self.state.accessibility = self.clicker.trusted()
        if not self.state.accessibility and not self.config.get("ax_prompted"):
            self.config.set("ax_prompted", True)
            print("[airtouch] Accessibility is off - asking macOS to prompt")
            self.clicker.request_trust()

        if not self.args.demo:
            granted = request_camera_access()
            if granted is False:
                print("[airtouch] camera access was denied for this app", file=sys.stderr)
            self.cam = Camera(
                index=int(self.config.get("camera_index")),
                width=int(self.config.get("capture_width")),
                height=int(self.config.get("capture_height")),
            )
            if not self.cam.start():
                self._report_camera_failure()
                return False
            w, h = self.cam.size
            print(f"[airtouch] camera {w}x{h} @ {self.cam.fps:.0f}fps")

        self.tracker = HandTracker()
        self.calib = None if self.args.calibrate else Calibration.load(self.calib_path())
        if self.calib is not None and (
            self.calib.screen_w != self._screen[0] or self.calib.screen_h != self._screen[1]
        ):
            print("[airtouch] calibration was made for a different screen size - redo it")
            self.calib = None
        self.state.has_calib = self.calib is not None
        mode = getattr(self.args, "mode", "auto")
        if mode == "auto":
            mode = "absolute" if self.calib is not None else "relative"
        if mode == "absolute" and self.calib is None:
            print("[airtouch] absolute mode needs a calibration - starting the 9-point setup")
        self.state.control = "absolute" if mode == "absolute" else "relative"
        self.require_pointing = bool(self.config.get("require_pointing"))
        fps = float(self.config.get("process_fps")) or 20.0
        self._min_frame_gap = 1.0 / max(fps, 5.0)
        self.state.overlays_collapsed = bool(self.config.get("overlays_collapsed"))
        self.state.gain = self.gain
        print(f"[airtouch] control mode: {self.state.control}")
        if self.calib is not None:
            self.state.calib_rms = self.calib.rms
            print(f"[airtouch] loaded calibration ({self.calib.rms:.2f}px rms)")
        return True

    # ------------------------------------------------------------------
    def _report_camera_failure(self) -> None:
        err = (self.cam.error if self.cam else "unknown") or "unknown"
        print("", file=sys.stderr)
        print("AirTouch cannot read the camera (%s)." % err, file=sys.stderr)
        print("", file=sys.stderr)
        print("Fix: grant camera access to the app that launched this process.", file=sys.stderr)
        print("  System Settings > Privacy & Security > Camera  ->  enable AirTouch / Terminal", file=sys.stderr)
        print("  Then relaunch.  ('./run.sh --check' re-tests it.)", file=sys.stderr)
        try:
            import subprocess

            from AppKit import NSAlert, NSApplication

            app = NSApplication.sharedApplication()
            app.setActivationPolicy_(0)
            app.activateIgnoringOtherApps_(True)
            alert = NSAlert.alloc().init()
            alert.setMessageText_("AirTouch cannot use the camera")
            alert.setInformativeText_(
                "Grant Camera access to AirTouch in System Settings > Privacy & Security > "
                "Camera, then reopen the app.\n\nAfter an update macOS asks again."
            )
            alert.addButtonWithTitle_("Open Camera Settings")
            alert.addButtonWithTitle_("Quit")
            if alert.runModal() == 1000:
                subprocess.run(
                    ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera"],
                    check=False,
                )
        except Exception:
            pass

    # ------------------------------------------------------------------
    def start_calibration(self) -> None:
        self.calib_points = []
        self._calib_buffer = []
        self.state.calib_progress = 0.0
        self.state.calib_map = []
        self.calib = None
        self.state.has_calib = False
        self.state.mode = "calibrate"
        self.state.calib_index = 0
        self.state.calib_done = False
        self.state.calib_error = ""
        self.state.calib_hint = (
            "Hold your index fingertip on the ring until it fills - no tapping needed."
        )
        self.state.calib_total = len(self.grid)
        self.state.calib_target = tuple(self.grid[0])
        self.state.calib_dirty = True
        self._calib_fit_at = 0.0
        self._calib_last_tap = time.monotonic()
        self._calib_recorded_at = 0.0
        self._smooth = None

    # ------------------------------------------------------------------
    def _finish_calibration(self) -> None:
        cam_size = self.cam.size if self.cam is not None else (1280, 720)
        try:
            calib = Calibration.fit(
                self.grid, np.asarray(self.calib_points, dtype=np.float32),
                self._screen, cam_size,
            )
        except Exception as exc:  # degenerate taps
            self.state.calib_error = f"{exc} - try again, tap firmly at each ring"
            self.state.calib_index = 0
            self.calib_points = []
            self.state.calib_target = tuple(self.grid[0])
            self.state.calib_dirty = True
            return

        self.calib = calib
        try:
            calib.save(self.calib_path())
        except Exception as exc:
            print(f"[airtouch] could not save calibration: {exc}", file=sys.stderr)
        self.state.has_calib = True
        self.state.calib_rms = calib.rms
        self.state.calib_done = True
        self.state.calib_hint = (
            f"saved to {self.calib_path()}  (reprojection error {calib.rms:.1f}px)"
        )
        self.state.calib_dirty = True
        self._calib_fit_at = time.monotonic()
        print(f"[airtouch] calibration saved, rms {calib.rms:.2f}px "
              f"({calib.inliers}/9 inliers)")

    # ------------------------------------------------------------------
    def _record_calib_point(self, x: float, y: float) -> None:
        if self.state.calib_done:
            return
        self.calib_points.append((x, y))
        cw, ch = self.cam.size if self.cam is not None else (1280, 720)
        self.state.calib_map = list(self.state.calib_map) + [(x / cw, y / ch)]
        self.state.calib_index = len(self.calib_points)
        self.state.calib_dirty = True
        print(f"[airtouch] calib point {len(self.calib_points)}/{len(self.grid)} at ({x:.0f},{y:.0f})")
        if len(self.calib_points) >= len(self.grid):
            self._finish_calibration()
        else:
            self.state.calib_target = tuple(self.grid[len(self.calib_points)])
            self.state.calib_hint = "now hold on the next ring"

    def _calibration_step(self, frame, hand: Optional[Hand], taps, now: float) -> None:
        self.state.calib_tip = None

        # provisional mapping so the "you" dot appears as soon as it can
        if hand is not None and len(self.calib_points) >= 4 and not self.state.calib_done:
            try:
                tmp = Calibration.fit(
                    self.grid[:len(self.calib_points)],
                    np.asarray(self.calib_points, dtype=np.float32),
                    self._screen,
                    (frame.shape[1], frame.shape[0]),
                )
                u, v = tmp.cam_to_uv(*hand.tip_px)
                if math.isfinite(u) and math.isfinite(v):
                    self.state.calib_tip = (min(max(u, 0.0), 1.0), min(max(v, 0.0), 1.0))
            except Exception:
                self.state.calib_tip = None

        if self.state.calib_done:
            if now - self._calib_fit_at > 1.6:
                self.state.mode = "live"
                self.state.calib_done = False
                self.state.tip_screen = None
                self.smoother.reset()
            return

        if hand is None:
            self._calib_buffer = []
            self.state.calib_progress = 0.0
            self.state.calib_hint = "no hand - hold your fingertip over the ring"
            return

        tip = hand.tip_px

        # mouse click / N still works as a manual override
        if self.state.calib_register:
            self.state.calib_register = False
            self._calib_buffer = []
            self.state.calib_progress = 0.0
            self._record_calib_point(tip[0], tip[1])
            return

        if not hand.index_extended:
            self._calib_buffer = []
            self.state.calib_progress = 0.0
            self.state.calib_hint = "extend your index finger and hold it on the ring"
            return

        buf = self._calib_buffer
        if buf:
            mx = float(np.median([b[0] for b in buf]))
            my = float(np.median([b[1] for b in buf]))
            if math.hypot(tip[0] - mx, tip[1] - my) > 55.0:
                self._calib_buffer = [(tip[0], tip[1], now)]
                self.state.calib_progress = 0.0
                self.state.calib_hint = "hold still on the ring"
                return

        buf.append((tip[0], tip[1], now))
        span = now - buf[0][2]
        self.state.calib_progress = min(1.0, span / CALIB_HOLD)
        self.state.calib_hint = "hold still..." if span < CALIB_HOLD else "got it"

        if span >= CALIB_HOLD:
            xs = np.asarray([b[0] for b in buf], dtype=np.float32)
            ys = np.asarray([b[1] for b in buf], dtype=np.float32)
            self._calib_buffer = []
            self.state.calib_progress = 0.0
            self._record_calib_point(float(np.median(xs)), float(np.median(ys)))

    # ------------------------------------------------------------------
    def _process_tap(self, tap, frame, hand: Hand, now: float) -> None:
        if self.state.control == "relative" or self.calib is None:
            sx, sy = self._cursor or (self._screen[0] / 2, self._screen[1] / 2)
            self._tap_at(sx, sy, now, tap)
            return
        h, w = frame.shape[:2]
        px, py = tap.x * w, tap.y * h
        u, v = self.calib.cam_to_uv(px, py)
        zone = self.calib.zone(u, v)

        if zone != "screen":
            self.state.taps += 1
            if zone == "keyboard":
                self.state.kb_taps += 1
                self.state.last_event = "keyboard-zone tap (beta: tracked only)"
                self.state.flash_t = time.monotonic()
                self.state.zone = "keyboard"
            return

        sx, sy = self.calib.to_screen(u, v)
        sx = min(max(sx, 0.0), self._screen[0] - 1)
        sy = min(max(sy, 0.0), self._screen[1] - 1)
        self._tap_at(sx, sy, now, tap)

    def _tap_at(self, sx: float, sy: float, now: float, tap) -> None:
        self.state.taps += 1
        # never click the menu bar / notch strip by accident
        if sy < 26.0:
            self.state.last_event = "tap ignored (menu bar)"
            return
        if self.state.paused:
            self.state.last_event = "tap ignored (paused)"
            return

        double = False
        if self._last_tap is not None:
            lt, lx, ly = self._last_tap
            if now - lt < 0.60 and math.hypot(sx - lx, sy - ly) < 0.055 * self._screen[0]:
                double = True
        self._last_tap = None if double else (now, sx, sy)

        self._smooth = (sx, sy)
        self.state.tip_screen = (sx, sy)
        self.state.flash_t = time.monotonic()

        send = True
        if double:
            self.state.doubles += 1
            self.state.last_event = f"double-click {int(sx)},{int(sy)}"
        elif self.state.click_single:
            self.state.clicks += 1
            self.state.last_event = f"click {int(sx)},{int(sy)}"
        else:
            # double-tap-only mode: the first tap waits for a second one
            send = False
            self.state.last_event = "single tap - no click (double-tap mode)"

        if not send:
            print(f"[airtouch] {self.state.last_event} [{tap.kind}]")
            return

        if self.clicker.dry_run or not self.clicker.trusted():
            self.state.last_event += " (not sent)"
        else:
            self.clicker.click(sx, sy, double=double)
        print(f"[airtouch] {self.state.last_event} [{tap.kind}]")

    # ------------------------------------------------------------------
    def _relative_step(self, frame, hand: Optional[Hand], taps, now: float) -> None:
        """Air-trackpad: the cursor follows fingertip *movement*, so no
        calibration (and no accurate aiming at the screen) is required."""
        if hand is None or self.state.paused:
            self.state.tip_screen = None
            self.state.zone = None
            self._last_tip_norm = None
            return

        x, y = hand.tip
        self.state.zone = "screen"
        if self._cursor is None:
            self._cursor = (self._screen[0] / 2.0, self._screen[1] / 2.0)
        if self._last_tip_norm is None:
            self._last_tip_norm = (x, y)
            self.state.tip_screen = self._cursor
            return

        dx = x - self._last_tip_norm[0]
        dy = y - self._last_tip_norm[1]
        self._last_tip_norm = (x, y)

        # a poke drags the fingertip around - do not let it move the cursor
        if now - self.state.flash_t < 0.25:
            dx = dy = 0.0
        if abs(dx) < 0.0013:
            dx = 0.0
        if abs(dy) < 0.0013:
            dy = 0.0

        cx, cy = self._cursor
        cx = min(max(cx + dx * self._screen[0] * self.gain, 0.0), self._screen[0] - 1)
        cy = min(max(cy + dy * self._screen[1] * self.gain, 0.0), self._screen[1] - 1)
        self._cursor = (cx, cy)
        sx, sy = self.smoother.update(cx, cy, now)
        self.state.tip_screen = (sx, sy)
        if self._right_down:
            self.actions_.right_drag(sx, sy)

        for tap in taps:
            self._process_tap(tap, frame, hand, now)

    def _live_step(self, frame, hand: Optional[Hand], taps, now: float) -> None:
        if hand is None or self.state.paused:
            self.gestures.reset()
            self.state.posture = "none"
            self.state.right_drag = False
            self._last_tip_norm = None
            self.state.tip_screen = None
            self.state.zone = None
            return

        gestures_allowed = self.gestures_enabled and hand.wrist[1] >= self.gesture_wrist_y
        for action in self.gestures.update(hand, now, True, gestures_allowed):
            self._run_action(action, frame, hand, now)
        self.state.posture = self.gestures.mode
        self.state.right_drag = self.gestures.right_active

        # scroll / screenshot / swipe modes freeze the pointer
        if not self.gestures.cursor_active:
            self._last_tip_norm = hand.tip
            return

        # the pointer and the poke-click need a pointing hand; the gesture
        # actions above do not
        if not self.state.pointing:
            self._last_tip_norm = hand.tip
            return

        if self.state.control == "relative":
            self._relative_step(frame, hand, taps, now)
            return
        if self.calib is None:
            self.state.tip_screen = None
            return
        if hand is None:
            self.state.tip_screen = None
            self.state.zone = None
            return

        u, v = self.calib.cam_to_uv(*hand.tip_px)
        zone = self.calib.zone(u, v)
        self.state.zone = zone
        if zone == "outside":
            self.state.tip_screen = None
            return

        sx, sy = self.calib.to_screen(u, v)
        sx = min(max(sx, 0.0), self._screen[0] - 1)
        sy = min(max(sy, 0.0), self._screen[1] - 1)

        if self._smooth is None or math.hypot(sx - self._smooth[0], sy - self._smooth[1]) > 260:
            self._smooth = (sx, sy)
            self.smoother.reset()
        px_, py_ = self.smoother.update(sx, sy, now)
        self._smooth = (px_, py_)
        self.state.tip_screen = self._smooth
        if self._right_down:
            self.actions_.right_drag(px_, py_)

        for tap in taps:
            self._process_tap(tap, frame, hand, now)

    # ------------------------------------------------------------------
    def _run_action(self, action: Action, frame, hand: Hand, now: float) -> None:
        kind = action.kind
        cx, cy = self._cursor or (self._screen[0] / 2.0, self._screen[1] / 2.0)

        if kind == "click":
            self._tap_at(cx, cy, now, type("T", (), {"kind": "pinch"})())
        elif kind == "double_click":
            self.state.taps += 1
            self.state.doubles += 1
            self.state.flash_t = time.monotonic()
            self.state.last_event = f"select (double) {int(cx)},{int(cy)}"
            if not (self.clicker.dry_run or not self.clicker.trusted()):
                self.clicker.click(cx, cy, double=True)
            print(f"[airtouch] {self.state.last_event}")
        elif kind == "right_down":
            self._right_down = True
            self.state.right_drag = True
            self.state.last_event = "right button down"
            self.actions_.right_down(cx, cy)
            self.state.flash_t = time.monotonic()
        elif kind == "right_up":
            self._right_down = False
            self.state.right_drag = False
            self.actions_.right_up(cx, cy)
            self.state.last_event = "right click"
            print("[airtouch] right click")
        elif kind == "scroll":
            self.actions_.scroll(action.value)
        elif kind == "screenshot":
            ok = self.actions_.screenshot_to_clipboard()
            self.state.last_event = "screenshot to clipboard" if ok else "screenshot failed"
            self.state.flash_t = time.monotonic()
            print(f"[airtouch] {self.state.last_event}")
        elif kind == "paste":
            self.actions_.paste()
            self.state.last_event = "pasted screenshot"
            self.state.flash_t = time.monotonic()
            print("[airtouch] pasted screenshot")
        elif kind == "switch_window":
            self.actions_.switch_window(action.value)
            self.state.last_event = "switched window"
            print(f"[airtouch] switch window {'next' if action.value > 0 else 'previous'}")
        elif kind == "mission_control":
            self.actions_.mission_control()
            self.state.last_event = "mission control"
            print("[airtouch] mission control")

    # ------------------------------------------------------------------
    def _maybe_preview(self, frame, hand: Optional[Hand], now: float) -> None:
        # the thumbnail only exists inside the calibration screen: keeping the
        # image pipeline off while tracking saves a lot of CPU and memory
        if self.state.mode != "calibrate":
            return
        if now - getattr(self, "_last_preview_at", 0.0) < 1 / 20.0:
            return
        self._last_preview_at = now
        try:
            self.state.preview_rgb = build_preview(frame, hand, self.calib, 384)
            self.state.preview_seq += 1
            self.state.preview_caption = (
                "camera - screen(green) keyboard(blue)" if self.calib else "camera"
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _demo_frame(self, t: float):
        import cv2

        frame = np.zeros((360, 640, 3), np.uint8)
        frame[:] = (18, 20, 26)
        cv2.putText(frame, "DEMO - no camera", (18, 336), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (90, 200, 255), 1, cv2.LINE_AA)
        return frame

    def _demo_hand(self, t: float) -> Hand:
        """Fingertip orbits the calibrated screen; pokes forward twice every 2.6s."""
        u = 0.5 + 0.36 * math.cos(t * 0.9)
        v = 0.5 + 0.32 * math.sin(t * 1.3)
        base = 0.19
        poke = 0.0
        local = t % 2.6
        if local < 0.14:
            poke = 0.012 * (local / 0.14)
        elif local < 0.28:
            poke = 0.012 * (1 - (local - 0.14) / 0.14)
        if self.calib is not None:
            px, py = self.calib.uv_to_cam(u, v)
        else:
            px, py = 640 * u, 360 * v
        pts = np.zeros((21, 2), np.float32)
        pts[core.WRIST] = (px, py + 120)
        pts[core.INDEX_MCP] = (px, py + 70)
        pts[core.INDEX_PIP] = (px, py + 40)
        pts[core.INDEX_TIP] = (px, py)
        pts[core.MIDDLE_MCP] = (px + 40, py + 90)
        pts[core.MIDDLE_TIP] = (px + 40, py + 20)
        pts[core.THUMB_TIP] = (px - 30, py + 60)
        scale = (base + poke) * (1 + 0.1 * math.sin(t * 7))
        return Hand(
            tip_px=(float(px), float(py)),
            tip=(float(px / 640.0), float(py / 360.0)),
            scale=float(scale),
            pinch=0.8 + 0.3 * math.sin(t * 3),
            index_extended=True,
            points=pts,
        )

    # ------------------------------------------------------------------
    def _demo_calib(self) -> Calibration:
        uv = self.grid
        cam = np.stack([180 + uv[:, 0] * 900, 120 + uv[:, 1] * 460], axis=1)
        return Calibration.fit(uv, cam.astype(np.float32), self._screen, (1280, 720))

    # ------------------------------------------------------------------
    def _tracking_loop(self) -> None:
        last = time.monotonic()
        while self._running:
            now = time.monotonic()
            if self.args.demo:
                time.sleep(1 / 30.0)
                frame = self._demo_frame(now)
                hand = self._demo_hand(now)
                taps = self.detector.update(hand, now)
                self.state.hand = True
                self.state.pointing = True
                self.state.cam_fps = 30.0
                self.state.ratio = hand.scale / max(self.detector.baseline or hand.scale, 1e-9)
                if self.state.mode == "calibrate":
                    self._calibration_step(frame, hand, taps, now)
                else:
                    self._live_step(frame, hand, taps, now)
                self._maybe_preview(frame, hand, now)
            else:
                # paused = camera released: no capture, no inference, no battery
                if self.state.paused:
                    if self.cam is not None and self.cam.is_running:
                        self.cam.stop()
                        print("[airtouch] paused - camera released")
                    self.state.tip_screen = None
                    self.state.det_fps = 0.0
                    time.sleep(0.15)
                    continue
                if self.cam is not None and not self.cam.is_running:
                    if not self.cam.start():
                        print("[airtouch] camera reopen failed:", self.cam.error)
                        time.sleep(0.5)
                        continue
                    self._seq = -1
                    print("[airtouch] resumed - camera reopened")

                frame, ts, seq = self.cam.read()
                if frame is None or seq == self._seq:
                    time.sleep(0.004)
                    continue
                self._seq = seq

                # cap inference rate: past ~20 fps the extra frames buy nothing,
                # and drop to 8 fps when no hand has been seen for a moment
                recent = (now - self._last_hand_t) < 0.8
                gap = self._min_frame_gap if recent else max(self._min_frame_gap, 1 / 8.0)
                if now - self._last_track_t < gap:
                    time.sleep(0.003)
                    continue

                # motion gate (MOG2): skip MediaPipe when nothing moved and no
                # hand was seen recently, so idle frames cost ~nothing
                if self.gate is not None and (now - self._last_hand_t) > 0.6:
                    moving = self.gate.update(frame)
                    # never go fully blind: keep a floor even when still
                    if not moving and (now - self._last_track_t) < 0.34:
                        self.state.cam_fps = self.cam.fps
                        self._maybe_preview(frame, None, now)
                        time.sleep(0.004)
                        continue

                self._last_track_t = now
                hand = self.tracker.process(frame, ts)
                self.state.hand = hand is not None
                self.state.cam_fps = self.cam.fps

                # A closed hand, a fist, or a hand gripping a cup/phone is not an
                # input device: ignore it for cursor and taps, but keep drawing it.
                active = None
                taps = []
                if hand is not None:
                    self._last_hand_t = now
                    # ignore hands that are far away, half out of frame, or at
                    # face height - that is where false positives come from
                    if hand.scale < self.min_hand_scale:
                        self.state.rejected = "too far"
                        hand_ok = False
                    elif not (0.02 <= hand.tip[0] <= 0.98 and 0.03 <= hand.tip[1] <= 0.99):
                        self.state.rejected = "edge of frame"
                        hand_ok = False
                    else:
                        self.state.rejected = ""
                        hand_ok = True
                    self.state.pointing = hand_ok and (
                        bool(hand.pointing) or not self.require_pointing
                    )
                    # a fist or a flat palm is not "pointing", but the gesture
                    # engine still needs to see it
                    if hand_ok:
                        active = hand
                    if self.state.pointing:
                        taps = self.detector.update(hand, now)
                        self.state.ratio = hand.scale / max(
                            self.detector.baseline or hand.scale, 1e-9
                        )
                    else:
                        self.detector.reset()
                        self.state.ratio = 1.0
                else:
                    self.state.pointing = False
                    self.state.rejected = ""
                    self.detector.reset()
                    self.state.ratio = 1.0

                if self.state.mode == "calibrate":
                    self._calibration_step(frame, active, taps, now)
                else:
                    self._live_step(frame, active, taps, now)
                self._maybe_preview(frame, hand, now)

            dt = max(now - last, 1e-3)
            last = now
            inst = 1.0 / dt
            self.state.det_fps = (
                inst if self.state.det_fps == 0 else 0.9 * self.state.det_fps + 0.1 * inst
            )

    # ------------------------------------------------------------------
    def _status(self) -> None:
        st = self.state
        if st.mode == "calibrate":
            st.status_txt = "calibrating"
            st.status_col = (1.0, 0.78, 0.30)
        elif st.hand and not st.pointing:
            st.status_txt = "hand up - point your index finger"
            st.status_col = (1.0, 0.72, 0.25)
        elif st.tip_screen is None:
            st.status_txt = "waiting for a hand"
            st.status_col = (0.75, 0.78, 0.85)
        elif st.zone == "keyboard":
            st.status_txt = "hand over keyboard"
            st.status_col = (0.60, 0.70, 1.0)
        else:
            st.status_txt = "tracking screen"
            st.status_col = (0.30, 0.95, 0.55)

    # ------------------------------------------------------------------
    def run(self) -> int:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        from .ui import AppDelegate, Overlay

        if not self.setup():
            return 2

        if self.args.demo and self.calib is None:
            self.calib = self._demo_calib()
            self.state.has_calib = True
            self.state.calib_rms = 0.0
            print("[airtouch] demo calibration generated (identity-ish)")

        if self.calib is None and self.state.control == "absolute":
            self.start_calibration()

        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self.overlay = Overlay.alloc().initWithState_(self.state)
        from .ui import AppShell

        self.shell = AppShell.alloc().initWithController_(self)
        self.overlay.shell = self.shell
        self.overlay.set_cursor_visible(bool(self.config.get("show_cursor")))
        self.apply_settings()
        delegate = AppDelegate.alloc().initWithOverlay_(self.overlay)
        app.setDelegate_(delegate)

        self._running = True
        threading.Thread(target=self._tracking_loop, name="tracking", daemon=True).start()
        threading.Thread(target=self._status_loop, name="status", daemon=True).start()

        def _sigint(signum, frame_):
            self.state.quit = True

        signal.signal(signal.SIGINT, _sigint)
        signal.signal(signal.SIGTERM, _sigint)

        print("[airtouch] running. Ctrl+C here (or quit the terminal) to stop.")
        try:
            app.run()
        finally:
            self._running = False
            if self.cam is not None:
                self.cam.stop()
            if self.tracker is not None:
                self.tracker.close()
        return 0

    def _status_loop(self) -> None:
        while self._running:
            self._status()
            if self.state.quit:
                from AppKit import NSApplication

                NSApplication.sharedApplication().terminate_(None)
                return
            time.sleep(0.05)


# --------------------------------------------------------------------------
# diagnostics
# --------------------------------------------------------------------------
def check(args) -> int:
    ok = True
    print("AirTouch diagnostics")
    print("-" * 46)
    print(f"python           {sys.version.split()[0]}  ({sys.executable})")

    try:
        import cv2

        print(f"opencv           {cv2.__version__}")
    except Exception as exc:
        ok = False
        print(f"opencv           MISSING ({exc})")

    try:
        import mediapipe

        print(f"mediapipe        {mediapipe.__version__}")
    except Exception as exc:
        ok = False
        print(f"mediapipe        MISSING ({exc})")

    print(f"hand model       {'ok' if core.MODEL_PATH.exists() else 'MISSING'}  {core.MODEL_PATH}")

    clicker = Clicker()
    print(f"accessibility    {'granted' if clicker.trusted() else 'NOT granted (clicks will not post)'}")
    print(f"quartz events    {'ok' if clicker.available else 'unavailable'}")

    try:
        from .ui import main_screen_size

        sw, sh = main_screen_size()
        print(f"screen           {int(sw)} x {int(sh)} points")
    except Exception as exc:
        ok = False
        print(f"screen           FAILED ({exc})")

    calib = Calibration.load(args.calib)
    if calib is None:
        print("calibration      none yet")
    else:
        print(
            f"calibration      ok  {calib.screen_w}x{calib.screen_h}  "
            f"rms {calib.rms:.2f}px  {time.strftime('%Y-%m-%d %H:%M', time.localtime(calib.created))}"
        )

    granted = request_camera_access()
    print(f"camera access    {'granted' if granted else ('denied' if granted is False else 'unknown')}")

    cam = Camera(index=args.camera)
    print("camera           opening...")
    if cam.start(timeout=5.0):
        f, ts, seq = cam.read()
        print(f"camera           ok  {f.shape[1]}x{f.shape[0]}  {cam.fps:.0f}fps")
        cam.stop()
    else:
        ok = False
        print(f"camera           FAILED ({cam.error})")
        print()
        print("  Grant camera access: System Settings > Privacy & Security > Camera")
        print("  Enable AirTouch (or Terminal / the app you launch from), then retry.")
        try:
            import subprocess

            from AppKit import NSAlert, NSApplication

            app = NSApplication.sharedApplication()
            app.setActivationPolicy_(0)
            app.activateIgnoringOtherApps_(True)
            alert = NSAlert.alloc().init()
            alert.setMessageText_("AirTouch cannot use the camera")
            alert.setInformativeText_(
                "Grant Camera access to AirTouch in System Settings > Privacy & Security > "
                "Camera, then reopen the app.\n\nAfter an update macOS asks again."
            )
            alert.addButtonWithTitle_("Open Camera Settings")
            alert.addButtonWithTitle_("Quit")
            if alert.runModal() == 1000:
                subprocess.run(
                    ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera"],
                    check=False,
                )
        except Exception:
            pass

    print("-" * 46)
    print("RESULT:", "ready" if ok else "not ready")
    return 0 if ok else 1


# --------------------------------------------------------------------------
def shot(args) -> int:
    """Render each window's view offscreen to PNG - a headless UI self-check."""
    import cv2
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    from . import ui as U

    out = Path(args.shot)
    out.mkdir(parents=True, exist_ok=True)
    for stale in list(out.glob("hud*.png")) + list(out.glob("preview*.png")) + list(out.glob("chip*.png")):
        try:
            stale.unlink()
        except OSError:
            pass
    st = State(show_preview=True, dry_run=True)
    st.hand = True
    st.ratio = 1.06
    st.cam_fps = 29.4
    st.det_fps = 27.1
    st.taps = 14
    st.clicks = 9
    st.doubles = 3
    st.kb_taps = 2
    st.calib_rms = 2.3
    st.has_calib = True
    st.accessibility = False
    st.zone = "screen"
    st.tip_screen = (760.0, 420.0)
    st.last_event = "click 758,421"
    st.status_txt = "tracking screen"
    st.status_col = (0.30, 0.95, 0.55)

    # synth camera + calibration for the preview
    frame = np.zeros((720, 1280, 3), np.uint8)
    frame[:] = (26, 30, 38)
    cv2.putText(frame, "camera preview (synthetic)", (40, 60), cv2.FONT_HERSHEY_SIMPLEX,
                0.9, (120, 200, 255), 2, cv2.LINE_AA)
    grid = calibration_grid(CALIB_INSET)
    cam = np.stack([180 + grid[:, 0] * 900, 120 + grid[:, 1] * 460], axis=1).astype(np.float32)
    from .core import Calibration as C

    calib = C.fit(grid, cam, (1512, 982), (1280, 720))
    hand = None
    try:
        from .core import Hand

        pts = np.zeros((21, 2), np.float32)
        base = (620.0, 400.0)
        for i, (dx, dy) in enumerate(
            [(0, 150), (-40, 90), (-55, 55), (-62, 20), (-66, 0),
             (0, 90), (6, 45), (10, 20), (12, 0), (40, 100), (46, 60), (50, 30), (52, 0),
             (80, 120), (86, 80), (90, 50), (92, 20),
             (115, 140), (120, 105), (124, 80), (126, 55)]
        ):
            pts[i] = (base[0] + dx, base[1] + dy)
        hand = Hand((base[0] - 66, base[1]), ((base[0] - 66) / 1280, base[1] / 720),
                    0.19, 0.8, True, pts)
    except Exception:
        pass
    st.preview_rgb = build_preview(frame, hand, calib, 384)
    st.preview_caption = "camera - screen(green) keyboard(blue)"

    cur = U.CursorView.alloc().initWithFrame_(U.NSMakeRect(0, 0, 80, 80))
    cur.flash_t = time.monotonic()
    U.render_view(cur, out / "cursor.png")

    # fake desktop backdrop so contrast can be judged
    sw, sh = 1512, 982
    yy = np.linspace(0.0, 1.0, sh, dtype=np.float32)[:, None, None]
    desk = (np.array([28, 40, 62], np.float32) * (1 - yy) + np.array([64, 58, 96], np.float32) * yy)
    desk = np.repeat(desk, sw, axis=1)
    desk[120:520, 180:900] = np.array([238, 240, 246], np.float32)
    desk[140:180, 200:520] = np.array([70, 120, 220], np.float32)
    for row in range(6):
        desk[220 + row * 40:232 + row * 40, 210:860] = np.array([150, 155, 170], np.float32)
    desk[560:900, 980:1430] = np.array([24, 26, 32], np.float32)
    backdrop = U.image_from_rgb(desk.astype(np.uint8))

    cv = U.CalibrationFullView.alloc().initWithFrame_(U.NSMakeRect(0, 0, sw, sh))
    cv.state = st
    cv.index = 2
    cv.total = 5
    cv.target = (0.5, 0.5)
    cv.hint = "aim, then tap toward the screen"
    st.calib_tip = (0.44, 0.42)
    st.calib_map = [(0.30, 0.26), (0.50, 0.23), (0.70, 0.25), (0.31, 0.45), (0.52, 0.43)]
    st.calib_hint = "hold still..."
    st.calib_progress = 0.62
    cv.setNeedsDisplay_(True)
    U.render_view(cv, out / "calibration.png", backdrop=backdrop)

    # settings panel
    try:
        class _FakeCtl:
            def __init__(self, st, cfg):
                self.state = st
                self.config = cfg
                self.calib = None
                self.overlay = None

        cfg = Config()
        cfg.data["gain"] = 2.6
        shell = U.AppShell.alloc().initWithController_(_FakeCtl(st, cfg))
        sv = shell.buildSettingsView_((470, 450))
        shell._sync_controls()
        U.render_view(sv, out / "settings.png")
    except Exception as exc:
        print("settings render failed:", exc)

    print("wrote", ", ".join(str(p.name) for p in sorted(out.glob("*.png"))), "to", out)
    return 0


# --------------------------------------------------------------------------
def install_file_log() -> None:
    """Windowed .app bundles have no stdout - tee everything into a log file."""
    if not getattr(sys, "frozen", False):
        return
    try:
        log = paths.log_path()
        if log.exists() and log.stat().st_size > 2_000_000:
            log.write_text("")
        handle = open(log, "a", buffering=1, errors="replace")
        sys.stdout = handle
        sys.stderr = handle
        os.dup2(handle.fileno(), 1)
        os.dup2(handle.fileno(), 2)
        print(f"\n=== AirTouch {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    except Exception:
        pass


def self_update() -> int:
    """Headless update: check, download, stage, exit (the swap script finishes)."""
    from .version import __version__

    state, msg, release = updater.check()
    print(f"[update] running {__version__}: {msg}")
    if state != "available" or release is None:
        return 0
    ok, detail = updater.install_release(release)
    print(f"[update] {detail}")
    return 0 if ok else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="airtouch", description=__doc__)
    p.add_argument("--calibrate", action="store_true", help="redo the 9-point calibration")
    p.add_argument("--dry-run", action="store_true", help="track + show cursor, never click")
    p.add_argument("--no-preview", action="store_true", help="hide the camera preview window")
    p.add_argument("--camera", type=int, default=0, help="camera index (default 0)")
    p.add_argument("--demo", action="store_true", help="synthetic hand, no camera (implies --dry-run)")
    p.add_argument("--no-motion-gate", action="store_true",
                   help="run MediaPipe on every frame (higher CPU, no MOG2 gate)")
    p.add_argument("--mode", choices=["auto", "relative", "absolute"], default="auto",
                   help="auto (absolute when calibrated, else air-trackpad), "
                        "relative (air-trackpad, no calibration), absolute (needs calibration)")
    p.add_argument("--gain", type=float, default=2.2,
                   help="air-trackpad sensitivity: cursor speed per hand movement")
    p.add_argument("--calib", default=None, help="calibration file path")
    p.add_argument("--settings", action="store_true", help="open the settings window on start")
    p.add_argument("--check", action="store_true", help="print diagnostics and exit")
    p.add_argument("--self-update", action="store_true",
                   help="check GitHub for a newer release, install it and quit")
    p.add_argument("--shot", default=None, help="render UI to PNGs in this directory and exit")
    args = p.parse_args(argv)

    if not (args.check or args.shot or args.demo):
        install_file_log()

    if args.demo:
        args.dry_run = True
        args.no_preview = False

    if args.self_update:
        return self_update()
    if args.check:
        return check(args)
    if args.shot:
        return shot(args)
    ctl = Controller(args)
    ctl.state.open_settings = bool(args.settings)
    return ctl.run()


if __name__ == "__main__":
    raise SystemExit(main())
