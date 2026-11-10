#!/bin/bash
# Build the current version and publish it as a GitHub release.
#   ./release.sh                  -> uses "AirTouch <version>" as the notes
#   ./release.sh notes.md         -> uses that file as the release notes
# Bump airtouch/version.py first; the tag comes from it.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
VERSION="$("$PY" -c 'from airtouch.version import __version__; print(__version__)')"
TAG="v$VERSION"

if gh release view "$TAG" >/dev/null 2>&1; then
  echo "release $TAG already exists - bump airtouch/version.py first" >&2
  exit 1
fi

./build.sh

echo "== publishing $TAG"
if [ "${1:-}" != "" ] && [ -f "${1:-}" ]; then
  gh release create "$TAG" dist/AirTouch.zip dist/AirTouch.dmg \
    --target main --title "AirTouch $VERSION" --notes-file "$1"
else
  gh release create "$TAG" dist/AirTouch.zip dist/AirTouch.dmg \
    --target main --title "AirTouch $VERSION" \
    --notes "AirTouch $VERSION - grab AirTouch.dmg to install, or AirTouch.zip to let the in-app updater use it."
fi
echo
echo "done: $(gh release view "$TAG" --json url -q .url)"
