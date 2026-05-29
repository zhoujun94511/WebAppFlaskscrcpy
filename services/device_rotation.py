"""Device rotation helper — robust fallback for ROMs that ignore scrcpy.

Why this module exists
======================

scrcpy ships a ``TYPE_ROTATE_DEVICE`` control message but the underlying
``IWindowManager.freezeRotation`` call is silently dropped by every
modern Xiaomi (MIUI / HyperOS) and a fair number of OPPO / vivo
firmwares. The front-end optimistically tries scrcpy first (fast path,
≈10 ms), watches the video stream for the tell-tale aspect-ratio swap,
and falls back to this module when it doesn't see one.

What we do here
---------------

1. **Track the target rotation in the caller**, not in the device.
   ``adb shell settings get system user_rotation`` has been observed to
   return stale values right after a write on Xiaomi devices, which
   bricked the "cycle through 4 orientations" logic — every click came
   back as the same value. We now accept an explicit target from the
   client; no read-modify-write.

2. **Prefer ``cmd window user-rotation lock <N>``** (Android 10+). This
   is the post-OREO official API — equivalent to toggling auto-rotate
   off + locking rotation, but it goes through ``WindowManagerService``
   so the DisplayManager actually broadcasts the change. That broadcast
   is what makes scrcpy's encoder re-negotiate dimensions on its own.

3. **Fall back to ``settings put system …``** for older Android (≤ 9).
   Still useful for the long tail of legacy devices.

The frontend is expected to restart the scrcpy stream after we return
on the older fallback path — see the docstring of :func:`apply` for
details. Stream restart isn't strictly necessary on the ``cmd window``
path (scrcpy gets the broadcast and re-negotiates by itself) but doing
it unconditionally keeps the front-end logic uniform.
"""

# cspell:ignore adbutils dumpsys

from __future__ import annotations

import logging
import re
import threading
from typing import Dict

from adbutils import adb

_log = logging.getLogger(__name__)

# Surface.ROTATION_*: 0=portrait, 1=landscape (90° CCW), 2=upside-down,
# 3=reverse-landscape (90° CW from portrait).
VALID_ROTATIONS = (0, 1, 2, 3)

# Per-device SDK cache. ``ro.build.version.sdk`` is immutable at
# runtime; the original code re-ran ``getprop`` on every rotate call,
# adding ~50-200 ms of ADB round-trip latency per click for zero
# information. Cache once per session.
_sdk_cache: Dict[str, int] = {}
_sdk_cache_lock = threading.Lock()


class RotationError(RuntimeError):
    """Raised when the device refuses every rotation strategy."""


def _sdk(device_id: str) -> int:
    """Best-effort SDK level. 0 means "unknown — try modern path anyway"."""
    with _sdk_cache_lock:
        cached = _sdk_cache.get(device_id)
    if cached is not None:
        return cached
    try:
        raw = adb.device(device_id).shell(
            "getprop ro.build.version.sdk", timeout=4
        ) or ""
        value = int(re.sub(r"\D", "", raw) or "0")
    except (RuntimeError, OSError, TimeoutError, ValueError):
        value = 0
    # Cache even the failure (0) — a probe that just timed out is
    # unlikely to succeed next click anyway, and the rotation falls
    # back to "try both" behaviour on 0.
    with _sdk_cache_lock:
        _sdk_cache[device_id] = value
    return value


def _try_cmd_window(device_id: str, target: int) -> bool:
    """Modern path (Android 10+).

    ``cmd window user-rotation lock <0..3>`` is what the system Settings
    app itself uses under the hood. Returns True if the command echoes
    success or empty (the usual happy path); False if the binary refuses
    the subcommand (early Android 10 / cut-down ROMs).
    """
    try:
        out = adb.device(device_id).shell(
            f"cmd window user-rotation lock {target}", timeout=4
        ) or ""
    except (RuntimeError, OSError, TimeoutError) as exc:
        _log.debug("cmd window user-rotation failed on %s: %s", device_id, exc)
        return False
    low = out.strip().lower()
    # Success looks like an empty response. Recognise the various ways
    # the command can be rejected on older / cut-down builds so we know
    # to fall through to the legacy path.
    if not low:
        return True
    rejected = (
        "unknown command", "error", "exception", "usage:",
        "no such", "permission",
    )
    if any(token in low for token in rejected):
        _log.debug("cmd window rejected on %s: %s", device_id, low.splitlines()[0])
        return False
    # Unknown output but no obvious error keyword — assume success and
    # verify by reading back in caller if needed.
    return True


def _try_settings_db(device_id: str, target: int) -> bool:
    """Legacy path (≤ Android 9, also a safety net for cut-down ROMs).

    Two writes are required: disable auto-rotate first, otherwise the
    accelerometer reasserts whichever orientation the sensor agrees with.
    """
    try:
        dev = adb.device(device_id)
        dev.shell("settings put system accelerometer_rotation 0", timeout=5)
        dev.shell(f"settings put system user_rotation {target}", timeout=5)
        return True
    except (RuntimeError, OSError, TimeoutError) as exc:
        _log.debug("settings put failed on %s: %s", device_id, exc)
        return False


def apply(device_id: str, target: int) -> int:
    """Rotate the device to absolute orientation ``target``.

    Returns the rotation that was actually applied (== ``target`` on
    success). Raises :class:`RotationError` if every strategy fails.

    … note::
       The scrcpy front-end should restart its video stream after this
       call returns. The encoder needs to pick up the new screen
       geometry — on the modern ``cmd window`` path the broadcast
       reaches scrcpy and it re-negotiates on its own, but on the
       legacy ``settings put`` path scrcpy never sees a DisplayManager
       event and the picture would stay stuck on the old orientation.
       Calling :func:`apply` and then restarting the stream
       unconditionally is the simpler invariant.
    """
    if target not in VALID_ROTATIONS:
        raise RotationError(f"invalid rotation: {target!r}")

    # On Android 10+ try the modern path first; on older try the legacy
    # path directly. SDK probe failures (sdk == 0) fall through to the
    # try-both behaviour, which is the safest default.
    sdk = _sdk(device_id)
    order = ("cmd", "settings") if sdk == 0 or sdk >= 29 else ("settings", "cmd")

    last_failure = None
    for strategy in order:
        ok = (
            _try_cmd_window(device_id, target)
            if strategy == "cmd"
            else _try_settings_db(device_id, target)
        )
        if ok:
            _log.info(
                "rotated %s to %d via %s (sdk=%d)",
                device_id, target, strategy, sdk,
            )
            # Rotation swaps logical width/height, so the cached size
            # in input_shell goes stale. Drop it so the next swipe
            # re-reads ``wm size``. Lazy import keeps this module
            # decoupled from input_shell at import time.
            try:
                from services.input_shell import invalidate_screen_size_cache
            except ImportError:
                invalidate_screen_size_cache = None
            if invalidate_screen_size_cache is not None:
                invalidate_screen_size_cache(device_id)
            return target
        last_failure = strategy

    raise RotationError(
        f"all rotation strategies failed on {device_id} "
        f"(last={last_failure}, sdk={sdk})"
    )
