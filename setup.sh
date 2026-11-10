#!/bin/bash
# One-shot developer setup: venv + dependencies (+ model if it is missing).
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip >/dev/null
./.venv/bin/python -m pip install -r requirements.txt

if [ ! -f models/hand_landmarker.task ]; then
  echo "downloading the hand model..."
  mkdir -p models
  curl -fsSL -o models/hand_landmarker.task \
    https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task
fi

echo
echo "ready:"
echo "  ./run.sh --check      # verify camera + permissions"
echo "  ./run.sh              # run"
echo "  ./build.sh            # build dist/AirTouch.app + .zip + .dmg"
