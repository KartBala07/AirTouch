#!/usr/bin/env python3
"""Checks for the update machinery (no network, no GUI)."""

import plistlib
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from airtouch import updater  # noqa: E402
from airtouch.version import __version__  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


check("version looks like semver", len(__version__.split(".")) == 3, __version__)

# ---------------------------------------------------------------- versions
check("parse plain", updater.parse_version("0.4.1") == (0, 4, 1))
check("parse v-prefix", updater.parse_version("v1.2.3") == (1, 2, 3))
check("parse short", updater.parse_version("0.5") == (0, 5, 0))
check("parse junk suffix", updater.parse_version("0.4.0-beta1") == (0, 4, 0))
check("newer minor", updater.is_newer("0.5.0", "0.4.9"))
check("newer patch", updater.is_newer("0.4.10", "0.4.9"))
check("same is not newer", not updater.is_newer("0.4.0", "0.4.0"))
check("older is not newer", not updater.is_newer("0.3.9", "0.4.0"))

# ---------------------------------------------------------------- swap script
script = updater.swap_script(Path("/Applications/AirTouch.app"), Path("/tmp/new/AirTouch.app"), 4242)
check("swap waits for the pid", "kill -0 4242" in script)
check("swap replaces the target", 'rm -rf "/Applications/AirTouch.app"' in script)
check("swap copies the staged app", 'ditto "/tmp/new/AirTouch.app"' in script)
check("swap relaunches", 'open "/Applications/AirTouch.app"' in script)
check("swap clears quarantine", "com.apple.quarantine" in script)

# ---------------------------------------------------------------- archive
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    src = tmp / "src"
    app = src / "AirTouch.app" / "Contents"
    app.mkdir(parents=True)
    (app / "Info.plist").write_bytes(
        plistlib.dumps(
            {"CFBundleShortVersionString": "9.9.9", "CFBundleIdentifier": "com.bala.airtouch"}
        )
    )
    archive = tmp / "AirTouch.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in (src / "AirTouch.app").rglob("*"):
            zf.write(path, path.relative_to(src))

    staged = updater.extract_archive(archive, tmp / "out")
    check("zip extract finds the app", staged is not None and staged.name == "AirTouch.app",
          str(staged))
    if staged is not None:
        check("staged bundle version readable", updater.bundle_version(staged) == "9.9.9",
              str(updater.bundle_version(staged)))

    bogus = tmp / "nothing.zip"
    with zipfile.ZipFile(bogus, "w") as zf:
        zf.writestr("readme.txt", "no app here")
    check("archive without .app is refused", updater.extract_archive(bogus, tmp / "out2") is None)

check("frozen detection is false here", updater.is_frozen() is False)
check("no bundle path in a checkout", updater.app_bundle() is None)

print()
if FAILS:
    print("FAILED:", ", ".join(FAILS))
    raise SystemExit(1)
print("all updater checks passed")
