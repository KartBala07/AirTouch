# AirTouch (beta 0.4)

Turn the MacBook camera into a touchscreen: the app tracks your index fingertip and
clicks wherever you point or poke. Tap twice quickly to select (double-click).

The whole UI is the menu-bar item plus one small cursor ring - there are no floating
panels. Everything it knows (hand seen, pointing, camera/track fps, tap/click counts,
Accessibility state, mode, calibration error) is printed live in the menu dropdown.

```
./run.sh                 start (menu-bar app + overlay)
./run.sh --calibrate     redo the 9-point calibration
./run.sh --dry-run       show the cursor, never click
./run.sh --check         diagnostics: camera, model, permissions, screen
./run.sh --demo          synthetic hand, no camera (UI smoke test)
./run.sh --no-motion-gate run MediaPipe on every frame (higher CPU)
./run.sh --settings       open the settings window on start
```

Quit from the menu bar item, or `pkill -f airtouch.main` / `Ctrl+C` in a terminal.

## Menu bar + settings

The app lives in the menu bar (no Dock icon). The menu is the status display:

```
AirTouch · air-trackpad
Hand: yes (pointing)   cam 30 / track 20 fps
Taps 12   Clicks 9   Double 2
Accessibility: granted
────────────────────────────
Enable Accessibility…        (only while it is missing)
✓ Pause tracking             ⌘P
✓ Show pointer ring
────────────────────────────
Settings…                    ⌘,
Recalibrate…
────────────────────────────
Quit AirTouch                ⌘Q
```

Pause releases the camera completely - no capture, no inference, no battery drain -
and resumes it when unpaused.

The settings panel changes everything live, no restart:

| control | what it does |
|---|---|
| Control mode | air-trackpad (default) or absolute (starts the 9-ring calibration) |
| Cursor speed | gain 1.0–5.0 for the air-trackpad |
| Tap sensitivity | how much the hand must grow to count as a poke (left = easier) |
| Pinch threshold | thumb–index distance ratio that counts as a pinch |
| Show pointer ring | the single on-screen overlay, on or off |
| Only respond to a pointing hand | ignore a fist or a hand that is holding something |
| Only track when something moves | MOG2 motion gate in front of MediaPipe |
| Single tap clicks | off = only a double-tap sends a click |
| Dry run | never post real mouse events (safe testing) |

Settings live in `~/Library/Application Support/AirTouch/config.json`; the log is
`~/Library/Application Support/AirTouch/airtouch.log`; calibration is
`~/Library/Application Support/AirTouch/calibration.json`.

## Two control modes

**Air-trackpad (default, no calibration).** Your fingertip moves the cursor like a
trackpad in the air: move right, the cursor goes right; sensitivity is `--gain`
(default 2.2). Tap to click, two quick taps to double-click. This is what runs on a
first launch, because absolute pointing with a hand held 30-50 cm from the screen is
inherently imprecise - the fingertip's image position depends on depth as much as on
where you aim.

**Absolute (touchscreen-like, needs calibration).** The fingertip is mapped onto the
screen by a 9-point homography, so *where you point* is *where the click lands*. Use
`--mode absolute --calibrate`. This only works well when the hand is close to the
screen plane (a hand-width from the glass); held further back, the fit is rejected
rather than silently mis-clicking.

```
./run.sh                        # auto: absolute if calibrated, else air-trackpad
./run.sh --mode relative        # force air-trackpad
./run.sh --mode absolute --calibrate
```

## Calibrating absolute mode (optional)

The app shows 9 rings, one at a time. **Reach your arm out toward the screen** (fingertip
roughly a hand-width from the glass) - this is the posture the calibration assumes, and
it is the posture you will use afterwards. Then for each ring:

1. Point the index fingertip at the ring and hold still for a moment.
2. **Poke forward** ~10 cm and back. That is a "tap". Pinching thumb+index also taps,
   and **N** (or a mouse click) marks the current finger position with no gesture.
3. Move to the next ring. Consecutive taps must be at least 60 camera-pixels apart,
   otherwise the app refuses the point and tells you to move further.

