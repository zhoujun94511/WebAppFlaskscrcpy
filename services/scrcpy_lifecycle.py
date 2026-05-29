"""Bridge scrcpy ``Client`` lifecycle events to a Socket.IO broadcast.

scrcpy clients fire ``EVENT_INIT`` when their video / control sockets are
up, and ``EVENT_DISCONNECT`` when the stream loop dies. We translate those
into ``scrcpy_status`` broadcasts so every tab can update its per-device
session state — most importantly, tear down the WebRTC peer when scrcpy
dies (otherwise the browser is stuck with a stale MediaStream that has
no live frames and the card shows a black box).

The :func:`attach` helper is idempotent per ``Client`` instance — calling
it twice on the same client doesn't double-register.
"""

from __future__ import annotations

import logging
from typing import Any
from weakref import WeakSet

from scrcpy.scrcpyconst import EVENT_DISCONNECT, EVENT_INIT

from . import socket_bridge

_log = logging.getLogger(__name__)

# Track which Client instances we've already wired so a second
# ``attach()`` call (e.g. on subsequent ``/api/start``) is a no-op.
_attached: "WeakSet[Any]" = WeakSet()


def attach(client: Any, device_id: str) -> None:
    if client is None or client in _attached:
        return

    # Capture the epoch ONCE at attach time (not at broadcast time) so a
    # late-firing ``EVENT_DISCONNECT`` from THIS client still carries
    # THIS client's identity even after ``scrcpy_clients`` has already
    # swapped in a fresh Client for the same device_id. Without the
    # capture-at-attach, the closure would look up ``client.lifecycle_epoch``
    # later — which still resolves correctly since each Client owns its
    # own epoch — but binding the local makes the intent obvious and
    # keeps the broadcast payload deterministic if some future refactor
    # ever swaps ``lifecycle_epoch`` in place on the same instance.
    epoch = getattr(client, 'lifecycle_epoch', None)

    def _on_init(*_args: Any, **_kwargs: Any) -> None:
        socket_bridge.broadcast('scrcpy_status', {
            'device_id': device_id,
            'status': 'connected',
            'control_available': bool(getattr(client, 'control_available', False)),
            # Frontend keys its per-device "current epoch" off this on
            # ``connected``; subsequent ``disconnected`` events whose
            # epoch doesn't match are stale and dropped.
            'epoch': epoch,
        })

    def _on_disconnect(*_args: Any, **_kwargs: Any) -> None:
        socket_bridge.broadcast('scrcpy_status', {
            'device_id': device_id,
            'status': 'disconnected',
            'epoch': epoch,
        })

    try:
        client.add_listener(EVENT_INIT, _on_init)
        client.add_listener(EVENT_DISCONNECT, _on_disconnect)
        _attached.add(client)
        # If the client was already alive by the time we attached (the
        # usual case — ``Client.start(threaded=True)`` fires EVENT_INIT
        # synchronously before returning), replay a ``connected`` so the
        # frontend's session state is in sync.
        if getattr(client, 'alive', False):
            _on_init()
    except (AttributeError, RuntimeError) as exc:
        _log.warning('failed to attach lifecycle listeners for %s: %s', device_id, exc)
