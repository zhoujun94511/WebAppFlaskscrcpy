"""WebRTC signaling routes (Socket.IO).

This module only translates Socket.IO events ↔ service calls.

Events
------
client → server:
    webrtc:offer    { device_id, sdp }                                  → ``webrtc:answer`` / ``webrtc:closed``
    webrtc:ice      { device_id, candidate, sdpMid, sdpMLineIndex }
    webrtc:close    { device_id?}   (device_id omitted ⇒ close every PC for this tab)

server → client (per-sid):
    webrtc:answer   { device_id, sdp, type}
    webrtc:ice      { device_id, candidate, sdpMid, sdpMLineIndex }
    webrtc:closed   { device_id?, reason }

All session orchestration, DataChannel decoding, file-transfer state and
clipboard handling live in ``services/``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from flask import request

from services import authentication, device_watch, reservations, socket_bridge, webrtc_session

_log = logging.getLogger(__name__)


def register(socketio: Any) -> None:
    """Bind WebRTC events onto the given Flask-SocketIO instance."""

    # Make the same socketio instance available for tab-wide broadcasts
    # (scrcpy_status etc.) from anywhere in services/.
    socket_bridge.bind(socketio)
    # Kick off the adb-devices watcher — pushes ``devices_changed`` socket
    # events when a phone gets plugged in or unplugged so the grid updates
    # without the user mashing the refresh button.
    device_watch.start()

    def _emit_to(sid: str, event: str, payload: Dict[str, Any]) -> None:
        socketio.emit(event, payload, to=sid)

    @socketio.on('connect')
    def _on_connect(auth: Any = None) -> None:
        sid = request.sid  # type: ignore[attr-defined]
        boot_id = ''
        if isinstance(auth, dict):
            boot_id = str(auth.get('boot_id') or '')
        socket_bridge.set_boot_id(sid, boot_id)
        _log.info('[sid=%s boot=%s] socket connected', sid, boot_id or '-')

    @socketio.on('webrtc:offer')
    def _on_offer(data: Dict[str, Any]) -> None:
        sid = request.sid  # type: ignore[attr-defined]
        data = data or {}
        device_id = data.get('device_id') or ''
        sdp = data.get('sdp') or ''

        def _emit(event: str, payload: Dict[str, Any]) -> None:
            # Tag every server→client message with device_id so the frontend
            # can route it to the right PC out of its per-device pool.
            payload = dict(payload or {})
            payload.setdefault('device_id', device_id)
            _emit_to(sid, event, payload)

        boot = socket_bridge.get_boot_id(sid) or '-'
        _log.info('[sid=%s boot=%s dev=%s] webrtc:offer received', sid, boot, device_id)

        # Reservation gate: the video/input/file data plane is the real
        # control surface, so guarding only HTTP /start would be bypassable.
        try:
            reservations.assert_owner(device_id, authentication.user_from_socket())
        except reservations.ReservationError as exc:
            _log.warning('[sid=%s dev=%s] offer denied: %s', sid, device_id, exc)
            _emit('webrtc:closed', {'reason': f'denied:{exc}'})
            return

        result = webrtc_session.start_session(sid, device_id, sdp, _emit)
        if not result.ok:
            _log.warning('[sid=%s boot=%s dev=%s] start_session failed: %s', sid, boot, device_id, result.reason)
            _emit('webrtc:closed', {'reason': result.reason or 'unknown'})
            return
        _emit('webrtc:answer', result.answer or {})

    @socketio.on('webrtc:ice')
    def _on_ice(data: Dict[str, Any]) -> None:
        sid = request.sid  # type: ignore[attr-defined]
        data = data or {}
        device_id = data.get('device_id')
        if not device_id:
            _log.warning('[%s] webrtc:ice missing device_id; ignored', sid)
            return
        webrtc_session.handle_ice(sid, device_id, data)

    @socketio.on('webrtc:close')
    def _on_close(data: Dict[str, Any]) -> None:
        sid = request.sid  # type: ignore[attr-defined]
        data = data or {}
        device_id = data.get('device_id') or None
        webrtc_session.close_session(sid, device_id)

    @socketio.on('disconnect')
    def _on_disconnect() -> None:
        # Tab is gone — tear down EVERY PeerSession this sid owns.
        sid = request.sid  # type: ignore[attr-defined]
        boot = socket_bridge.get_boot_id(sid) or '-'
        _log.info('[sid=%s boot=%s] socket disconnected', sid, boot)
        webrtc_session.close_session(sid, None)
        socket_bridge.forget_boot_id(sid)
