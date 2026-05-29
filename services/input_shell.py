"""Shell-based input helpers (``adb she'll input ...``).

Distinct from ``services.scrcpy_input``: that module dispatches messages
into a *live* scrcpy control channel (low-latency, requires an active
stream). This one is the always-available fallback that drives the same
hardware actions through plain adb shell, so quick-action UI buttons
(footer swipes, etc.) keep working before the user presses Play.

The single responsibility here is to translate a high-level intent
(e.g. "swipe down") into a concrete ``adb she'll input swipe`` invocation
on the device, sizing the gesture against the device's real resolution.
Routes in ``api/devices.py`` are thin adapters that parse JSON, call
:func:`swipe_direction`, and shape the response.
"""

# cspell:ignore adbutils

from __future__ import annotations

import logging
import threading
from typing import Dict, Tuple

from adbutils import adb

_log = logging.getLogger(__name__)

VALID_DIRECTIONS = ("up", "down", "left", "right")

# Per-device screen-size cache. Resolution is effectively immutable
# during a session (rotation swaps width / height but doesn't change
# the *physical* pixel count — and the cache stores the post-rotation
# logical dims anyway). Without this every footer-swipe paid the full
# ``adb shell wm size`` round-trip (≈200-500 ms on USB; worse on Wi-Fi
# adb). Invalidated explicitly via ``invalidate_screen_size_cache`` —
# the rotation service should call this if you ever observe stale
# results on a particular ROM.
_screen_size_cache: Dict[str, Tuple[int, int]] = {}
_screen_size_cache_lock = threading.Lock()


def get_screen_size(device_id: str) -> Tuple[int, int] | None:
    """Public, non-raising wrapper around the cached ``wm size`` lookup.

    Returns ``(width, height)`` on success or ``None`` if the device is
    unreachable / ``wm size`` failed. Used by ``/api/devices`` to
    expose screen geometry in the device listing so the UI can render
    correctly-shaped card placeholders BEFORE a scrcpy stream exists.

    The first call per device pays the adb round-trip (~200-500 ms);
    subsequent calls hit the same per-device cache the swipe helpers
    use, so this is effectively free for the lifetime of the session.
    """
    try:
        return _read_screen_size(device_id)
    except (InputShellError, RuntimeError, OSError, TimeoutError) as exc:
        _log.debug("get_screen_size(%s) failed: %s", device_id, exc)
        return None


def invalidate_screen_size_cache(device_id: str | None = None) -> None:
    """Drop a single device's cached size, or the whole cache if None.

    Safe to call from any thread. Called by the rotation service on
    successful orientation change (rotation swaps dims), and could be
    called by a future "resolution changed" hook.
    """
    with _screen_size_cache_lock:
        if device_id is None:
            _screen_size_cache.clear()
        else:
            _screen_size_cache.pop(device_id, None)

# Sweep length as a fraction of the relevant screen axis. ~60% of the
# axis is long enough to register as a deliberate gesture (notification
# shade, recents) while staying short of a fling so the landing point
# remains predictable.
_SWEEP_FRACTION = 0.30  # half-length on each side of the centre point

# Acceptable duration window in milliseconds. Below 50ms Android tends
# to interpret the gesture as a fling; above 2s it stalls the input
# subsystem on some ROMs. The default 250ms matches a comfortable
# human-scale swipe.
DEFAULT_DURATION_MS = 250
_MIN_DURATION_MS = 50
_MAX_DURATION_MS = 2000


class InputShellError(RuntimeError):
    """Raised when the adb she'll input operation cannot be completed."""


def _read_screen_size(device_id: str) -> Tuple[int, int]:
    """Return ``(width, height)`` in physical pixels.

    Honours ``Override size`` when present (that's what the user is
    actually looking at when DPI/size has been overridden via
    ``wm size``). Falls back to the ``Physical size`` line.

    Cached per device. The first call pays the ``adb shell wm size``
    round-trip; subsequent calls for the same device hit the cache.
    Call :func:`invalidate_screen_size_cache` if you need to force a
    refresh (rotation, DPI override change, etc.).
    """
    with _screen_size_cache_lock:
        cached = _screen_size_cache.get(device_id)
    if cached is not None:
        return cached

    raw = adb.device(device_id).shell("wm size", timeout=4) or ""
    physical: Tuple[int, int] = (0, 0)
    override: Tuple[int, int] = (0, 0)
    for line in raw.splitlines():
        line = line.strip()
        if ":" not in line or "size" not in line.lower():
            continue
        head, spec = line.split(":", 1)
        try:
            w_str, h_str = spec.strip().lower().split("x", 1)
            dims = (int(w_str), int(h_str))
        except ValueError:
            continue
        if "override" in head.lower():
            override = dims
        else:
            physical = dims
    chosen = override if override != (0, 0) else physical
    if chosen == (0, 0):
        raise InputShellError("could not read screen size from wm size")
    with _screen_size_cache_lock:
        _screen_size_cache[device_id] = chosen
    return chosen


def _resolve_endpoints(
    direction: str, width: int, height: int
) -> Tuple[int, int, int, int]:
    """Compute (x1, y1, x2, y2) for a centred directional sweep."""
    cx, cy = width // 2, height // 2
    dx = int(width * _SWEEP_FRACTION)
    dy = int(height * _SWEEP_FRACTION)
    if direction == "up":
        return cx, cy + dy, cx, cy - dy
    if direction == "down":
        return cx, cy - dy, cx, cy + dy
    if direction == "left":
        return cx + dx, cy, cx - dx, cy
    if direction == "right":
        return cx - dx, cy, cx + dx, cy
    raise InputShellError(f"invalid direction: {direction!r}")


def _clamp_duration(duration_ms: int) -> int:
    return max(_MIN_DURATION_MS, min(_MAX_DURATION_MS, int(duration_ms)))


def swipe_direction(
    device_id: str,
    direction: str,
    duration_ms: int = DEFAULT_DURATION_MS,
) -> None:
    """Issue a centred directional swipe via ``adb she'll input swipe``.

    ``direction`` must be one of :data:`VALID_DIRECTIONS`. Raises
    :class:`InputShellError` (subclass of ``RuntimeError``) on invalid
    input or shell failure; the underlying ``adbutils`` ``RuntimeError``
    / ``OSError`` / ``TimeoutError`` are *not* wrapped — let the route
    layer decide whether to coerce them to a 5xx response.
    """
    direction = (direction or "").strip().lower()
    if direction not in VALID_DIRECTIONS:
        raise InputShellError(f"invalid direction: {direction!r}")
    width, height = _read_screen_size(device_id)
    x1, y1, x2, y2 = _resolve_endpoints(direction, width, height)
    duration = _clamp_duration(duration_ms)
    _log.debug(
        "swipe %s on %s: (%d,%d) → (%d,%d) over %dms (screen=%dx%d)",
        direction, device_id, x1, y1, x2, y2, duration, width, height,
    )
    adb.device(device_id).shell(
        f"input swipe {x1} {y1} {x2} {y2} {duration}", timeout=4
    )
