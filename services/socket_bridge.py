"""Tiny socket.io broadcast bridge.

The Flask-SocketIO instance is owned by ``app.py``. Service-layer code
shouldn't import Flask, so we capture a reference here once and let
helpers broadcast events to every connected tab.

Usage
-----
    from services import socket_bridge

    # api/webrtc.py:register()
    socket_bridge.bind(socketio)

    # anywhere:
    socket_bridge.broadcast('scrcpy_status', {...})
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

_log = logging.getLogger(__name__)

_socketio: Optional[Any] = None

# sid → boot_id mapping captured at socket connect time. Used to tag log
# lines so we can correlate Socket.IO sids and HTTP requests back to one
# frontend JS module load — essential for diagnosing whether a duplicate
# PeerSession is two tabs, an HMR reload, or a hidden double-call.
_boot_ids: Dict[str, str] = {}


def bind(socketio: Any) -> None:
    """Capture the Flask-SocketIO instance. Called once at app boot."""
    global _socketio
    _socketio = socketio


def broadcast(event: str, payload: Dict[str, Any]) -> None:
    """Send ``event`` with ``payload`` to every connected client.

    Silently no-ops if the bridge hasn't been bound (e.g. unit tests that
    don't spin up the socket layer).
    """
    if _socketio is None:
        _log.debug('socket_bridge.broadcast(%s) ignored: not bound', event)
        return
    try:
        _socketio.emit(event, payload)
    except (RuntimeError, OSError) as exc:
        _log.warning('socket_bridge broadcast failed (%s): %s', event, exc)


def set_boot_id(sid: str, boot_id: str) -> None:
    if not sid:
        return
    _boot_ids[sid] = boot_id or ''


def get_boot_id(sid: str) -> str:
    return _boot_ids.get(sid or '', '')


def forget_boot_id(sid: str) -> None:
    _boot_ids.pop(sid or '', None)
