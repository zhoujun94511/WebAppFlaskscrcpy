"""Adaptive bitrate wiring: glue between TWCC controller and scrcpy restart.

When the TWCC controller decides on a new target bitrate, it calls back
into us. We can't block the asyncio loop with a scrcpy restart (which
involves adb subprocess work), so we hand the actual reconfigure to a
worker thread and let the loop continue.
"""

from __future__ import annotations

import logging
import threading
from typing import Tuple

import scrcpy.scrcpyclients as scrcpy_clients
from scrcpy.webrtc.peer_manager import PeerManager, get_peer_manager

_log = logging.getLogger(__name__)

DEFAULT_BITRATE_BPS = 8_000_000


def set_adaptive(device_id: str, enabled: bool) -> Tuple[int, dict]:
    """Toggle TWCC adaptive bitrate for every PC bound to ``device_id``.

    Returns ``(affected_pc_count, status_dict)``. The status dict mirrors
    the JSON shape the REST endpoint emits so the route layer can pass
    it through unchanged.
    """
    pm = get_peer_manager()
    cfg = scrcpy_clients.get_device_config(device_id)
    initial_bps = int(cfg.get('bitrate') or DEFAULT_BITRATE_BPS)

    callback = _make_reconfigure_callback(pm, device_id) if enabled else None
    affected = pm.set_adaptive(
        device_id,
        enabled,
        initial_bps=initial_bps,
        on_target_bps=callback,
    )
    return affected, {'status': 'ok', 'enabled': enabled, 'affected': affected}


def _make_reconfigure_callback(pm: PeerManager, device_id: str):
    """Return a thread-safe callback that restarts scrcpy at a new bitrate.

    The controller fires this from the asyncio loop; we spawn a worker
    thread so the loop isn't blocked on adb operations (which can take
    hundreds of ms).
    """

    def _on_target(target_bps: int) -> None:
        def _apply() -> None:
            try:
                new_client, _ = scrcpy_clients.reconfigure(device_id, bitrate=int(target_bps))
                if new_client and new_client.alive:
                    pm.reattach_for_device(device_id, new_client)
            except (RuntimeError, OSError, ConnectionError) as exc:
                _log.warning('adaptive reconfigure failed for %s: %s', device_id, exc)

        threading.Thread(
            target=_apply,
            name=f'adapt-{device_id[:8]}',
            daemon=True,
        ).start()

    return _on_target
