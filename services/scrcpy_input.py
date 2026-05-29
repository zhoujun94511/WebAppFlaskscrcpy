"""Decode an ``input`` DataChannel message and forward to scrcpy control.

Schema:

    { "t": "touch",  "x": <px>, "y": <px>, "action": 0|1|2 }
    { "t": "key",    "code": <android-keycode>, "action": 0|1 }
    { "t": "scroll", "x": <px>, "y": <px>, "h": <int>, "v": <int> }
    { "t": "text",   "text": "..."}
    { "t": "swipe",  "startX": <px>, "startY": <px>, "endX": <px>, "endY": <px>, "duration": <ms> }
    { "t": "power",  "mode": 0|1|2 }
    { "t": "expandNotification" }
    { "t": "expandSettings" }
    { "t": "collapse" }
    { "t": "rotate" }
    { "t": "request_keyframe", "reason": "<freeze|drops|...>" }   # adaptive quality L1

    # Clipboard fallback (also accepted on the adb DataChannel; same shape):
    { "t": "clipboard.get" }
    { "t": "clipboard.set", "text": "...", "paste": true|false }

Replies (clipboard.value / clipboard.ack) are sent back via the ``reply``
callable — typically the same one that the adb dispatcher uses — because
the input DataChannel is one-way (unreliable, no message handler on the
client side).

Anything else is silently dropped.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

_log = logging.getLogger(__name__)

ReplyFn = Callable[[Dict[str, Any]], None]


def dispatch(
    client: Any,
    msg: str,
    reply: Optional[ReplyFn] = None,
    device_id: Optional[str] = None,
) -> None:
    """Parse ``msg`` and call the matching ``client.control.*`` method.

    ``client`` is a live ``scrcpy.scrcpycore.Client``. Errors are logged at
    warning level — input loss is not fatal; the next message will
    arrive ~16 ms later.

    ``reply`` is optional and only used by request/response events
    (currently the clipboard fallback). When ``None``, those replies are
    suppressed (no harm: caller didn't wire a reply channel).

    ``device_id`` is consumed by *device-scoped* messages that don't fit
    the per-client control channel — currently just ``request_keyframe``
    (handled by the adaptive quality controller). When the caller didn't
    pass it, those messages are silently dropped.
    """
    try:
        evt = json.loads(msg)
    except (ValueError, TypeError):
        return
    t = evt.get('t')
    try:
        if t == 'touch':
            client.control.touch(evt['x'], evt['y'], evt['action'])
        elif t == 'key':
            client.control.keycode(evt['code'], evt['action'])
        elif t == 'scroll':
            client.control.scroll(
                evt['x'], evt['y'],
                int(evt.get('h', 0)),
                int(evt.get('v', 0)),
            )
        elif t == 'text':
            client.control.text(evt['text'])
        elif t == 'swipe':
            # swipe() walks an interpolated touch sequence with
            # time.sleep between steps (200 ms+ total). This dispatcher
            # runs on the aiortc DataChannel callback thread, which is
            # the asyncio loop thread — calling swipe inline would
            # freeze TWCC stats / ICE keepalives / every other PC for
            # the swipe duration. Offload to a worker so the loop stays
            # responsive. The underlying ``inject()`` wrapper already
            # holds ``control_socket_lock`` per send, so concurrent
            # swipes from two threads stay byte-aligned on the wire
            # (touches may interleave but writes never corrupt).
            sx, sy = evt['startX'], evt['startY']
            ex, ey = evt['endX'], evt['endY']
            dur = evt.get('duration', 200)

            def _run_swipe():
                try:
                    client.control.swipe(sx, sy, ex, ey, duration_ms=dur)
                except (OSError, AttributeError, RuntimeError) as worker_exc:
                    _log.warning('swipe worker failed: %s', worker_exc)

            threading.Thread(
                target=_run_swipe,
                name='swipe-worker',
                daemon=True,
            ).start()
        elif t == 'power':
            client.control.set_screen_power_mode(int(evt.get('mode', 2)))
        elif t == 'expandNotification':
            client.control.expand_notification_panel()
        elif t == 'expandSettings':
            client.control.expand_settings_panel()
        elif t == 'collapse':
            client.control.collapse_panels()
        elif t == 'rotate':
            client.control.rotate_device()
        elif t == 'clipboard.set':
            seq = int(time.time_ns() & 0xFFFFFFFFFFFFFFFF)
            ok = client.control.set_clipboard(
                evt.get('text', ''),
                paste=bool(evt.get('paste', False)),
                sequence=seq,
            )
            if reply is not None:
                reply({'t': 'clipboard.ack', 'ok': bool(ok), 'sequence': seq})
        elif t == 'clipboard.get':
            text = client.control.get_clipboard()
            if reply is not None:
                reply({'t': 'clipboard.value', 'text': text})
        elif t == 'request_keyframe':
            # L1 entry: front-end observed a decoded-frame stall, dropped
            # frame, or freeze. Ask the quality controller to force scrcpy
            # to emit a fresh IDR. Cooldown / promotion to L2 happens
            # inside the controller. Lazy import to keep this module free
            # of services-layer deps for unit tests.
            if device_id:
                try:
                    from services.quality_controller import get_controller
                except ImportError:
                    get_controller = None
                if get_controller is not None:
                    get_controller().request_keyframe(
                        device_id, reason=str(evt.get('reason') or '')
                    )
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        _log.warning('input dispatch error (%s): %s', t, exc)
