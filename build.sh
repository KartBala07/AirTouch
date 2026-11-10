#!/bin/bash
# Build a shareable AirTouch.app + AirTouch.dmg + AirTouch.zip (Apple Silicon).
set -euo pipefail
cd "$(dirname "$0")"

# Use the project venv when present, otherwise whatever python is on PATH
# (CI installs requirements.txt into the runner's python).
PY="${PYTHON:-.venv/bin/python}"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3)"
fi

APP="dist/AirTouch.app"
PLIST="$APP/Contents/Info.plist"
VERSION="$("$PY" -c 'from airtouch.version import __version__; print(__version__)')"

echo "== 1/5  icon"
"$PY" tools/make_icon.py

echo "== 2/5  PyInstaller (AirTouch $VERSION, this takes a few minutes)"
"$PY" -m PyInstaller --noconfirm --clean --windowed --name AirTouch \
  --icon icon.icns \
  --osx-bundle-identifier com.bala.airtouch \
  --add-data "models/hand_landmarker.task:models" \
  --add-data "stubs/matplotlib:matplotlib" \
  --collect-all mediapipe \
  --exclude-module matplotlib \
  --exclude-module tkinter \
  airtouch_main.py

echo "== 3/5  trim the bundle, fix Info.plist, sign"
# OpenCV ships haar cascades and sample data we never load.
rm -rf "$APP/Contents/Frameworks/cv2/data" 2>/dev/null || true
find "$APP/Contents/Frameworks" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$APP/Contents/Frameworks" -name "*.pyi" -delete 2>/dev/null || true

plist_set() {  # plist_set key type value
  /usr/libexec/PlistBuddy -c "Set :$1 $3" "$PLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :$1 $2 $3" "$PLIST"
}
plist_set NSCameraUsageDescription string "AirTouch uses the camera to see your hand and fingertip. Video never leaves this Mac."
plist_set LSUIElement bool true
plist_set NSHighResolutionCapable bool true
plist_set CFBundleDisplayName string AirTouch
plist_set CFBundleShortVersionString string "$VERSION"
plist_set CFBundleVersion string "$VERSION"
plist_set NSHumanReadableCopyright string "AirTouch beta - MIT licensed"

codesign --force --deep --sign - "$APP"

echo "== 4/5  zip"
rm -f dist/AirTouch.zip
ditto -c -k --sequesterRsrc --keepParent "$APP" dist/AirTouch.zip

echo "== 5/5  DMG"
rm -rf dist/dmg
mkdir -p dist/dmg
cp -R "$APP" dist/dmg/
cp "share/ReadMe first.txt" dist/dmg/
ln -s /Applications dist/dmg/Applications
hdiutil create -volname "AirTouch" -srcfolder dist/dmg -ov -format UDZO dist/AirTouch.dmg >/dev/null

echo
echo "done (AirTouch $VERSION):"
du -sh "$APP" dist/AirTouch.zip dist/AirTouch.dmg
