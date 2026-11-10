"""AppKit layer for AirTouch.

Deliberately UI-light: a menu-bar item that carries all live status, one small
cursor ring, and a full-screen calibration overlay shown only while calibrating.
No floating camera preview, no HUD window.
"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np
import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSBitmapImageFileTypePNG,
    NSButton,
    NSControlStateValueOff,
    NSControlStateValueOn,
    NSDeviceRGBColorSpace,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSGraphicsContext,
    NSImage,
    NSImageInterpolationLow,
    NSMakeRect,
    NSMakeSize,
    NSMenu,
    NSMenuItem,
    NSMutableParagraphStyle,
    NSObject,
    NSParagraphStyleAttributeName,
    NSPopUpButton,
    NSRunLoop,
    NSRunLoopCommonModes,
    NSScreen,
    NSScreenSaverWindowLevel,
    NSSlider,
    NSStatusBar,
    NSSwitchButton,
    NSTextAlignmentCenter,
    NSTextAlignmentLeft,
    NSTextAlignmentRight,
    NSTextField,
    NSTimer,
    NSVariableStatusItemLength,
    NSView,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
    NSColor,
)
from CoreFoundation import CFDataCreate
from Quartz import (
    CGColorSpaceCreateDeviceRGB,
    CGDataProviderCreateWithCFData,
    CGImageCreate,
    kCGImageAlphaNone,
    kCGRenderingIntentDefault,
)
from AppKit import NSString  # noqa: E402

from . import updater
from .version import __version__

ACCENT = (0.16, 0.78, 1.00)
GOOD = (0.30, 0.95, 0.55)
WARN = (1.00, 0.72, 0.25)
BAD = (1.00, 0.38, 0.38)


def color(r, g, b, a=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)


def main_screen_size():
    f = NSScreen.mainScreen().frame()
    return float(f.size.width), float(f.size.height)


def draw_text(s, x, y, w, h, size=11, col=None, bold=False, align="left"):
    font = NSFont.monospacedSystemFontOfSize_weight_(size, 0.55 if bold else 0.22)
    style = NSMutableParagraphStyle.alloc().init()
    style.setAlignment_(
        {
            "left": NSTextAlignmentLeft,
            "center": NSTextAlignmentCenter,
            "right": NSTextAlignmentRight,
        }[align]
    )
    attrs = {
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: col if col is not None else color(1, 1, 1, 0.92),
        NSParagraphStyleAttributeName: style,
    }
    NSString.stringWithString_(s).drawInRect_withAttributes_(NSMakeRect(x, y, w, h), attrs)


def rounded_rect(x, y, w, h, r, fill=None, stroke=None, line=1.0):
    path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(x, y, w, h), r, r)
    if fill is not None:
        fill.set()
        path.fill()
    if stroke is not None:
        stroke.set()
        path.setLineWidth_(line)
        path.stroke()


def image_from_rgb(rgb: np.ndarray) -> Optional[NSImage]:
    try:
        rgb = np.ascontiguousarray(rgb)
        h, w = rgb.shape[:2]
        data = rgb.tobytes()
        provider = CGDataProviderCreateWithCFData(CFDataCreate(None, data, len(data)))
        cg = CGImageCreate(
            w, h, 8, 24, w * 3,
            CGColorSpaceCreateDeviceRGB(), kCGImageAlphaNone, provider, None, False,
            kCGRenderingIntentDefault,
        )
        if cg is None:
            return None
        return NSImage.alloc().initWithCGImage_size_(cg, NSMakeSize(w, h))
    except Exception:
        return None


def new_window(x, y, w, h, level=NSScreenSaverWindowLevel, mouse=False):
    """Borderless transparent window placed by top-left screen coordinates."""
    sw, sh = main_screen_size()
    rect = NSMakeRect(x, sh - y - h, w, h)
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        rect, 0, NSBackingStoreBuffered, False
    )
    win.setLevel_(level)
    win.setOpaque_(False)
    win.setBackgroundColor_(NSColor.clearColor())
    win.setHasShadow_(False)
    win.setIgnoresMouseEvents_(not mouse)
    win.setReleasedWhenClosed_(False)
    win.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorStationary
        | NSWindowCollectionBehaviorFullScreenAuxiliary
    )
    return win


def place_window(win, x, y, w, h):
    sw, sh = main_screen_size()
    win.setFrame_display_(NSMakeRect(x, sh - y - h, w, h), True)
    view = win.contentView()
    if view is not None:
        view.setFrame_(NSMakeRect(0, 0, w, h))
        view.setNeedsDisplay_(True)


# --------------------------------------------------------------------------
# views
# --------------------------------------------------------------------------
class BaseView(NSView):
    def drawRect_(self, rect):
        # A Python exception escaping drawRect becomes an ObjC exception and
        # kills the process - trap it so a drawing bug can never quit the app.
        try:
            self.paint_(rect)
        except Exception:
            import sys
            import traceback

            traceback.print_exc(file=sys.stderr)
            sys.stderr.write("[airtouch] draw error above\n")
            sys.stderr.flush()

    def paint_(self, rect):
        pass

    def isFlipped(self):
        return True

    def acceptsFirstResponder(self):
        return True

    def acceptsFirstMouse_(self, event):
        return True


class CursorView(BaseView):
    flash_t = 0.0
    mode = "idle"

    def paint_(self, rect):
        b = self.bounds()
        cx, cy = b.size.width / 2.0, b.size.height / 2.0

        col = ACCENT
        if self.mode == "keyboard":
            col = (0.55, 0.65, 1.0)

        halo = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - 16, cy - 16, 32, 32))
        color(*col, 0.13).set()
        halo.setLineWidth_(7.0)
        halo.stroke()

        ring = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - 9.5, cy - 9.5, 19, 19))
        color(1, 1, 1, 0.92).set()
        ring.setLineWidth_(1.6)
        ring.stroke()

        dot = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - 3, cy - 3, 6, 6))
        color(*col, 0.98).set()
        dot.fill()

        age = time.monotonic() - self.flash_t
        if 0.0 <= age <= 0.42:
            p = age / 0.42
            r = 13 + 24 * p
            flash = NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(cx - r, cy - r, 2 * r, 2 * r)
            )
            color(*GOOD, 0.55 * (1 - p)).set()
            flash.setLineWidth_(2.6)
            flash.stroke()


class CalibrationView(BaseView):
    state = None
    target = (0.5, 0.5)
    index = 0
    total = 9
    hint = ""
    done = False
    error = ""

    def paint_(self, rect):
        b = self.bounds()
        w, h = b.size.width, b.size.height

        color(0.02, 0.03, 0.05, 0.86).set()
        NSBezierPath.fillRect_(NSMakeRect(0, 0, w, h))

        if self.done:
            draw_text("Calibration saved", 0, h / 2 - 44, w, 36, size=26,
                      bold=True, align="center", col=color(*GOOD))
            draw_text(self.hint, 0, h / 2 + 2, w, 26, size=13, align="center")
            return

        tx = self.target[0] * w
        ty = self.target[1] * h

        disc = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(tx - 78, ty - 78, 156, 156))
        color(0.0, 0.0, 0.0, 0.72).set()
        disc.fill()

        pulse = 0.5 + 0.5 * np.sin(time.monotonic() * 3.4)
        outer = 40 + 10 * pulse
        p = NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(tx - outer, ty - outer, 2 * outer, 2 * outer)
        )
        color(*ACCENT, 0.55).set()
        p.setLineWidth_(1.5)
        p.stroke()

        ring = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(tx - 26, ty - 26, 52, 52))
        color(1, 1, 1, 0.95).set()
        ring.setLineWidth_(2.6)
        ring.stroke()

        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            seg = NSBezierPath.bezierPath()
            seg.moveToPoint_((tx + dx * 26, ty + dy * 26))
            seg.lineToPoint_((tx + dx * 46, ty + dy * 46))
            seg.setLineWidth_(1.4)
            color(1, 1, 1, 0.45).set()
            seg.stroke()

        dot = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(tx - 4, ty - 4, 8, 8))
        color(*ACCENT, 1.0).set()
        dot.fill()

        tip = getattr(self.state, "calib_tip", None)
        if tip is not None:
            fx, fy = tip[0] * w, tip[1] * h
            line = NSBezierPath.bezierPath()
            line.moveToPoint_((fx, fy))
            line.lineToPoint_((tx, ty))
            line.setLineWidth_(1.2)
            color(*WARN, 0.45).set()
            line.stroke()
            fp = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(fx - 9, fy - 9, 18, 18))
            color(*WARN, 0.95).set()
            fp.fill()
            draw_text("you", fx + 14, fy - 8, 60, 16, size=11, col=color(*WARN))

        draw_text(f"AirTouch calibration   {self.index + 1} / {self.total}", 0, 46, w, 34,
                  size=25, bold=True, align="center")
        draw_text("Reach toward the screen, point at the ring, then poke forward",
                  0, 86, w, 24, size=15, align="center", col=color(1, 1, 1, 0.85))
        draw_text(self.hint, 0, h - 104, w, 26, size=13.5, align="center",
                  col=color(1, 1, 1, 0.78))
        draw_text("push = tap   \u00b7   pinch = tap   \u00b7   N = mark   \u00b7   esc = quit",
                  0, h - 70, w, 20, size=12, align="center", col=color(1, 1, 1, 0.52))
        if self.error:
            draw_text(self.error, 0, h - 40, w, 22, size=13, align="center", col=color(*BAD))

        # camera thumbnail (only exists while calibrating)
        rgb = getattr(self.state, "preview_rgb", None)
        if rgb is not None:
            pw, ph = 320, 180
            px, py = w - pw - 28, h - ph - 28
            img = image_from_rgb(rgb)
            if img is not None:
                rounded_rect(px - 6, py - 6, pw + 12, ph + 12, 10,
                             fill=color(0, 0, 0, 0.55), stroke=color(1, 1, 1, 0.14))
                NSGraphicsContext.currentContext().setImageInterpolation_(NSImageInterpolationLow)
                img.drawInRect_(NSMakeRect(px, py, pw, ph))
                for nx, ny in getattr(self.state, "calib_map", []):
                    mx, my = px + nx * pw, py + ny * ph
                    d = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(mx - 3.5, my - 3.5, 7, 7))
                    color(*GOOD, 0.95).set()
                    d.fill()

    def keyDown_(self, event):
        try:
            chars = (event.charactersIgnoringModifiers() or "").lower()
        except Exception:
            chars = ""
        if event.keyCode() == 53:  # esc
            self.state.quit = True
        elif chars == "n":
            self.state.calib_register = True
            self.state.calib_dirty = True
        else:
            objc.super(CalibrationView, self).keyDown_(event)

    def mouseDown_(self, event):
        self.state.calib_register = True
        self.state.calib_dirty = True


class CalibrationWindow(NSWindow):
    def canBecomeKeyWindow(self):
        return True

    def canBecomeMainWindow(self):
        return True


class CalibrationFullView(CalibrationView):
    pass


# --------------------------------------------------------------------------
# overlay: the cursor ring only
# --------------------------------------------------------------------------
class Overlay(NSObject):
    state = None
    mode = "live"
    shell = None
    CURSOR = 80

    def initWithState_(self, state):
        self = objc.super(Overlay, self).init()
        if self is None:
            return None
        self.state = state
        self.mode = state.mode
        self._last_flash = 0.0
        self._last_shell = 0.0
        self._last_calib_draw = 0.0

        sw, sh = main_screen_size()
        self.screen_w, self.screen_h = sw, sh

        self.cursor_win = new_window(0, 0, self.CURSOR, self.CURSOR)
        self.cursor_view = CursorView.alloc().initWithFrame_(
            NSMakeRect(0, 0, self.CURSOR, self.CURSOR)
        )
        self.cursor_win.setContentView_(self.cursor_view)

        self.calib_win = CalibrationWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, sw, sh), 0, NSBackingStoreBuffered, False
        )
        self.calib_win.setLevel_(NSScreenSaverWindowLevel + 1)
        self.calib_win.setOpaque_(False)
        self.calib_win.setBackgroundColor_(NSColor.clearColor())
        self.calib_win.setHasShadow_(False)
        self.calib_win.setReleasedWhenClosed_(False)
        self.calib_win.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        self.calib_view = CalibrationFullView.alloc().initWithFrame_(NSMakeRect(0, 0, sw, sh))
        self.calib_view.state = state
        self.calib_view.total = state.calib_total
        self.calib_win.setContentView_(self.calib_view)
        return self

    # ------------------------------------------------------------------
    def start(self):
        if getattr(self.state, "show_cursor", True):
            self.cursor_win.orderFrontRegardless()
        if self.state.mode == "calibrate":
            self._set_mode("calibrate")
        timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / 60.0, self, "tick:", None, True
        )
        NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
        if self.shell is not None:
            self.shell.start()
            if getattr(self.state, "open_settings", False):
                self.shell.show_settings()

    # ------------------------------------------------------------------
    def tick_(self, timer):
        st = self.state
        now = time.monotonic()

        if st.mode != self.mode:
            self._set_mode(st.mode)

        if self.mode == "calibrate":
            self.calib_view.index = st.calib_index
            self.calib_view.target = st.calib_target
            self.calib_view.hint = st.calib_hint
            self.calib_view.done = st.calib_done
            self.calib_view.error = st.calib_error
            if st.calib_dirty or (now - self._last_calib_draw) > 0.05:
                st.calib_dirty = False
                self._last_calib_draw = now
                self.calib_view.setNeedsDisplay_(True)
            return

        tip = st.tip_screen
        want_cursor = bool(getattr(st, "show_cursor", True)) and tip is not None
        if want_cursor:
            x, y = tip
            self.cursor_win.setFrameOrigin_(
                (x - self.CURSOR / 2.0, self.screen_h - y - self.CURSOR / 2.0)
            )
            self.cursor_view.mode = "keyboard" if st.zone == "keyboard" else "screen"
            if not self.cursor_win.isVisible():
                self.cursor_win.orderFrontRegardless()
        elif self.cursor_win.isVisible():
            self.cursor_win.orderOut_(None)

        if st.flash_t != self._last_flash:
            self._last_flash = st.flash_t
            self.cursor_view.flash_t = st.flash_t
            self.cursor_view.setNeedsDisplay_(True)

        if self.shell is not None and now - self._last_shell > 0.25:
            self._last_shell = now
            try:
                self.shell.refresh()
            except Exception:
                pass

    # ------------------------------------------------------------------
    def set_cursor_visible(self, visible):
        self.state.show_cursor = bool(visible)
        if not self.state.show_cursor:
            self.cursor_win.orderOut_(None)
        elif self.state.tip_screen is not None:
            self.cursor_win.orderFrontRegardless()

    # ------------------------------------------------------------------
    def _set_mode(self, mode):
        self.mode = mode
        app = NSApplication.sharedApplication()
        if mode == "calibrate":
            self.cursor_win.orderOut_(None)
            app.setActivationPolicy_(0)
            app.activateIgnoringOtherApps_(True)
            self.calib_win.makeKeyAndOrderFront_(None)
            self.calib_win.makeFirstResponder_(self.calib_view)
        else:
            self.calib_win.orderOut_(None)
            app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
            if getattr(self.state, "show_cursor", True):
                self.cursor_win.orderFrontRegardless()


# --------------------------------------------------------------------------
# menu bar + settings panel
# --------------------------------------------------------------------------
OPTION_ROWS = [
    ("require_pointing", "Only respond to a pointing hand (ignore fists / holding things)"),
    ("check_updates", "Check for updates at launch"),
    ("show_cursor", "Show pointer ring"),
    ("motion_gate", "Only track when something moves (saves CPU)"),
    ("click_on_single", "Single tap clicks (off = only double-tap clicks)"),
    ("dry_run", "Dry run - never send real clicks"),
]


def _label(text, x, y, w, size=12.5, bold=False, secondary=False):
    field = NSTextField.labelWithString_(text)
    field.setFrame_(NSMakeRect(x, y, w, 18))
    field.setFont_(NSFont.systemFontOfSize_weight_(size, 0.55 if bold else 0.0))
    if secondary:
        field.setTextColor_(NSColor.secondaryLabelColor())
    return field


class SettingsContent(BaseView):
    def paint_(self, rect):
        NSColor.windowBackgroundColor().set()
        NSBezierPath.fillRect_(self.bounds())


def menu_item(target, title, action, key, enabled=True):
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
    item.setTarget_(target)
    item.setEnabled_(enabled)
    return item


class AppShell(NSObject):
    """Menu bar item (all live status lives here) + the settings panel."""

    controller = None
    state = None
    config = None
    overlay = None
    settings_window = None
    _status_label = None
    _value_labels = {}
    _popup = None
    _sliders = {}
    _update = {"state": "idle", "msg": "", "release": None}

    def initWithController_(self, controller):
        self = objc.super(AppShell, self).init()
        if self is None:
            return None
        self.controller = controller
        self.state = controller.state
        self.config = controller.config
        self.overlay = getattr(controller, "overlay", None)
        self._option_keys = [row[0] for row in OPTION_ROWS]
        return self

    # -- status item ----------------------------------------------------
    def start(self):
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        button = self.item.button()
        try:
            img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                "hand.point.up.left.fill", "AirTouch"
            )
        except Exception:
            img = None
        if img is not None:
            img.setTemplate_(True)
            button.setImage_(img)
            button.setToolTip_("AirTouch")
        else:
            button.setTitle_("AT")

        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        self.title_item = menu_item(self, f"AirTouch {__version__}", None, "", enabled=False)
        self.hand_item = menu_item(self, "Hand: -", None, "", enabled=False)
        self.counts_item = menu_item(self, "Taps 0   Clicks 0   Double 0", None, "", enabled=False)
        self.acc_item = menu_item(self, "Accessibility: checking", None, "", enabled=False)
        for item in (self.title_item, self.hand_item, self.counts_item, self.acc_item):
            menu.addItem_(item)
        menu.addItem_(NSMenuItem.separatorItem())

        self.ax_item = menu_item(self, "Enable Accessibility\u2026", "enableAccessibility:", "")
        menu.addItem_(self.ax_item)
        self.pause_item = menu_item(self, "Pause tracking", "togglePause:", "p")
        menu.addItem_(self.pause_item)
        self.cursor_item = menu_item(self, "Show pointer ring", "toggleCursor:", "")
        menu.addItem_(self.cursor_item)
        menu.addItem_(NSMenuItem.separatorItem())
        self.update_item = menu_item(self, "Check for Updates\u2026", "updateAction:", "")
        menu.addItem_(self.update_item)
        menu.addItem_(menu_item(self, "Settings\u2026", "openSettings:", ","))
        menu.addItem_(menu_item(self, "Recalibrate\u2026", "recalibrate:", ""))
        menu.addItem_(NSMenuItem.separatorItem())
        menu.addItem_(menu_item(self, "Quit AirTouch", "quitApp:", "q"))

        self.item.setMenu_(menu)
        self.refresh()
        self.auto_check()

    # -- updates --------------------------------------------------------
    def updateAction_(self, sender=None):
        if not updater.is_frozen():
            self._run_update_worker(self._pull_worker)
            return
        state = self._update.get("state")
        if state == "offer":
            self._run_update_worker(self._install_worker)
        elif state != "checking":
            self._run_update_worker(self._check_worker)

    def _run_update_worker(self, worker):
        import threading

        self._update = {"state": "checking", "msg": "checking\u2026", "release": None}
        self.refresh()
        threading.Thread(target=worker, daemon=True).start()

    def _check_worker(self):
        state, msg, release = updater.check()
        self._update = {"state": state, "msg": msg, "release": release}
        self.config.set("last_update_check", time.time())

    def _pull_worker(self):
        ok, msg = updater.git_update()
        self._update = {
            "state": "current" if ok else "error",
            "msg": msg,
            "release": None,
        }

    def _install_worker(self):
        release = self._update.get("release")
        if release is None:
            self._check_worker()
            return
        self._update = {"state": "installing", "msg": f"installing {release.version}\u2026", "release": release}
        ok, msg = updater.install_release(release)
        self._update = {
            "state": "installing" if ok else "error",
            "msg": msg,
            "release": release,
        }
        if ok:
            # the swap script waits for this pid, so quit once it is staged
            time.sleep(0.6)
            self.state.quit = True

    def auto_check(self):
        if not updater.is_frozen():
            return
        if not self.config.get("check_updates"):
            return
        last = float(self.config.get("last_update_check") or 0.0)
        if time.time() - last < 86400:
            return
        self._run_update_worker(self._check_worker)

    # -- actions --------------------------------------------------------
    def togglePause_(self, sender=None):
        self.state.paused = not self.state.paused
        self.refresh()

    def toggleCursor_(self, sender=None):
        show = not bool(getattr(self.state, "show_cursor", True))
        self.state.show_cursor = show
        self.config.set("show_cursor", show)
        if self.overlay is not None:
            self.overlay.set_cursor_visible(show)
        self._sync_controls()
        self.refresh()

    def enableAccessibility_(self, sender=None):
        self.controller.clicker.request_trust()
        import subprocess

        subprocess.run(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"],
            check=False,
        )

    def recalibrate_(self, sender=None):
        self.controller.start_calibration()
        self.close_settings()

    def quitApp_(self, sender=None):
        self.state.quit = True

    def openSettings_(self, sender=None):
        self.show_settings()

    def modeChanged_(self, sender):
        self.config.set("mode", "absolute" if sender.indexOfSelectedItem() == 1 else "relative")
        if self.config.get("mode") == "absolute" and self.controller.calib is None:
            self.recalibrate_()
            return
        self.controller.apply_settings()
        self.refresh()

    def gainChanged_(self, sender):
        value = round(float(sender.doubleValue()), 2)
        self.config.set("gain", value)
        self._value_labels["gain"].setStringValue_(f"{value:.1f}x")
        self.controller.apply_settings()

    def sensitivityChanged_(self, sender):
        threshold = round(float(sender.doubleValue()), 4)
        self.config.set("push_threshold", threshold)
        self._value_labels["sens"].setStringValue_(f"{(threshold - 1) * 100:.1f}% push")
        self.controller.apply_settings()

    def pinchChanged_(self, sender):
        value = round(float(sender.doubleValue()), 3)
        self.config.set("pinch_threshold", value)
        self._value_labels["pinch"].setStringValue_(f"{value:.2f} ratio")
        self.controller.apply_settings()

    def optionChanged_(self, sender):
        idx = int(sender.tag())
        if not (0 <= idx < len(self._option_keys)):
            return
        key = self._option_keys[idx]
        on = bool(sender.state() == NSControlStateValueOn)
        self.config.set(key, on)
        self.controller.apply_settings()
        self._sync_controls()
        self.refresh()

    def showSystemSettings_(self, sender=None):
        import subprocess

        subprocess.run(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera"],
            check=False,
        )

    # -- settings window ------------------------------------------------
    def buildSettingsView_(self, size):
        width, height = size
        view = SettingsContent.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
        y = 18

        view.addSubview_(_label("Control mode", 24, y + 3, 130, 13))
        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(170, y - 3, 276, 26), False
        )
        popup.addItemsWithTitles_(
            ["Air-trackpad (no calibration)", "Absolute (point at the screen)"]
        )
        popup.setTarget_(self)
        popup.setAction_("modeChanged:")
        view.addSubview_(popup)
        self._popup = popup
        y += 44

        view.addSubview_(_label("Cursor speed", 24, y + 3, 130, 13))
        slider = NSSlider.alloc().initWithFrame_(NSMakeRect(170, y - 3, 190, 24))
        slider.setMinValue_(1.0)
        slider.setMaxValue_(5.0)
        slider.setContinuous_(True)
        slider.setTarget_(self)
        slider.setAction_("gainChanged:")
        view.addSubview_(slider)
        self._sliders["gain"] = slider
        self._value_labels["gain"] = _label("2.2x", 366, y + 3, 80, 12, secondary=True)
        view.addSubview_(self._value_labels["gain"])
        y += 38

        view.addSubview_(_label("Tap sensitivity", 24, y + 3, 130, 13))
        slider = NSSlider.alloc().initWithFrame_(NSMakeRect(170, y - 3, 190, 24))
        slider.setMinValue_(1.025)
        slider.setMaxValue_(1.10)
        slider.setContinuous_(True)
        slider.setTarget_(self)
        slider.setAction_("sensitivityChanged:")
        view.addSubview_(slider)
        self._sliders["sens"] = slider
        self._value_labels["sens"] = _label("5.0% push", 366, y + 3, 90, 12, secondary=True)
        view.addSubview_(self._value_labels["sens"])
        view.addSubview_(_label("left = easier to trigger", 170, y + 20, 190, 11, secondary=True))
        y += 44

        view.addSubview_(_label("Pinch threshold", 24, y + 3, 130, 13))
        slider = NSSlider.alloc().initWithFrame_(NSMakeRect(170, y - 3, 190, 24))
        slider.setMinValue_(0.15)
        slider.setMaxValue_(0.55)
        slider.setContinuous_(True)
        slider.setTarget_(self)
        slider.setAction_("pinchChanged:")
        view.addSubview_(slider)
        self._sliders["pinch"] = slider
        self._value_labels["pinch"] = _label("0.34 ratio", 366, y + 3, 90, 12, secondary=True)
        view.addSubview_(self._value_labels["pinch"])
        y += 40

        view.addSubview_(_label("Behaviour", 24, y, 200, size=10.5, secondary=True))
        y += 20
        for idx, (key, title) in enumerate(OPTION_ROWS):
            box = NSButton.alloc().initWithFrame_(NSMakeRect(24, y, width - 48, 22))
            box.setButtonType_(NSSwitchButton)
            box.setTitle_(title)
            box.setFont_(NSFont.systemFontOfSize_(12.5))
            box.setTag_(idx)
            box.setState_(NSControlStateValueOn if self.config.get(key) else NSControlStateValueOff)
            box.setTarget_(self)
            box.setAction_("optionChanged:")
            view.addSubview_(box)
            self._value_labels["opt_" + key] = box
            y += 26

        y += 6
        rec = NSButton.alloc().initWithFrame_(NSMakeRect(24, y, 140, 30))
        rec.setBezelStyle_(1)
        rec.setTitle_("Recalibrate\u2026")
        rec.setTarget_(self)
        rec.setAction_("recalibrate:")
        view.addSubview_(rec)

        perm = NSButton.alloc().initWithFrame_(NSMakeRect(172, y, 150, 30))
        perm.setBezelStyle_(1)
        perm.setTitle_("Camera settings\u2026")
        perm.setTarget_(self)
        perm.setAction_("showSystemSettings:")
        view.addSubview_(perm)

        quit_btn = NSButton.alloc().initWithFrame_(NSMakeRect(width - 100, y, 76, 30))
        quit_btn.setBezelStyle_(1)
        quit_btn.setTitle_("Quit")
        quit_btn.setTarget_(self)
        quit_btn.setAction_("quitApp:")
        view.addSubview_(quit_btn)

        self._status_label = _label("", 24, height - 56, width - 48, 11, secondary=True)
        self._status_label.setFrame_(NSMakeRect(24, height - 56, width - 48, 36))
        view.addSubview_(self._status_label)
        return view

    def show_settings(self):
        if self.settings_window is None:
            width, height = 470, 470
            style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
            win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, width, height), style, NSBackingStoreBuffered, False
            )
            win.setTitle_("AirTouch Settings")
            win.setReleasedWhenClosed_(False)
            win.setDelegate_(self)
            win.setContentView_(self.buildSettingsView_((width, height)))
            win.center()
            self.settings_window = win

        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._sync_controls()
        self.settings_window.makeKeyAndOrderFront_(None)
        self.refresh()

    def close_settings(self):
        if self.settings_window is not None:
            self.settings_window.orderOut_(None)

    def windowWillClose_(self, note):
        try:
            NSApplication.sharedApplication().setActivationPolicy_(
                NSApplicationActivationPolicyAccessory
            )
        except Exception:
            pass

    def _sync_controls(self):
        st, cfg = self.state, self.config
        if self._popup is not None:
            self._popup.selectItemAtIndex_(
                1 if (cfg.get("mode") == "absolute" or st.control == "absolute") else 0
            )
        if "gain" in self._sliders:
            gain = float(cfg.get("gain"))
            self._sliders["gain"].setDoubleValue_(gain)
            self._value_labels["gain"].setStringValue_(f"{gain:.1f}x")
        if "sens" in self._sliders:
            thr = float(cfg.get("push_threshold"))
            self._sliders["sens"].setDoubleValue_(thr)
            self._value_labels["sens"].setStringValue_(f"{(thr - 1) * 100:.1f}% push")
        if "pinch" in self._sliders:
            val = float(cfg.get("pinch_threshold"))
            self._sliders["pinch"].setDoubleValue_(val)
            self._value_labels["pinch"].setStringValue_(f"{val:.2f} ratio")
        for key, _title in OPTION_ROWS:
            box = self._value_labels.get("opt_" + key)
            if box is not None:
                box.setState_(NSControlStateValueOn if cfg.get(key) else NSControlStateValueOff)

    # -- live refresh (menu titles) --------------------------------------
    def refresh(self):
        st = self.state
        mode = "air-trackpad" if st.control == "relative" else "absolute"
        title = f"AirTouch {__version__} \u00b7 {mode}"
        if st.paused:
            title += " \u00b7 paused"
        self.title_item.setTitle_(title)

        self._refresh_update_item()

        hand = "yes" if st.hand else "no"
        pointing = "pointing" if st.pointing else ("not pointing" if st.hand else "-")
        self.hand_item.setTitle_(
            f"Hand: {hand} ({pointing})   cam {st.cam_fps:.0f} / track {st.det_fps:.0f} fps"
        )
        self.counts_item.setTitle_(
            f"Taps {st.taps}   Clicks {st.clicks}   Double {st.doubles}"
        )
        self.acc_item.setTitle_(
            "Accessibility: granted" if st.accessibility
            else "Accessibility: off - clicks blocked"
        )
        self.ax_item.setHidden_(bool(st.accessibility))

        for item, on in (
            (self.pause_item, st.paused),
            (self.cursor_item, getattr(st, "show_cursor", True)),
        ):
            item.setState_(NSControlStateValueOn if on else NSControlStateValueOff)

        if (
            self._status_label is not None
            and self.settings_window is not None
            and self.settings_window.isVisible()
        ):
            acc = "accessibility ok" if st.accessibility else "no accessibility - clicks blocked"
            self._status_label.setStringValue_(
                f"{st.status_txt}  \u00b7  camera {st.cam_fps:.0f} fps  \u00b7  tracking {st.det_fps:.0f} fps\n"
                f"taps {st.taps}   clicks {st.clicks}   double {st.doubles}   {acc}"
            )


    # ------------------------------------------------------------------
    def _refresh_update_item(self):
        info = self._update or {}
        state = info.get("state", "idle")
        release = info.get("release")
        version = getattr(release, "version", "") if release is not None else ""

        if not updater.is_frozen():
            self.update_item.setTitle_("Pull latest from GitHub")
            self.update_item.setEnabled_(state != "checking")
            return

        if state == "idle":
            self.update_item.setTitle_("Check for Updates\u2026")
            self.update_item.setEnabled_(True)
        elif state == "checking":
            self.update_item.setTitle_("Checking for updates\u2026")
            self.update_item.setEnabled_(False)
        elif state == "available":
            self.update_item.setTitle_(f"Update to {version} \u2014 install now")
            self.update_item.setEnabled_(True)
        elif state == "current":
            self.update_item.setTitle_(f"Up to date ({version or __version__})")
            self.update_item.setEnabled_(False)
        elif state == "none":
            self.update_item.setTitle_("No releases published yet")
            self.update_item.setEnabled_(True)
        elif state == "installing":
            self.update_item.setTitle_("Installing update\u2026")
            self.update_item.setEnabled_(False)
        else:
            msg = (info.get("msg") or "update failed")[:44]
            self.update_item.setTitle_(f"Update check failed \u2014 {msg}")
            self.update_item.setEnabled_(True)


class AppDelegate(NSObject):
    overlay = None

    def initWithOverlay_(self, overlay):
        self = objc.super(AppDelegate, self).init()
        if self is None:
            return None
        self.overlay = overlay
        return self

    def applicationDidFinishLaunching_(self, note):
        if self.overlay is not None:
            self.overlay.start()

    def applicationShouldTerminateAfterLastWindowClosed_(self, sender):
        return False


def render_view(view, path, scale=1.0, backdrop=None):
    """Render a view to PNG offscreen (used by --shot self-check)."""
    from AppKit import NSBitmapImageRep

    b = view.bounds()
    w = int(b.size.width * scale)
    h = int(b.size.height * scale)
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, w, h, 8, 4, True, False, NSDeviceRGBColorSpace, w * 4, 32
    )
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)
    ctx.setShouldAntialias_(True)
    if backdrop is not None:
        backdrop.drawInRect_(b)
    view.displayRectIgnoringOpacity_inContext_(b, ctx)
    NSGraphicsContext.restoreGraphicsState()
    data = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
    data.writeToFile_atomically_(str(path), True)
    return str(path)
