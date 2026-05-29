"""H.264 passthrough MediaStreamTrack.

aiortc 1.14 routes ``track.recv()`` returning ``av.Packet`` through
``H264Encoder.pack(packet)``, which performs RFC 6184 packetization
without re-encoding. We exploit that to send scrcpy's H.264 stream
straight to the browser:

    scrcpy server → adb tunnel → core.py (frame-by-frame demux)
                  → EVENT_H264 (bytes, pts_us, is_keyframe)
                  → this track → av.Packet → aiortc pack() → RTP
                  → browser HW decode

This requires scrcpy's ``send_frame_meta=true`` option (see
``scrcpy.scrcpycore``) — that's what lets us pull complete frames + PTS off
the socket. Without frame metadata scrcpy's wire format is just
"opaque bytes, no boundaries" and per-frame timestamping is impossible.

Late-joining peers (``attach_video_source`` after scrcpy has been
running for a while) get the cached SPS+PPS+IDR bootstrap replayed
immediately, so the browser's H.264 decoder can initialise without
waiting for the next periodic keyframe.
"""

from __future__ import annotations

import asyncio
import fractions
import logging
import threading
from typing import Any, Optional

_log = logging.getLogger(__name__)

VIDEO_CLOCK_RATE = 90000          # RTP video clock rate per RFC 7741
_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)
_SCRCPY_PTS_BASE = fractions.Fraction(1, 1_000_000)  # μs → seconds

try:
    import av  # type: ignore
    from aiortc import MediaStreamTrack  # type: ignore

    _AIORTC_OK = True
except Exception as _exc:  # noqa: BLE001
    _log.info("aiortc/av unavailable, ScrcpyVideoTrack in stub mode: %s", _exc)
    MediaStreamTrack = object  # type: ignore
    av = None  # type: ignore
    _AIORTC_OK = False