The camera thumbnail bottom-right shows a green dot for every recorded tap - the nine
dots should form a rough 3x3 grid. If they clump together, your hand is not travelling
across the screen and the app will reject the result.

After all 9 points the homography is fitted with RANSAC and sanity-checked (area,
orientation, horizon). Bad tap sets are refused with the reason instead of silently
producing a calibration that clicks in the wrong place. The result lands in
`calibration.json`.

Why "aim then poke" matters: poking throws the fingertip toward the camera, which shifts
its image position more than aiming does. The tap therefore reports the *median of the
pre-poke aim pose*, not the poke peak - that is what makes the 9 rings line up.

## While it runs

* The cursor only follows a **pointing** hand. A fist, a flat open palm, or a hand
  gripping a cup/phone/pen is ignored (thumb folded to the index base, or index
  curled). The HUD says *"hand up - point your index finger"* while that happens.
* A ring follows your fingertip (absolute mode also turns it light blue over the
  keyboard band, which is tracked but does nothing yet).
* Both overlays collapse: the small **–** chip at the top-right of the camera
  preview or the HUD shrinks it to a pill; click the pill to bring it back. The menu
  item *Minimize overlays* (`cmd+M`) does both at once, and the choice is remembered.
* Clicks in the top 26 px (menu bar / notch) are ignored on purpose.
* A HUD in the top-left corner shows: zone, hand scale ratio, camera/detection fps,
  calibration error, tap/click counters, and any warnings.
* A camera preview sits in the bottom-right: green quad = calibrated screen,
  blue band = keyboard zone, skeleton = tracked hand.
* Tap = click at the fingertip. Two taps within 0.6 s and 8 cm = double-click.

## Weight

Measured on this machine while tracking at 640x480:

| | before (0.2) | now (0.3) |
|---|---|---|
| capture | 1280x720 | 640x480 (4x fewer pixels) |
| inference | up to 30 fps | capped at 20 fps |
| preview pipeline | 20 fps resize + colour convert + CGImage | none while tracking |
| window server work | 2 live panels + HUD redraws | one 80x80 cursor |
| paused | camera still open | camera released |
| app bundle | 282 MB | ~240 MB (opencv-python-headless) |
| RSS | ~362 MB | ~300 MB |

Most of what is left is MediaPipe (111 MB) and OpenCV (119 MB). OpenCV is only used
for camera capture, a few array ops and the calibration homography - replacing it
with AVFoundation would cut roughly another 100 MB of bundle and 60 MB of RAM.

## Install for other people

Two ways in, both from the repo:

```bash
git clone https://github.com/KartBala07/AirTouch.git
cd AirTouch
./setup.sh          # venv + deps (+ hand model if missing)
./run.sh            # runs from source
```

