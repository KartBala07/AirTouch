AirTouch 0.2 (beta)
===================

1. Drag AirTouch into Applications.
2. First open: right-click AirTouch > Open > Open (the app is not notarized).
3. Allow Camera access when macOS asks.
4. Allow Accessibility: System Settings > Privacy & Security > Accessibility,
   enable "AirTouch". Without it the cursor moves but clicks never reach macOS.
   The menu bar item shows "Enable Accessibility..." while this is missing.
5. The app lives in the menu bar (hand icon). Click it for Settings, Pause, Quit.

Air-trackpad mode needs no calibration: move your index finger to move the
cursor, poke forward (or pinch thumb+index) to click, poke twice quickly to
double-click. Cursor speed and tap sensitivity are in Settings.

Absolute (touchscreen-like) mode maps where you point onto the screen, but needs
a 9-ring calibration and works best with your hand close to the glass.

Updates: the menu has "Check for Updates"; it downloads the newest release, swaps
the app in place and reopens it. macOS may ask for Camera access again after an
update (the builds are ad-hoc signed, so each release looks like a new app).

Apple Silicon only. Camera frames never leave the machine.
Log: ~/Library/Application Support/AirTouch/airtouch.log
