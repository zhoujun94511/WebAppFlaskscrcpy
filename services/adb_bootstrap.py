"""Ensure an ``adb`` binary is available before anything imports adbutils.

Resolution order
----------------
1. Existing ``adb`` on the system ``PATH``  →  use it as-is.
2. Previously-extracted bundle under ``resources/runpath/``  →  reuse.
3. Extract the OS-matching zip from ``resources/re_adb/`` into
   ``resources/runpath/`` and use that.

The bundle directory is then prepended to ``PATH`` so:
- ``adbutils`` (which spawns ``adb start-server`` via ``subprocess`` when the
  server isn't already up) finds our binary first;
- any other process we spawn (scrcpy push, etc.) inherits the same lookup.

We also call ``adb start-server`` once explicitly so the first adbutils
query doesn't pay the spawn cost mid-request.

Why bundle at all? On a fresh user machine without Android Platform Tools
installed, the app used to die at first device probe. Now we ship a copy
of the official Google Platform Tools (one zip per OS) and self-deploy.

Usage
-----
Call :func:`ensure_adb` *once* at app boot, before any module does
``from adbutils import adb``.
"""

from __future__ import annotations

# cspell:ignore runpath adbutils
import logging
import os
import platform
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BUNDLE_DIR = _REPO_ROOT / "resources" / "re_adb"
_EXTRACT_DIR = _REPO_ROOT / "resources" / "runpath"

# Mapping platform.system() → zip we expect to ship.
_ZIPS = {
    "Windows": "platform-tools-latest-windows.zip",
    "Darwin": "platform-tools-latest-darwin.zip",
    "Linux": "platform-tools-latest-linux.zip",
}


def _binary_name() -> str:
    return "adb.exe" if platform.system() == "Windows" else "adb"


def _scan_extracted() -> Optional[Path]:
    """Find an ``adb`` binary anywhere under EXTRACT_DIR (zip layouts vary)."""
    if not _EXTRACT_DIR.exists():
        return None
    needle = _binary_name()
    for candidate in _EXTRACT_DIR.rglob(needle):
        if candidate.is_file():
            return candidate
    return None


def _extract_bundle() -> Optional[Path]:
    system = platform.system()
    zip_name = _ZIPS.get(system)
    if zip_name is None:
        _log.warning("adb-bootstrap: no bundle for OS %s", system)
        return None
    archive = _BUNDLE_DIR / zip_name
    if not archive.exists():
        _log.warning("adb-bootstrap: bundle missing: %s", archive)
        return None

    _EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    _log.info("adb-bootstrap: extracting %s -> %s", archive.name, _EXTRACT_DIR)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(_EXTRACT_DIR)

    found = _scan_extracted()
    if found is not None and system != "Windows":
        # ZIP doesn't preserve exec bits on POSIX — restore them.
        for f in [found, *found.parent.glob("fastboot"), *found.parent.glob("mke2fs*")]:
            try:
                f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except OSError:
                pass
    if found is not None and system == "Darwin":
        _strip_macos_quarantine(found.parent)
    return found


def _strip_macos_quarantine(directory: Path) -> None:
    """Remove the ``com.apple.quarantine`` xattr Gatekeeper sets on files
    extracted from a downloaded zip.

    Without this, the first ``adb`` invocation triggers the macOS "cannot
    be opened because the developer cannot be verified" dialog and exits
    with code 126. ``xattr -dr`` is the canonical fix; it's preinstalled
    on every macOS release back to 10.5, so we can rely on it being on
    PATH.
    """
    try:
        subprocess.run(
            ["xattr", "-dr", "com.apple.quarantine", str(directory)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.warning("adb-bootstrap: failed to strip quarantine: %s", exc)


def _prepend_path(adb_dir: str) -> None:
    cur = os.environ.get("PATH", "")
    parts = cur.split(os.pathsep) if cur else []
    if adb_dir in parts:
        return
    os.environ["PATH"] = adb_dir + os.pathsep + cur


def _start_server(adb_path: Path) -> None:
    """Idempotent ``adb start-server`` so the first adbutils call is fast."""
    try:
        subprocess.run(
            [str(adb_path), "start-server"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.warning("adb-bootstrap: start-server failed: %s", exc)


def ensure_adb() -> Optional[Path]:
    """Resolve a usable ``adb`` binary, extracting the bundle if needed.

    Returns the absolute path to the chosen binary, or ``None`` when neither
    a system adb nor a bundled zip is available for this OS.
    """
    # (1) System adb on PATH wins — respects user's existing toolchain.
    on_path = shutil.which("adb")
    if on_path:
        adb_path = Path(on_path)
        _log.info("adb-bootstrap: using system adb at %s", adb_path)
        _start_server(adb_path)
        return adb_path

    # (2) Reuse a previously-extracted bundle.
    found = _scan_extracted()
    if found is None:
        # (3) First boot — extract the OS-matching zip.
        found = _extract_bundle()

    if found is None:
        _log.error("adb-bootstrap: no adb on PATH and bundle extraction failed")
        return None

    _prepend_path(str(found.parent))
    _log.info("adb-bootstrap: using bundled adb at %s", found)
    _start_server(found)
    return found