or download `AirTouch.dmg` from the
[releases page](https://github.com/KartBala07/AirTouch/releases), drag it to
Applications, and allow Camera + Accessibility on first launch.

## Updates

The app has a **Check for Updates** item in its menu (it also checks once a day at
launch, switchable in Settings):

* **Downloaded .app** - it asks GitHub for the newest release, downloads the new
  `AirTouch.zip`, verifies the version inside the bundle, then a detached shell
  script waits for the app to exit, swaps the bundle in place and relaunches it.
  No work for the user beyond clicking the menu item.
* **Source checkout** - the same menu item becomes *Pull latest from GitHub* and runs
  `git pull --ff-only`; restart to apply.

Releasing a new version: bump `airtouch/version.py`, commit, then

```bash
git tag v0.4.1 && git push origin v0.4.1
```

GitHub Actions (`.github/workflows/release.yml`) runs the headless tests, builds the
app, and attaches `AirTouch.zip` + `AirTouch.dmg` to the release. Because both the
in-app updater and the workflow read the same tag, releases and updates stay in sync.

## Build a shareable app + DMG

```
./build.sh          # icon -> PyInstaller .app -> Info.plist -> ad-hoc sign -> AirTouch.dmg
```

Output: `dist/AirTouch.app` (~280 MB, includes Python, OpenCV, MediaPipe and the hand
model) and `dist/AirTouch.dmg`. The DMG contains the app plus an `Applications`
shortcut, so a recipient drags it in.

Notes for whoever receives it:

* **Apple Silicon only** (arm64 build). Intel Macs need their own build on Intel.
* The app is **ad-hoc signed, not notarized**. On first open macOS may say it cannot
  verify the developer: right-click the app > *Open* > *Open*, or
  `xattr -dr com.apple.quarantine /Applications/AirTouch.app`.
* First launch asks for **Camera** permission ("AirTouch would like to access the
  camera") - click Allow.
* Clicks need **Accessibility** permission: System Settings > Privacy & Security >
  Accessibility > enable AirTouch. Without it the app tracks and shows the cursor
  but clicks never reach the system (the HUD says so).
* Nothing is uploaded anywhere; the camera frames stay in memory.

## How it works

```
camera (AVFoundation, mirrored)
   -> MediaPipe HandLandmarker (21 landmarks, VIDEO mode)
   -> index fingertip + hand scale + pinch ratio
   -> TapDetector (push-toward-camera OR pinch, with refractory + stability gate)
   -> 9-point homography (screen uv <-> camera pixels) fitted with RANSAC
   -> CGEvent mouse move/click at the mapped point
```

Files:

| path | what |
|---|---|
| `airtouch/core.py` | camera, hand tracking, tap detection, calibration maths, clicking |
| `airtouch/ui.py` | AppKit overlay windows (cursor, HUD, preview, calibration) + menu bar + settings panel |
| `airtouch/config.py` | persistent settings (`config.json`) |
| `airtouch/paths.py` | resource vs. writable paths, frozen-app aware |
| `airtouch/main.py` | wiring, tracking loop, CLI, diagnostics |
| `tests/test_math.py` | headless checks for homography + gesture logic |
| `tools/probe_camera.py` | 4-second camera/hand-detection probe, saves a frame |
| `airtouch/updater.py` | release check, download, bundle swap, `git pull` |
| `build.sh` | builds `dist/AirTouch.app` + `.zip` + `.dmg` |
| `setup.sh` | venv + dependencies for a fresh clone |
| `stubs/matplotlib` | inert stub so MediaPipe's unused drawing helpers import without shipping matplotlib |

Credits: the motion gate in `core.py` is adapted from the MOG2 background-subtraction
pipeline in
[mattheweisenberg6/Python-Movement-Tracker](https://github.com/mattheweisenberg6/Python-Movement-Tracker)
(background subtraction + morphology + contour area filter). That repo detects *that*
something moved; MediaPipe HandLandmarker supplies the fingertip landmarks that make
cursor mapping and taps possible.

## Known limitations (beta)

* **The keyboard is not really visible to the camera.** The lid camera faces you,
  so the app cannot see the keys - "keyboard zone" just means the lower band of the
  camera view where your hand is when it drops to the keyboard. Taps there are
  counted, not typed. Synth keystrokes would be the next step.
* **Single display.** Mapping is calibrated to the main screen only.
* **Homography assumes a finger plane.** Reaching far to one side of the screen
  drifts a few cm; keep the hand at a consistent distance.
* Clicks are posted through CGEvent, so the app needs **Accessibility** permission
  (System Settings > Privacy & Security > Accessibility). The HUD warns when it is
  missing. Camera needs **Camera** permission for AirTouch.
* No global hotkey yet - stop it with `pkill`, or launch from a terminal and hit
  Ctrl+C.
* Works best in normal indoor light with the hand inside the frame; strong backlight
  kills the landmarker.

## Troubleshooting

| symptom | fix |
|---|---|
| `camera failed` / `camera_unavailable` | System Settings > Privacy & Security > Camera > enable AirTouch (or Terminal) |
| cursor moves but nothing is clicked | Accessibility permission; HUD shows `no accessibility` |
| clicks land away from the ring | redo `./run.sh --calibrate`, keep the same hand distance |
| taps fire while hovering | hold the hand still, or redo calibration further from the camera |
| double-click never triggers | two taps must be within 0.6 s and 8 cm |
