"""Socket.IO bindings for the ADB terminal.

Events
------
client → server:
    terminal:open    { device_id }                 → terminal:opened or terminal:error
    terminal:input   { data }                       (string, sent as utf-8 bytes)
    terminal:resize  { cols, rows }
    terminal:close   {}

server → client:
    terminal:opened  { session_id }
    terminal:output  { data }                       (string; binary stdout decoded as utf-8 replace)
    terminal:closed  { reason }
    terminal:error   { error }

We key sessions by Socket.IO sid: one terminal per browser tab. Disconnects
auto-close the underlying adb shell.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict

from flask import request

from services import authentication, reservations

from .adb_shell import get_session_registry

_log = logging.getLogger(__name__)


def register(socketio: Any) -> None:
    registry = get_session_registry()

    def _to_client(sid: str, event: str, payload: Dict[str, Any]) -> None:
        socketio.emit(event, payload, to=sid)

    @socketio.on("terminal:open")
    def _on_open(data: Dict[str, Any]) -> None:
        sid = request.sid  # type: ignore[attr-defined]
        device_id = (data or {}).get("device_id", "").strip()
        if not device_id:
            _to_client(sid, "terminal:error", {"error": "missing device_id"})
            return

        # adb shell is the highest-privilege surface — enforce reservation.
        try:
            reservations.assert_owner(device_id, authentication.user_from_socket())
        except reservations.ReservationError as exc:
            _to_client(sid, "terminal:error", {"error": str(exc)})
            return

        def on_output(chunk: bytes) -> None:
            # Decode for the xterm consumer; binary-safe pass-through via base64
            # would be nicer for non-text output, but adb shell is overwhelmingly
            # text-based and xterm expects strings.
            text = chunk.decode("utf-8", errors="replace")
            _to_client(sid, "terminal:output", {"data": text})

        def on_close(reason: str) -> None:
            _to_client(sid, "terminal:closed", {"reason": reason})

        try:
            registry.open(sid, device_id, on_output=on_output, on_close=on_close)
        except RuntimeError as exc:
            _to_client(sid, "terminal:error", {"error": str(exc)})
            return
        _to_client(sid, "terminal:opened", {"session_id": sid})

    @socketio.on("terminal:input")
    def _on_input(data: Dict[str, Any]) -> None:
        sid = request.sid  # type: ignore[attr-defined]
        payload = data.get("data", "") if isinstance(data, dict) else ""
        if isinstance(payload, str):
            registry.write(sid, payload.encode("utf-8"))
        elif isinstance(payload, dict) and payload.get("b64"):
            try:
                registry.write(sid, base64.b64decode(payload["b64"]))
            except (ValueError, TypeError, KeyError):
                pass

    @socketio.on("terminal:resize")
    def _on_resize(data: Dict[str, Any]) -> None:
        sid = request.sid  # type: ignore[attr-defined]
        try:
            cols = int(data.get("cols") or 0)
            rows = int(data.get("rows") or 0)
        except (ValueError, TypeError, AttributeError):
            return
        registry.resize(sid, cols, rows)

    @socketio.on("terminal:close")
    def _on_close(_data: Dict[str, Any]) -> None:
        sid = request.sid  # type: ignore[attr-defined]
        registry.close(sid)

    # Auto-close on socket disconnect — registered alongside the WebRTC
    # disconnect handler; both are idempotent so coexistence is fine.
    @socketio.on("disconnect")
    def _on_disconnect() -> None:
        sid = request.sid  # type: ignore[attr-defined]
        registry.close(sid, reason="disconnect")
