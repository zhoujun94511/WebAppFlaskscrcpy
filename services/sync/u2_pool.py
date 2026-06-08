"""uiautomator2 device connection pool (Lab V2 — semantic layer).

Lazily connects a ``uiautomator2.Device`` per serial and caches it. The first
``connect()`` for a device pushes u2's bundled ``u2.jar`` to
``/data/local/tmp`` and launches the uiautomator server via ``app_process``
(an ephemeral process — NOT a persistently installed app), so it can take a
few seconds. Callers must therefore run pool access on a worker/request
thread, never on the aiortc asyncio loop.

uiautomator2 is imported lazily so the ``services.sync`` package still imports
when the optional dependency is absent or the lab feature is off.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

_log = logging.getLogger("sync")


class U2Error(Exception):
    """Raised when an uiautomator2 connection / call fails."""


_devices: dict[str, Any] = {}
_lock = threading.RLock()

# Per-serial lock to serialise u2 RPC calls to ONE device — the u2 jsonrpc
# connection isn't safe under concurrent calls (e.g. rapid taps spawning
# overlapping semantic workers). Different devices stay fully parallel.
_device_locks: dict[str, threading.RLock] = {}
_device_locks_guard = threading.Lock()


def device_lock(serial: str) -> threading.RLock:
    with _device_locks_guard:
        lk = _device_locks.get(serial)
        if lk is None:
            lk = threading.RLock()
            _device_locks[serial] = lk
        return lk


def _connect(serial: str) -> Any:
    try:
        import uiautomator2 as u2  # lazy: optional dependency
    except ImportError as exc:  # pragma: no cover
        raise U2Error("uiautomator2 未安装") from exc
    return u2.connect(serial)


def get(serial: str) -> Any:
    """Return a connected ``uiautomator2.Device`` for ``serial`` (cached).

    Raises :class:`U2Error` on connection failure. The slow connect runs
    outside the lock so other devices aren't blocked.
    """
    with _lock:
        dev = _devices.get(serial)
        if dev is not None:
            return dev
    try:
        dev = _connect(serial)
    except U2Error:
        raise
    except Exception as exc:  # noqa: BLE001 — surface as typed error
        raise U2Error(f"u2 连接失败: {exc}") from exc
    with _lock:
        # Another thread may have connected while we were outside the lock.
        existing = _devices.get(serial)
        if existing is not None:
            return existing
        _devices[serial] = dev
    _log.info("u2 connected: %s", serial)
    return dev


def peek(serial: str) -> Optional[Any]:
    """Return the cached device without connecting, or None."""
    with _lock:
        return _devices.get(serial)


def remove(serial: str) -> None:
    """Drop a device from the pool, best-effort stopping its server."""
    with _lock:
        dev = _devices.pop(serial, None)
    if dev is None:
        return
    # Best-effort stop. Resolve the method via getattr so we don't assume a
    # specific u2 version's API surface, and keep the except narrow.
    stop = getattr(dev, "stop_uiautomator", None)
    if callable(stop):
        try:
            stop()
        except (OSError, RuntimeError, AttributeError) as exc:
            _log.debug("u2 stop %s failed: %s", serial, exc)
    _log.info("u2 removed: %s", serial)


def clear() -> None:
    with _lock:
        serials = list(_devices.keys())
    for s in serials:
        remove(s)
