"""Update checking and self-update.

Two modes, picked automatically:

* frozen .app  -> ask the GitHub releases API, download the newest .zip/.dmg,
  then hand off to a detached shell script that swaps the bundle once this
  process exits and relaunches it.
* source checkout -> plain ``git pull --ff-only``; restarting applies it.
"""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from .version import REPO, __version__

API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
CHECK_TIMEOUT = 8.0
DOWNLOAD_TIMEOUT = 180.0


@dataclass
class Release:
    version: str
    url: str
    page: str = ""
    notes: str = ""
    published: str = ""


# --------------------------------------------------------------------------
# version helpers
# --------------------------------------------------------------------------
def parse_version(value: str) -> Tuple[int, int, int]:
    core = str(value).strip().lstrip("vV").split("-")[0].split("+")[0]
    bits = []
    for part in core.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        bits.append(int(digits or 0))
    while len(bits) < 3:
        bits.append(0)
    return tuple(bits[:3])  # type: ignore[return-value]


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


# --------------------------------------------------------------------------
# environment
# --------------------------------------------------------------------------
def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_bundle() -> Optional[Path]:
    """The running .app bundle, or None when running from a checkout."""
    if not is_frozen():
        return None
    path = Path(sys.executable).resolve()
    for parent in path.parents:
        if parent.suffix == ".app":
            return parent
    return None


def source_root() -> Path:
    return Path(__file__).resolve().parent.parent


def bundle_version(app: Path) -> Optional[str]:
    try:
        with open(app / "Contents" / "Info.plist", "rb") as fh:
            return str(plistlib.load(fh).get("CFBundleShortVersionString") or "")
    except Exception:
        return None


# --------------------------------------------------------------------------
# network
# --------------------------------------------------------------------------
def _fetch(url: str, timeout: float = CHECK_TIMEOUT) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"AirTouch/{__version__}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def latest_release() -> Optional[Release]:
    """Newest published release, or None when the repo has no releases yet."""
    try:
        payload = json.loads(_fetch(API_LATEST))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    except urllib.error.URLError:
        raise

    assets = payload.get("assets") or []
    chosen = None
    for suffix in (".zip", ".dmg"):
        for asset in assets:
            if str(asset.get("name", "")).lower().endswith(suffix):
                chosen = asset
                break
        if chosen is not None:
            break
    if chosen is None:
        return None
    return Release(
        version=str(payload.get("tag_name", "")).lstrip("vV"),
        url=chosen["browser_download_url"],
        page=str(payload.get("html_url", "")),
        notes=str(payload.get("body") or "")[:800],
        published=str(payload.get("published_at", "")),
    )


# --------------------------------------------------------------------------
# applying updates
# --------------------------------------------------------------------------
def swap_script(target: Path, staged: Path, pid: int) -> str:
    """Shell that waits for this process to exit, swaps the bundle, relaunches."""
    return f"""#!/bin/sh
# written by AirTouch's updater
for _ in $(seq 1 240); do
  kill -0 {pid} 2>/dev/null || break
  sleep 0.25
done
rm -rf "{target}"
ditto "{staged}" "{target}" || exit 1
xattr -dr com.apple.quarantine "{target}" 2>/dev/null
sleep 1
open -n "{target}"
"""


def extract_archive(archive: Path, into: Path) -> Optional[Path]:
    """Unpack a .zip or .dmg and return the .app inside it."""
    into.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()
    if name.endswith(".zip"):
        subprocess.run(["ditto", "-xk", str(archive), str(into)], check=True)
    elif name.endswith(".dmg"):
        mount = into / "mnt"
        mount.mkdir()
        subprocess.run(
            ["hdiutil", "attach", str(archive), "-nobrowse", "-quiet", "-mountpoint", str(mount)],
            check=True,
        )
        try:
            for item in mount.iterdir():
                if item.suffix == ".app":
                    subprocess.run(["ditto", str(item), str(into / item.name)], check=True)
        finally:
            subprocess.run(["hdiutil", "detach", str(mount), "-quiet"], check=False)
    else:
        return None

    for candidate in into.glob("*.app"):
        return candidate
    for candidate in into.glob("*/*.app"):
        return candidate
    return None


def install_release(release: Release) -> Tuple[bool, str]:
    """Download and stage an update, then schedule the swap. Caller must quit."""
    target = app_bundle()
    if target is None:
        return False, "not running from an .app bundle"

    work = Path(tempfile.mkdtemp(prefix="airtouch-update-"))
    suffix = ".zip" if release.url.lower().endswith(".zip") else ".dmg"
    archive = work / f"download{suffix}"
    try:
        archive.write_bytes(_fetch(release.url, timeout=DOWNLOAD_TIMEOUT))
        staged = extract_archive(archive, work / "new")
    except Exception as exc:
        shutil.rmtree(work, ignore_errors=True)
        return False, f"download failed: {exc}"
    if staged is None:
        shutil.rmtree(work, ignore_errors=True)
        return False, "the download did not contain an .app"

    found = bundle_version(staged)
    if found and release.version and parse_version(found) != parse_version(release.version):
        shutil.rmtree(work, ignore_errors=True)
        return False, f"downloaded build is {found}, expected {release.version}"

    script = work / "swap.sh"
    script.write_text(swap_script(target, staged, os.getpid()))
    script.chmod(0o755)
    subprocess.Popen(
        ["/bin/sh", str(script)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return True, f"installing {release.version}"


def git_update() -> Tuple[bool, str]:
    """Pull the source checkout this app was started from."""
    root = source_root()
    if not (root / ".git").exists():
        return False, "not a git checkout - reinstall from the release page"
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "pull", "--ff-only"],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except Exception as exc:
        return False, f"git pull failed: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return False, detail[-1][:200] if detail else "git pull failed"
    text = (result.stdout or "").strip().splitlines()
    if text and "Already up to date" in text[0]:
        return True, "already up to date"
    return True, "pulled latest - restart AirTouch to apply"


def check() -> Tuple[str, str, Optional[Release]]:
    """Return (state, message, release).

    state: 'current' | 'available' | 'none' | 'error'
    """
    try:
        release = latest_release()
    except Exception as exc:
        return "error", str(exc)[:160], None
    if release is None:
        return "none", "no releases published yet", None
    if is_newer(release.version):
        return "available", f"{release.version} available", release
    return "current", f"up to date ({release.version})", release
