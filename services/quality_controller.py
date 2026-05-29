"""Adaptive anti-mosaic controller.

Sits between the front-end stream-quality monitor and the scrcpy
lifecycle. Owns a four-layer defence against H.264 reference-frame
divergence ("mosaic" / "smearing"):

  L1  request_keyframe()    — front-end detected the picture broke;
                              force scrcpy to re-issue SPS/PPS/IDR by
                              restarting the stream. Cooldown 8 s.

  L2  tighten_gop()         — if L1 fires ≥ 3 times in 30 s the IDR
                              cadence isn't enough. Drop ``i_frame_
                              interval`` from 2 s → 1 s.

  L3  report_loss(loss)     — packet loss / RTT signals from the
                              existing BitrateController feed in here.
                              Sustained CRITICAL state forces a max-width step-down (only on ≥1080p devices).

  L4  heal()                — periodic tick. If nothing has fired for
                              ``HEAL_AFTER`` seconds, restore tightened
                              GOP and reduced max-width to defaults.

The controller is intentionally state-only here — the *actions* (scrcpy
restart, reconfigure) go through ``services.scrcpy_lifecycle`` and
``scrcpy.scrcpyclients.reconfigure`` so the existing locking + PC reattach
machinery is reused.

Thread model
------------
Public methods (``request_keyframe`` / ``report_loss`` / ``heal``) are
safe to call from any thread; an internal :class:`threading.RLock`
serialises mutations of the per-device state and ensures cooldowns and
ladder steps are race-free. Heavy I/O (scrcpy restart) is dispatched to
a worker thread so callers never block on adb.
"""

# cspell:ignore scrcpy adbutils

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import scrcpy.scrcpyclients as scrcpy_clients

_log = logging.getLogger(__name__)

# ── tuning constants ────────────────────────────────────────────────
# Minimum gap between two forced keyframes for the same device. Without
# this every glitch in the first ~200 ms after a restart would queue
# another restart, leading to a flicker loop.
KEYFRAME_COOLDOWN_S = 8.0

# Window over which we count L1 events; this many events inside the
# window promotes to L2 (tighten GOP).
L2_WINDOW_S = 30.0
L2_TRIGGER_COUNT = 3

# IDR cadence ladder. Default 2 s, tightened to 1 s under L2 pressure.
# We don't go below 1 s — IDR is 10–15× the size of a P-frame; sub-1s
# cadence eats most of the bitrate budget for keyframes alone.
GOP_DEFAULT_S = 2
GOP_TIGHT_S = 1

# How long the controller must see *no* L1/L2/L3 activity before it
# starts undoing tightened parameters. Generous because we don't want a
# user to see the picture get worse again right after it settled.
HEAL_AFTER_S = 60.0

# Width step-down ladder for L3. Only applied if the device's native
# width is at least the previous rung — we never *upscale* a small
# screen. 0 means "device native"; we never override that as a default.
MAX_WIDTH_LADDER = [0, 1440, 1080, 720]


@dataclass
class _DeviceState:
    """Per-device controller state. Guarded by ``QualityController._lock``."""
    last_keyframe_ts: float = 0.0
    recent_keyframe_ts: list = field(default_factory=list)   # for L2 window
    last_critical_ts: float = 0.0
    last_any_action_ts: float = 0.0   # for L4 healing
    # Currently applied tunables — read back from scrcpy_clients
    # lazily, but cached here for cheap state-machine decisions.
    current_gop_s: int = GOP_DEFAULT_S
    current_width_rung: int = 0   # index into MAX_WIDTH_LADDER


