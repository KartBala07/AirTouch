#!/usr/bin/env python3
"""Entry point for PyInstaller (frozen .app) and plain `python airtouch_main.py`."""

from airtouch.main import main

if __name__ == "__main__":
    raise SystemExit(main())
