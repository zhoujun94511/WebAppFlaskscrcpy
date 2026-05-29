"""High-level orchestration for one browser session's WebRTC peer.

The Socket.IO route layer is tiny: it parses the offer payload and
hands everything to :func:`start_session`. We do the heavy lifting:

* fetch / start the device's scrcpy client,
* allocate a peer session in the :class:`PeerManager`,
* wire its callbacks back to the route's ``emit`` helper and to the
  DataChannel dispatchers,
* run the SDP offer / answer round-trip,
* attach the H.264 source on success.

The return value tells the route layer what to send back to the client
(answer SDP) or what failure reason to surface.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import scrcpy.scrcpyclients as scrcpy_clients
from scrcpy.webrtc.peer_manager import get_peer_manager

from . import adb_dispatch, scrcpy_input

_log = logging.getLogger(__name__)

EmitFn = Callable[[str, Dict[str, Any]], None]


@dataclass
class SessionStartResult:
    ok: bool
    answer: Optional[Dict[str, str]] = None
    reason: Optional[str] = None  # populated when ``ok`` is False


def start_session(sid: str, device_id: str, sdp: str, emit: EmitFn) -> SessionStartResult:
    """Run the full offer→answer flow for one browser tab.

    ``emit(event, payload)`` is the only side-channel back to that tab —
    the route adapter binds this to ``socketio.emit(..., to=sid)``.
    """
    if not device_id or not sdp:
        return SessionStartResult(ok=False, reason='bad_request')

    client = scrcpy_clients.get_client(device_id)
    if client is None or not client.alive:
        return SessionStartResult(ok=False, reason='device_unavailable')

    pm = get_peer_manager()
    try:
        session = pm.get_or_create(sid, device_id)
    except RuntimeError as exc:
        _log.error('aiortc unavailable: %s', exc)
        return SessionStartResult(ok=False, reason='aiortc_unavailable')
    except ValueError as exc:
        return SessionStartResult(ok=False, reason=f'bad_request:{exc}')

    # Wire callbacks before negotiating so ICE candidates emitted during
    # ``handle_offer`` (rare but possible) reach the client. Tag each
    # outbound message with this session's device_id so the frontend
    # routes it to the right entry in its per-device PC pool.
    def _tag(payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(payload or {})
        payload.setdefault('device_id', device_id)
        return payload

    session.on_local_ice(lambda cand: emit('webrtc:ice', _tag(cand)))

    def _handle_close(reason: str) -> None:
        """PC-state cleanup: notify the browser AND release backend state.

        The H.264 listener subscription was previously never released
        until the entire Socket.IO session disconnected — every page
        refresh leaked one PeerSession's listener, and scrcpy kept
        dispatching every frame to the dead track. After a few refresh
        cycles we observed 4+ zombie sessions all attached to the same
        scrcpy client.

        Care has to be taken with WHERE this callback runs: aiortc fires
        ``connectionstatechange`` from INSIDE the asyncio loop, so any
        blocking call that needs the same loop to make progress
        (``run_sync(pc.close(), timeout=5)``) deadlocks for ``timeout``
        seconds and piles up. Two-step fix:

          1. Synchronously detach the H.264 listener — this is the
             critical part. Pure dict mutation, no asyncio involvement,
             kills the zombie immediately.
          2. Schedule the full ``pm.close()`` (which does ``pc.close()``
             via ``run_sync``) on a daemon thread. The asyncio callback
             returns immediately so the loop is free to actually execute
             ``pc.close()``; the worker thread waits.

        ``disconnected`` is left out on purpose — that's a transient ICE
        state that often recovers without a re-offer.
        """
        emit('webrtc:closed', _tag({'reason': reason}))
        if reason not in ('closed', 'failed'):
            return

        # Step 1 — synchronous, safe inside the asyncio loop.
        try:
            session.detach_video_source()
        except (RuntimeError, KeyError, AttributeError) as detach_exc:
            _log.debug(
                '[%s/%s] detach listener error: %s', sid, device_id, detach_exc,
            )

        # Step 2 — defer pc.close + dict-removal to a worker thread so
        # we don't block (or deadlock) the asyncio loop that fires this
        # callback. ``pm.close`` already swallows close() timeouts so
        # even a pathological PC won't keep the worker around long.
        def _bg_cleanup() -> None:
            try:
                pm.close(sid, device_id)
            except (RuntimeError, KeyError) as bg_exc:
                _log.warning(
                    '[%s/%s] background cleanup error: %s',
                    sid, device_id, bg_exc,
                )

        threading.Thread(
            target=_bg_cleanup,
            name=f'webrtc-cleanup-{sid[:8]}',
            daemon=True,
        ).start()

    session.on_closed(_handle_close)
    # The input dispatcher also handles the clipboard fallback (used when
    # the caller fires before the adb DataChannel has finished opening).
    # Both dispatchers share the same adb reply path so the value /
    # ack message reaches the frontend regardless of the request channel.
    reply = _make_reply(session)
    session.on_input_message(lambda msg: scrcpy_input.dispatch(client, msg, reply, device_id=device_id))
    session.on_adb_message(lambda msg: adb_dispatch.dispatch(client, msg, reply))

    try:
        answer = pm.handle_offer(sid, device_id, sdp)
    except (RuntimeError, ValueError, ConnectionError) as exc:
        _log.exception('[%s/%s] handle_offer error', sid, device_id)
        return SessionStartResult(ok=False, reason=f'offer_error:{exc}')

    pm.attach_video(sid, device_id, client)
    _log.info('[%s/%s] webrtc answer ready', sid, device_id)
    return SessionStartResult(ok=True, answer=answer)


def handle_ice(sid: str, device_id: str, candidate: Dict[str, Any]) -> None:
    get_peer_manager().handle_ice(sid, device_id, candidate)


def close_session(sid: str, device_id: Optional[str] = None) -> None:
    """Close one device's PC, or every PC for this tab when ``device_id`` is None."""
    get_peer_manager().close(sid, device_id)


def _make_reply(session: Any):
    """Build a ``reply(payload)`` callable that ships JSON over ``adb``."""

    def _reply(payload: Dict[str, Any]) -> None:
        try:
            session.adb_channel.send(json.dumps(payload))
        except (OSError, RuntimeError, ConnectionError) as send_exc:
            _log.warning('[%s] adb reply send failed: %s', session.sid, send_exc)

    return _reply
