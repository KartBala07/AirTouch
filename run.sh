#!/bin/bash
# AirTouch launcher. Keeps cwd at the project root so models/ resolves.
cd "$(dirname "$0")" || exit 1
exec "./.venv/bin/python" -m airtouch.main "$@"