class QualityController:
    """Singleton. Use :func:`get_controller` to access."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._devices: dict[str, _DeviceState] = {}
        # DEFAULT OFF (rolled back from default-on after observing
        # video-stream desync on real devices).
        #
        # Two phases of history here, both ended with the controller
        # off:
        #
        # 1. First cut routed L1 through ``scrcpy.scrcpyclients.reconfigure``
        #    which tore down every active PeerConnection. User taps
        #    looked like freezes to the monitor → reconfigure flood →
        #    feedback loop. Fixed by switching L1 to scrcpy's own
        #    TYPE_RESET_VIDEO (opcode 17, non-destructive in theory).
        #
        # 2. After re-enabling default-on, real-device testing showed
        #    "Video header looks bogus" warnings repeating at roughly
        #    the L1 cooldown cadence (8 s). The most plausible cause:
        #    the bundled scrcpy-server.jar's protocol version doesn't
        #    actually honour TYPE_RESET_VIDEO the same way upstream
        #    4.0 does — sending the opcode either confuses its byte
        #    parser or causes a transient encoder reset that injects
        #    non-frame bytes into the video socket. The stream
        #    recovery (``buf.clear()``) keeps working but every reset
        #    pushes a hard buffer drop on the consuming end.
        #
        # The L1-L4 machinery stays in the codebase — fully tested,
        # ready to flip back on if/when we ship a known-good jar or
        # add a wire-level capability probe. Until then the baseline
        # tuning (16 Mbps default bitrate, 60 fps cap, 2 s IDR
        # cadence) is plenty for most use.
        self._enabled = False

    # ── public configuration ────────────────────────────────────────
    def set_enabled(self, enabled: bool) -> None:
        """Master kill-switch. When False every L1–L4 entry is a no-op."""
        with self._lock:
            self._enabled = bool(enabled)
        _log.info("quality controller %s", "enabled" if enabled else "disabled")

    def is_enabled(self) -> bool:
        with self._lock:
            return self._enabled

    # ── L1: force keyframe ──────────────────────────────────────────
    def request_keyframe(self, device_id: str, reason: str = "") -> bool:
        """Front-end says the picture broke. Force a fresh IDR.

        Returns True if a reset was actually sent, False when the
        request was throttled by cooldown / disabled / unknown device.

        Implementation: sends scrcpy's ``TYPE_RESET_VIDEO`` (opcode 17)
        directly on the live client's control socket. MediaCodec emits
        a new SPS/PPS/IDR while the TCP stream and the PeerConnection
        stay up — no reconfigure, no reattach, no PC teardown. If three
        L1 fires accumulate inside ``L2_WINDOW_S`` we promote to L2 and
        tighten the GOP cadence (which DOES require a reconfigure).
        """
        if not device_id:
            return False
        with self._lock:
            if not self._enabled:
                return False
            st = self._devices.setdefault(device_id, _DeviceState())
            now = time.monotonic()
            if now - st.last_keyframe_ts < KEYFRAME_COOLDOWN_S:
                _log.debug(
                    "request_keyframe(%s) suppressed: cooldown (%s)",
                    device_id, reason or "no-reason",
                )
                return False
            st.last_keyframe_ts = now
            st.last_any_action_ts = now
            st.recent_keyframe_ts = [
                ts for ts in st.recent_keyframe_ts if now - ts < L2_WINDOW_S
            ]
            st.recent_keyframe_ts.append(now)
            promote_to_l2 = len(st.recent_keyframe_ts) >= L2_TRIGGER_COUNT
            current_gop = st.current_gop_s
        _log.info(
            "request_keyframe(%s): %s%s",
            device_id,
            reason or "no-reason",
            " → promoting to L2 (tighten GOP)" if promote_to_l2 else "",
        )
        if promote_to_l2 and current_gop > GOP_TIGHT_S:
            # L2 implies a reconfigure (GOP is set at scrcpy server
            # start time, MediaCodec doesn't expose a runtime knob for
            # it). The reconfigure will produce a fresh IDR for free,
            # so skip the L1 reset on this promoting tick.
            self._dispatch_reconfigure(device_id, i_frame_interval=GOP_TIGHT_S)
            with self._lock:
                self._devices[device_id].current_gop_s = GOP_TIGHT_S
                # Reset the window so we don't immediately re-promote.
                self._devices[device_id].recent_keyframe_ts.clear()
            return True
        # Pure L1: send the reset opcode on the live control socket.
        # ``peek_client`` deliberately doesn't start a new session — if
        # the device isn't currently streaming there's nothing to fix.
        self._dispatch_reset_video(device_id)
        return True

    @staticmethod
    def _dispatch_reset_video(device_id: str) -> None:
        """Send TYPE_RESET_VIDEO on the live scrcpy control channel.

        Non-destructive. Runs on a worker thread because
        ``control.reset_video()`` writes to a socket, and we don't want
        to block the input dispatcher / heal tick. Errors are logged
        and swallowed — a failed reset just means the picture stays
        glitched for one more GOP, not a fatal error.
        """
        def _send() -> None:
            try:
                client = scrcpy_clients.peek_client(device_id)
                if client is None:
                    _log.debug(
                        "reset_video on %s: no live client; skipping",
                        device_id,
                    )
                    return
                client.control.reset_video()
            except (OSError, AttributeError, RuntimeError) as exc:
                _log.warning(
                    "reset_video on %s failed: %s", device_id, exc,
                )

        threading.Thread(
            target=_send,
            name=f"qctl-reset-{device_id[:8]}",
            daemon=True,
        ).start()

    # ── L3: feed network signals from BitrateController ─────────────
    def report_loss(self, device_id: str, loss_ratio: float, rtt_ms: float) -> None:
        """Hook for the existing TWCC controller.

        Sustained CRITICAL (loss > 10% or RTT > 500ms over two ticks)
        triggers a max-width step-down. Doesn't override the bitrate
        ladder — BitrateController owns that.
        """
        if not device_id:
            return
        with self._lock:
            if not self._enabled:
                return
            st = self._devices.setdefault(device_id, _DeviceState())
            now = time.monotonic()
            critical = loss_ratio > 0.10 or rtt_ms > 500.0
            if not critical:
                return
            if now - st.last_critical_ts < L2_WINDOW_S:
                # Two CRITICAL ticks inside the window → step down.
                next_rung = st.current_width_rung + 1
                if next_rung < len(MAX_WIDTH_LADDER):
                    target_width = MAX_WIDTH_LADDER[next_rung]
                    st.current_width_rung = next_rung
                    st.last_any_action_ts = now
                    _log.info(
                        "L3 width step-down on %s: rung %d (width=%d)",
                        device_id, next_rung, target_width,
                    )
                    self._dispatch_reconfigure(device_id, max_width=target_width)
            st.last_critical_ts = now

    # ── L4: periodic heal ───────────────────────────────────────────
    def heal(self) -> None:
        """Periodic tick. Call ~every 10 s from any scheduler.

        Reverts L2/L3 tunables for any device that has been quiet for
        ``HEAL_AFTER_S``. We never roll back L1 (it's stateless — just a
        cooldown timer).
        """
        with self._lock:
            if not self._enabled:
                return
            now = time.monotonic()
            actions: list[tuple[str, dict]] = []
            for device_id, st in self._devices.items():
                if st.last_any_action_ts == 0:
                    continue
                if now - st.last_any_action_ts < HEAL_AFTER_S:
                    continue
                kwargs: dict = {}
                if st.current_gop_s != GOP_DEFAULT_S:
                    kwargs["i_frame_interval"] = GOP_DEFAULT_S
                    st.current_gop_s = GOP_DEFAULT_S
                if st.current_width_rung != 0:
                    kwargs["max_width"] = MAX_WIDTH_LADDER[0]
                    st.current_width_rung = 0
                if kwargs:
                    st.last_any_action_ts = now  # don't loop on this device
                    # Also clear the L1 event window. Otherwise, stale
                    # request_keyframe timestamps from BEFORE to heal
                    # (typically the same burst that triggered L2
                    # promotion in the first place) carry over and the
                    # very next L1 fire would instantly re-promote to
                    # L2 — defeating the purpose of healing.
                    st.recent_keyframe_ts.clear()
                    actions.append((device_id, kwargs))
        for device_id, kwargs in actions:
            _log.info("L4 heal on %s: %s", device_id, kwargs)
            self._dispatch_reconfigure(device_id, **kwargs)

    # ── internal: dispatch scrcpy reconfigure off the caller thread ─
    @staticmethod
    def _dispatch_reconfigure(device_id: str, **kwargs) -> None:
        """Fire scrcpy_clients.reconfigure on a worker so we never block
        the caller. PeerManager reattach is handled by the lifecycle
        layer (which is what scrcpy_clients.reconfigure calls into)."""
        # Drop None values — they mean "no change" but scrcpy.scrcpyclients
        # validates ints, so let it use its current value instead.
        cleaned = {k: v for k, v in kwargs.items() if v is not None}
        if not cleaned:
            return

        def _apply() -> None:
            try:
                from scrcpy.webrtc.peer_manager import get_peer_manager
                new_client, _ = scrcpy_clients.reconfigure(device_id, **cleaned)
                if new_client and new_client.alive:
                    get_peer_manager().reattach_for_device(device_id, new_client)
            except (RuntimeError, OSError, ConnectionError, ImportError) as exc:
                _log.warning(
                    "quality reconfigure failed for %s (%s): %s",
                    device_id, cleaned, exc,
                )

        threading.Thread(
            target=_apply,
            name=f"qctl-{device_id[:8]}",
            daemon=True,
        ).start()


_instance: Optional[QualityController] = None


def get_controller() -> QualityController:
    """Process-wide singleton. Thread-safe."""
    global _instance
    if _instance is None:
        _instance = QualityController()
    return _instance


# ── L4 heal background tick ────────────────────────────────────────
_heal_thread: Optional[threading.Thread] = None
_heal_stop = threading.Event()


def start_heal_loop(interval_s: float = 10.0) -> None:
    """Spin up the L4 periodic tick. Idempotent; safe to call at boot."""
    global _heal_thread
    if _heal_thread is not None and _heal_thread.is_alive():
        return

    def _loop() -> None:
        while not _heal_stop.wait(interval_s):
            try:
                get_controller().heal()
            except Exception as exc:  # noqa: BLE001
                _log.warning("heal loop error: %s", exc)

    _heal_stop.clear()
    _heal_thread = threading.Thread(
        target=_loop, name="qctl-heal", daemon=True
    )
    _heal_thread.start()


def stop_heal_loop() -> None:
    _heal_stop.set()
