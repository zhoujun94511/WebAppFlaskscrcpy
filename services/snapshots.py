"""On-demand JPEG screenshots of an active scrcpy stream.

Used by the matrix view's ``GET /api/snapshot`` endpoint. The route layer
turns the result into a Flask Response; this module just produces JPEG
bytes (or ``None`` if no frame is reachable in time).
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

import cv2

import scrcpy.scrcpyclients as scrcpy_clients

_log = logging.getLogger(__name__)

DEFAULT_WAIT_S = 1.2  # how long to wait for a fresh decode before falling back
MIN_QUALITY = 20
MAX_QUALITY = 95


def capture_jpeg(device_id: str, quality: int = 60, *, wait: float = DEFAULT_WAIT_S) -> Optional[bytes]:
    """Return a JPEG byte string of the device's current screen.

    Works whether the WebRTC path or the JPEG/WS path owns video: we
    register a one-shot frame listener so the stream loop performs a
    single decode even when no other consumer is attached. Falls back
    to ``client.last_frame`` if the listener times out.

    Returns ``None`` when the device isn't ready or no frame is
    available — the route layer maps that to an HTTP error.
    """
    if not device_id:
        return None
    quality = max(MIN_QUALITY, min(int(quality), MAX_QUALITY))

    client = scrcpy_clients.get_client(device_id)
    if not client or not client.alive:
        return None

    captured: dict = {'frame': None}
    event = threading.Event()

    def _one_shot(decoded_frame, *_args, **_kwargs):
        if decoded_frame is None:
            return
        captured['frame'] = decoded_frame
        event.set()

    client.add_listener('frame', _one_shot)
    try:
        event.wait(timeout=wait)
    finally:
        try:
            client.remove_listener('frame', _one_shot)
        except (KeyError, ValueError):
            pass

    frame = captured['frame'] or getattr(client, 'last_frame', None)
    if frame is None:
        return None

    ok, buf = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        _log.warning('cv2.imencode failed for device %s', device_id)
        return None
    return buf.tobytes()
