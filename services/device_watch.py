"""Poll ``adb devices`` and broadcast list changes over Socket.IO.

A 2.5s polling thread compares the connected-device set to the previous
snapshot. On any add/remove it pushes a ``devices_changed`` event so
every browser tab refreshes its grid without waiting for the user to
hit the refresh button. The set of broadcast events:

    devices_changed  { devices: [serial, ...], names: { serial: 'Brand Model' } }

Friendly names come from ``ro.product.brand`` + ``ro.product.model`` and
are cached per-serial (props don't change without a reboot). The cache
is invalidated on disconnect so a re-plug with different props (rare,
but possible after a wipe) doesn't show stale text.

The watcher also nudges scrcpy lifecycle: a device that vanishes from
adb gets its scrcpy client stopped + its persisted "auto-restore" entry
will silently filter out on next page load.
"""

# cspell:ignore adbutils getprop

from __future__ import annotations

import logging
import threading
from typing import Dict, List, Set

from adbutils import adb

import scrcpy.scrcpyclients as scrcpy_clients

from . import socket_bridge

_log = logging.getLogger(__name__)

_thread: threading.Thread | None = None
_stop = threading.Event()
_last_set: Set[str] = set()
_name_cache: Dict[str, str] = {}
_POLL_INTERVAL = 2.5  # seconds


def friendly_name(serial: str) -> str:
    """Return cached 'Brand Model' for ``serial``, populating the cache
    on first lookup. Falls back to the bare serial when adb is unhappy.
    """
    if serial in _name_cache:
        return _name_cache[serial]
    try:
        d = adb.device(serial)
        brand = d.shell('getprop ro.product.brand', timeout=3).strip()
        model = d.shell('getprop ro.product.model', timeout=3).strip()
        name = f'{brand} {model}'.strip()
    except (RuntimeError, OSError, TimeoutError) as exc:
        _log.debug('[devices] friendly_name %s failed: %s', serial, exc)
        name = ''
    if not name:
        name = serial
    _name_cache[serial] = name
    return name


def names_for(serials: List[str]) -> Dict[str, str]:
    return {s: friendly_name(s) for s in serials}


def _broadcast(serials: List[str]) -> None:
    socket_bridge.broadcast('devices_changed', {
        'devices': serials,
        'names': names_for(serials),
    })


def _on_device_removed(serial: str) -> None:
    """Best-effort cleanup when a device vanishes from adb."""
    _name_cache.pop(serial, None)
    # Stop the scrcpy client so a future reconnect builds a fresh one.
    try:
        scrcpy_clients.stop_client(serial)
    except Exception as exc:  # noqa: BLE001
        _log.warning('[devices] stop_client(%s) on disconnect failed: %s', serial, exc)


def _loop() -> None:
    global _last_set
    # Prime the cache with whatever's connected at startup — keeps the
    # first /api/devices call from blocking on getprop.
    try:
        _last_set = set(scrcpy_clients.get_devices())
        for s in _last_set:
            friendly_name(s)
    except Exception as exc:  # noqa: BLE001
        _log.warning('[devices] watcher prime failed: %s', exc)

    while not _stop.is_set():
        try:
            current = set(scrcpy_clients.get_devices())
            if current != _last_set:
                added = current - _last_set
                removed = _last_set - current
                if added:
                    _log.info('[devices] connected: %s', sorted(added))
                if removed:
                    _log.info('[devices] disconnected: %s', sorted(removed))
                for s in removed:
                    _on_device_removed(s)
                _last_set = current
                _broadcast(sorted(current))
        except Exception as exc:  # noqa: BLE001
            _log.warning('[devices] watch poll error: %s', exc)
        _stop.wait(timeout=_POLL_INTERVAL)


def start() -> None:
    """Start the background watcher. Idempotent."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name='device-watch', daemon=True)
    _thread.start()
    _log.info('[devices] watcher started (interval=%.1fs)', _POLL_INTERVAL)


def stop() -> None:
    _stop.set()