class ScrcpyVideoTrack(MediaStreamTrack):  # type: ignore[misc]
    """One scrcpy access unit per ``recv()`` — wrapped as ``av.Packet``.

    Producer side (scrcpy stream thread) calls :meth:`push_frame` with
    the full Annex-B bytes of a single H.264 frame plus its μs PTS.
    """

    kind = "video"

    def __init__(self) -> None:
        if not _AIORTC_OK:
            raise RuntimeError("aiortc not installed; cannot use ScrcpyVideoTrack")
        super().__init__()
        # Queue size sized to cover worst-case ICE handshake delay
        # (~1-2 seconds at 60 fps = ~120 frames) before recv() starts
        # draining. Below this we'd evict the bootstrap (which is the
        # FIRST item enqueued under the listener's ``attach_idr_seen``
        # gate) on overflow, and the browser would never receive a
        # decodable keyframe.
        self._queue: "asyncio.Queue[Any]" = asyncio.Queue(maxsize=128)
        self._loop = asyncio.get_event_loop()
        self._lock = threading.Lock()
        self._pts_offset: Optional[int] = None   # subtract from incoming μs so PTS starts at 0
        self._last_pts: int = -1                  # 90 kHz, monotonic guard

    def push_bootstrap(self, annexb: bytes) -> None:
        """Enqueue a pre-built SPS+PPS+IDR blob as the FIRST live packet.

        Previously this stashed the blob in a dedicated ``_bootstrap_packet``
        slot consulted by ``recv()`` before falling through to the live
        queue. That design had a real race: when ``recv()`` was already
        suspended in ``await self._queue.get()`` by the time the listener
        fired for the first IDR, ``_store_bootstrap`` set the slot but
        did NOT wake the queue awaiter — the very next ``_enqueue`` (the
        first live P-frame) woke it instead, and ``recv()`` returned the
        P-frame BEFORE the bootstrap. The browser then received RTP
        packets in the order
            [P-frame at pts>0]  [SPS+PPS+IDR at pts=0]
        which the H.264 decoder can't recover from cleanly — symptom is
        "firstFrame paints, every subsequent paint is black even though
        frames keep arriving and the paint callback keeps firing". The
        race triggers reliably on a refresh-attach because the scrcpy
        Client is reused and the first frame arrives within milliseconds
        of ``recv()`` starting; on a fresh deploy (manual Stop+Start)
        scrcpy server boot delays the first frame past ``recv()`` entry,
        so the slot path happened to work — which is exactly why the bug
        looked like "only refresh is broken".

        Fix: push the bootstrap through the same queue as live frames.
        The listener's ``attach_idr_seen`` gate guarantees nothing else
        has been enqueued before this for the current attach, so the
        bootstrap is unambiguously the first item ``recv()`` returns.
        Queue is sized to 128 packets to survive worst-case ICE delay
        without evicting the bootstrap on overflow.
        """
        if not annexb:
            return
        packet = self._build_packet(annexb, pts_90khz=0)
        if packet is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._enqueue, packet)
        except RuntimeError:
            pass

    def push_frame(self, annexb: bytes, pts_us: int, _is_keyframe: bool) -> None:
        """Producer entry. ``pts_us`` is scrcpy's microsecond PTS."""
        if not annexb:
            return
        # Skip scrcpy's config NAL (SPS+PPS-only frame, always sent with
        # pts_us=0). It carries no displayable content — its SPS/PPS are
        # already covered by the bootstrap blob and by the listener's
        # per-IDR parameter-set prefix. Pushing it here would lock
        # ``_pts_offset = 0``, making the next real frame's pts_us (which
        # is the device's monotonic clock, often billions of μs since
        # boot) get normalised to a huge 90 kHz value. The browser's
        # jitter buffer than sees "next frame is hours in the future"
        # and stalls — exact symptom: ``firstFrame`` paints once then
        # the picture freezes. Letting the first IDR or P-frame set the
        # offset instead keeps PTS deltas in the millisecond range.
        if pts_us == 0:
            return
        with self._lock:
            if self._pts_offset is None:
                self._pts_offset = pts_us
            # Normalise to 90 kHz, ensure monotonic.
            pts_90khz = int((pts_us - self._pts_offset) * 90 / 1000)
            # The bootstrap packet (SPS+PPS+IDR) sits at RTP TS=0 in its
            # own slot. RFC 6184 says RTP packets sharing a timestamp
            # are parts of the SAME access unit — so if the first live
            # frame also lands at PTS=0 (which it would, because
            # ``_pts_offset = pts_us`` makes the first delta zero), the
            # browser concatenates bootstrap + first P-frame into one
            # malformed AU and the decoder outputs garbage pixels for
            # the rest of the session. Symptom: ``firstFrame`` paints
            # (the standalone bootstrap IDR decodes fine), then every
            # subsequent paint shows black because each P-frame is
            # decoded against the previous corrupt output. Active
            # interaction (60 fps) recovers fast because the corrupt
            # state is short-lived; idle scrcpy (10 fps) sticks. Bump
            # to PTS=1 so they're distinct access units.
            if pts_90khz <= 0:
                pts_90khz = 1
            if pts_90khz <= self._last_pts:
                pts_90khz = self._last_pts + 1
            self._last_pts = pts_90khz
        packet = self._build_packet(annexb, pts_90khz=pts_90khz)
        if packet is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._enqueue, packet)
        except RuntimeError:
            pass

    @staticmethod
    def _build_packet(annexb: bytes, *, pts_90khz: int) -> Optional[Any]:
        try:
            packet = av.Packet(annexb)
        except Exception as exc:  # noqa: BLE001
            _log.debug("av.Packet construction failed: %s", exc)
            return None
        packet.pts = pts_90khz
        packet.time_base = _TIME_BASE
        return packet

    def _enqueue(self, packet: Any) -> None:
        """Runs on the asyncio loop. Drops oldest live frame on overflow
        (liveness > completeness). The bootstrap slot is independent and
        never gets evicted."""
        q = self._queue
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            q.put_nowait(packet)
        except asyncio.QueueFull:
            pass

    async def recv(self):  # type: ignore[override]
        # Single source of truth — the queue. The bootstrap (if any)
        # was enqueued by ``push_bootstrap`` as the FIRST item, before
        # any live frame, so the browser decoder always sees SPS+PPS+IDR
        # before any P-frame. The previous separate ``_bootstrap_packet``
        # slot had a wake-up race with ``await self._queue.get()`` that
        # could deliver the first P-frame BEFORE the bootstrap — see
        # ``push_bootstrap`` docstring for the gory details.
        return await self._queue.get()
