"""Adaptive bitrate controller driven by RTCPeerConnection stats.

Loop (one task per peer session, runs on the AsyncRunner loop):

    every POLL_INTERVAL seconds:
        pull outbound-rtp + remote-inbound-rtp stats
        derive (bandwidth_kbps, fraction_lost)
        decide a new target bitrate based on a 4-state ladder:
            HEALTHY  → cautiously raise (+10%, capped)
            STEADY   → keep current
            STRESSED → drop one ladder step (–25%)
            CRITICAL → drop two ladder steps (–50%)
        if change vs current > MIN_CHANGE_RATIO, fire reconfigure callback

The decision logic is intentionally conservative — abrupt scrcpy restarts
hurt UX more than slightly suboptimal bitrate. We rate-limit reconfigures
to once per COOLDOWN seconds.

This controller does NOT know the scrcpy device id directly; the caller
binds an ``on_change(target_bps)`` callback that, in practice, calls
``scrcpy.scrcpyclients.reconfigure(device_id, bitrate=target_bps)`` and then
re-attaches PCs via ``PeerManager.reattach_for_device``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

_log = logging.getLogger(__name__)

POLL_INTERVAL = 2.0          # seconds between stats polls
COOLDOWN = 6.0               # min seconds between reconfigure() calls
MIN_CHANGE_RATIO = 0.12      # only apply if |new-cur|/cur > 12%
MIN_BITRATE = 500_000        # 500 kbps floor
MAX_BITRATE = 20_000_000     # 20 Mbps ceiling

# Coarse ladder used for step-down moves; step-up uses smooth multiplication.
LADDER_KBPS = [500, 800, 1200, 2000, 3000, 5000, 8000, 12000, 16000, 20000]


def _ladder_step_below(current_kbps: int, steps: int = 1) -> int:
    idx = 0
    for i, rung in enumerate(LADDER_KBPS):
        if rung <= current_kbps:
            idx = i
        else:
            break
    target_idx = max(0, idx - steps)
    return LADDER_KBPS[target_idx]


@dataclass
class _Snapshot:
    ts: float
    bytes_sent: int = 0
    packets_sent: int = 0
    packets_lost: int = 0
    rtt_ms: float = 0.0
    available_kbps: Optional[float] = None  # from remote-inbound-rtp when present


@dataclass
class _State:
    current_bps: int = 8_000_000
    last_change_ts: float = field(default_factory=lambda: 0.0)
    last_snapshot: Optional[_Snapshot] = None


class BitrateController:
    """One controller per peer session.

    Wire-up:
        ctrl = BitrateController(pc, initial_bps, on_change=lambda bps: ...)
        ctrl.start()
        ...
        ctrl.stop()
    """

    def __init__(
        self,
        pc: Any,
        initial_bps: int,
        on_change: Callable[[int], Awaitable[None]] | Callable[[int], None],
        device_id: Optional[str] = None,
    ) -> None:
        self._pc = pc
        self._on_change = on_change
        # Optional — when present, every tick also reports loss/RTT to
        # the quality controller so its L3 layer can decide whether the
        # network is bad enough to warrant a resolution step-down.
        self._device_id = device_id
        self._state = _State(current_bps=int(initial_bps))
        self._task: Optional[asyncio.Task] = None
        self._stopped = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            return
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self._run(), name="bitrate-controller")

    async def stop(self) -> None:
        self._stopped.set()
        task = self._task
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _run(self) -> None:
        try:
            while not self._stopped.is_set():
                try:
                    await asyncio.wait_for(self._stopped.wait(), timeout=POLL_INTERVAL)
                    break
                except asyncio.TimeoutError:
                    pass
                try:
                    await self._tick()
                except Exception as exc:  # noqa: BLE001
                    _log.warning("bitrate tick error: %s", exc)
        except asyncio.CancelledError:
            pass

    async def _tick(self) -> None:
        snap = await self._collect()
        if snap is None:
            return

        prev = self._state.last_snapshot
        self._state.last_snapshot = snap

        if prev is None:
            return

        dt = max(snap.ts - prev.ts, 0.001)
        bytes_delta = max(snap.bytes_sent - prev.bytes_sent, 0)
        pkts_delta = max(snap.packets_sent - prev.packets_sent, 0)
        lost_delta = max(snap.packets_lost - prev.packets_lost, 0)

        send_kbps = (bytes_delta * 8) / 1000.0 / dt
        loss_ratio = (lost_delta / pkts_delta) if pkts_delta else 0.0
        bw_estimate_kbps = snap.available_kbps if snap.available_kbps else max(send_kbps, 100.0)

        regime = _classify(send_kbps, bw_estimate_kbps, loss_ratio, snap.rtt_ms)
        # L3 feed: every tick we hand loss/RTT to the quality controller,
        # which has its own two-tick-CRITICAL hysteresis for resolution
        # step-down. Import is lazy so unit tests that don't touch the
        # services layer don't pull it in.
        if self._device_id:
            try:
                from services.quality_controller import get_controller
            except ImportError:
                get_controller = None
            if get_controller is not None:
                get_controller().report_loss(
                    self._device_id, loss_ratio, snap.rtt_ms
                )
        target = self._decide_target(regime, send_kbps)
        await self._maybe_apply(target)

    def _decide_target(self, regime: str, send_kbps: float) -> int:
        cur_kbps = int(self._state.current_bps / 1000)
        if regime == "CRITICAL":
            return _ladder_step_below(cur_kbps, steps=2) * 1000
        if regime == "STRESSED":
            return _ladder_step_below(cur_kbps, steps=1) * 1000
        if regime == "HEALTHY":
            # cautiously raise, but don't exceed observed send rate by more than 1.5x
            ceiling_kbps = max(int(send_kbps * 1.5), int(cur_kbps * 1.1))
            target = min(int(cur_kbps * 1.1), ceiling_kbps)
            return min(max(target * 1000, MIN_BITRATE), MAX_BITRATE)
        return self._state.current_bps  # STEADY

    async def _maybe_apply(self, target_bps: int) -> None:
        cur = self._state.current_bps
        target_bps = max(MIN_BITRATE, min(MAX_BITRATE, target_bps))
        if cur <= 0:
            return
        delta_ratio = abs(target_bps - cur) / float(cur)
        if delta_ratio < MIN_CHANGE_RATIO:
            return
        now = time.time()
        if now - self._state.last_change_ts < COOLDOWN:
            return

        self._state.current_bps = target_bps
        self._state.last_change_ts = now
        _log.info("BitrateController: %d → %d bps", cur, target_bps)

        result = self._on_change(target_bps)
        if asyncio.iscoroutine(result):
            try:
                await result
            except Exception as exc:  # noqa: BLE001
                _log.warning("bitrate on_change error: %s", exc)

    async def _collect(self) -> Optional[_Snapshot]:
        try:
            stats = await self._pc.getStats()
        except Exception as exc:  # noqa: BLE001
            _log.debug("getStats failed: %s", exc)
            return None

        snap = _Snapshot(ts=time.time())
        items = stats.values() if hasattr(stats, "values") else stats

        for s in items:
            kind = getattr(s, "type", None)
            if kind == "outbound-rtp" and getattr(s, "kind", "") == "video":
                snap.bytes_sent += int(getattr(s, "bytesSent", 0) or 0)
                snap.packets_sent += int(getattr(s, "packetsSent", 0) or 0)
            elif kind == "remote-inbound-rtp" and getattr(s, "kind", "") == "video":
                snap.packets_lost = int(getattr(s, "packetsLost", 0) or 0)
                snap.rtt_ms = float(getattr(s, "roundTripTime", 0.0) or 0.0) * 1000.0
                # aiortc may not surface available bitrate; if absent we fall back.
                avail = getattr(s, "availableBitrate", None) or getattr(s, "availableOutgoingBitrate", None)
                if avail:
                    snap.available_kbps = float(avail) / 1000.0
        return snap


def classify(send_kbps: float, bw_kbps: float, loss_ratio: float, rtt_ms: float) -> str:
    """Public alias kept for tests / external callers; see :func:`_classify`."""
    return _classify(send_kbps, bw_kbps, loss_ratio, rtt_ms)


def ladder_step_below(current_kbps: int, steps: int = 1) -> int:
    """Public alias for :func:`_ladder_step_below`."""
    return _ladder_step_below(current_kbps, steps)


def _classify(send_kbps: float, bw_kbps: float, loss_ratio: float, rtt_ms: float) -> str:
    if loss_ratio > 0.10 or rtt_ms > 500.0:
        return "CRITICAL"
    if loss_ratio > 0.03 or rtt_ms > 250.0:
        return "STRESSED"
    headroom = bw_kbps - send_kbps
    if loss_ratio < 0.005 and headroom > send_kbps * 0.5 and rtt_ms < 120.0:
        return "HEALTHY"
    return "STEADY"
