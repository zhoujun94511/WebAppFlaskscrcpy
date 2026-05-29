"""RTCPeerConnection lifecycle manager keyed by (browser sid, device id).

One PC per ``(sid, device_id)`` pair. A single browser tab (sid) can now
hold multiple PeerSessions — one per device it's streaming concurrently.
Each PC owns:
- a video sender bound to a :class:`RawH264Track`, fed by an
  ``EVENT_H264`` listener on the scrcpy client;
- an "input"  DataChannel (unreliable, unordered)  →  control socket
- an "adb"    DataChannel (reliable,   ordered)    →  clipboard / file push

The manager is sync-friendly (all public methods take ``sid``, ``device_id``,
SDP/ICE dicts) but internally schedules work on the shared
:class:`AsyncRunner`.

External interface
------------------
``handle_offer(sid, device_id, sdp_offer)``      → ``answer_dict``
``handle_ice(sid, device_id, candidate_dict)``   → ``None``
``close(sid, device_id=None)``                   → ``None`` (None ⇒ close all for sid)
``attach_video_source(sid, device_id, client)``  → ``None``
``detach_video_source(sid, device_id)``          → ``None``

The signaling layer (:mod:`api.webrtc`) is the only caller. The tab-wide
``close(sid)`` form is reserved for the ``disconnect`` event — every other
operation requires a concrete ``device_id``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable, Dict, Optional, Tuple

from scrcpy.scrcpyconst import EVENT_H264

from .async_runner import AsyncRunner, get_runner

_log = logging.getLogger(__name__)


def _has_parameter_sets(annexb: bytes) -> bool:
    """Return True if the Annex-B blob already contains an SPS or PPS NAL.

    Scans for start codes (00 00 00 01 or 00 00 01) and inspects the first
    byte of each NAL — bits 0–4 are the NAL unit type. 7 = SPS, 8 = PPS.
    Bounded by ~64 bytes worth of scanning in practice since parameter sets,
    if present, sit at the front of the frame. We early-exit on first hit.
    """
    if not annexb:
        return False
    n = len(annexb)
    i = 0
    # Cap the search to a generous prefix so we don't walk huge IDR payloads.
    end = min(n, 256)
    while i + 3 <= end:
        if annexb[i] == 0 and annexb[i + 1] == 0:
            if annexb[i + 2] == 1:
                payload = i + 3
            elif i + 4 <= end and annexb[i + 2] == 0 and annexb[i + 3] == 1:
                payload = i + 4
            else:
                i += 1
                continue
            if payload < n:
                t = annexb[payload] & 0x1F
                if t in (7, 8):
                    return True
            i = payload
            continue
        i += 1
    return False


try:
    from aiortc import RTCConfiguration, RTCIceCandidate, RTCPeerConnection, RTCSessionDescription  # type: ignore
    from aiortc.sdp import candidate_from_sdp  # type: ignore

    _AIORTC_OK = True
except Exception as _exc:  # noqa: BLE001
    _log.info("aiortc unavailable, PeerManager in stub mode: %s", _exc)
    _AIORTC_OK = False


class PeerSession:
    """One PC + its bound tracks/channels + scrcpy listener wiring."""

    def __init__(self, sid: str, runner: AsyncRunner) -> None:
        if not _AIORTC_OK:
            raise RuntimeError("aiortc not installed")
        self.sid = sid
        self.runner = runner
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self.device_id: Optional[str] = None
        self.video_track: Any = None
        self.input_channel: Any = None
        self.adb_channel: Any = None
        self._scrcpy_listener: Optional[Callable[[bytes], None]] = None
        self._scrcpy_client: Any = None
        self._on_input_message: Optional[Callable[[str], None]] = None
        self._on_adb_message: Optional[Callable[[Any], None]] = None
        self._on_ice_local: Optional[Callable[[Dict[str, Any]], None]] = None
        self._on_closed: Optional[Callable[[str], None]] = None
        self._bitrate_ctrl: Any = None
        self._lock = threading.Lock()

        @self.pc.on("connectionstatechange")
        async def _on_state() -> None:
            state = self.pc.connectionState
            _log.info("[%s] PC state=%s", self.sid, state)
            if state in ("failed", "closed", "disconnected"):
                if self._on_closed:
                    self._on_closed(state)

        @self.pc.on("icecandidate")
        async def _on_ice(event: Any) -> None:
            cand = getattr(event, "candidate", None)
            if cand is None or self._on_ice_local is None:
                return
            self._on_ice_local({
                "candidate": cand.to_sdp() if hasattr(cand, "to_sdp") else str(cand),
                "sdpMid": getattr(cand, "sdpMid", None),
                "sdpMLineIndex": getattr(cand, "sdpMLineIndex", None),
            })

    # ----- callbacks setup -----
    def on_local_ice(self, cb: Callable[[Dict[str, Any]], None]) -> None:
        self._on_ice_local = cb

    def on_closed(self, cb: Callable[[str], None]) -> None:
        self._on_closed = cb

    def on_input_message(self, cb: Callable[[str], None]) -> None:
        self._on_input_message = cb

    def on_adb_message(self, cb: Callable[[Any], None]) -> None:
        self._on_adb_message = cb

    # ----- async core -----
    async def _setup_tracks_and_channels(self) -> None:
        from aiortc import RTCRtpSender  # type: ignore

        from .video_track import ScrcpyVideoTrack

        self.video_track = ScrcpyVideoTrack()
        transceiver = self.pc.addTransceiver(self.video_track, direction="sendonly")  # type: ignore[arg-type]
        # Force H.264 codec preference. aiortc's default SDP offer/answer puts
        # VP8 first, and Chrome happily accepts VP8 — at which point the
        # browser tries to decode our raw H.264 bytes as VP8 and drops every
        # frame ("framesDropped == framesReceived"). Pin the negotiation to
        # H.264 so the browser's H.264 hardware decoder gets the packets.
        caps = RTCRtpSender.getCapabilities("video")
        if caps is not None:
            h264 = [c for c in caps.codecs if c.mimeType.lower() == "video/h264"]
            if h264:
                try:
                    transceiver.setCodecPreferences(h264)
                    _log.info("[%s] forced H.264 codec preference", self.sid)
                except (ValueError, AttributeError) as exc:
                    _log.warning("[%s] setCodecPreferences failed: %s", self.sid, exc)

        self.input_channel = self.pc.createDataChannel(
            "input",
            ordered=False,
            maxRetransmits=0,
        )
        self.adb_channel = self.pc.createDataChannel("adb", ordered=True)

        @self.input_channel.on("message")
        def _on_input(msg: Any) -> None:
            if self._on_input_message and isinstance(msg, (str, bytes)):
                self._on_input_message(msg if isinstance(msg, str) else msg.decode("utf-8", "ignore"))

        @self.adb_channel.on("message")
        def _on_adb(msg: Any) -> None:
            if self._on_adb_message:
                self._on_adb_message(msg)

    async def handle_offer(self, sdp: str) -> Dict[str, str]:
        await self._setup_tracks_and_channels()
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
        answer = await self.pc.createAnswer()
        await self.pc.setLocalDescription(answer)
        # NOTE: SDP munging (b=AS + x-google-* hints, as done by
        # hqw700/ScrcpyOverWebRTC) is intentionally NOT applied here.
        # That munging is meaningful in their server-offer / browser-answer
        # flow — the browser's own libwebrtc honours the Google hints when
        # it is the sender. Our direction is reversed (browser offers,
        # aiortc answers), aiortc ignores ``x-google-*``, and adding the
        # extra fmtp params has been observed to break Chrome's H.264
        # decode (frames received but never decoded, RTCP PLI loop).
        # Bandwidth control lives in scrcpy's encoder; ``/api/reconfigure``
        # is the supported knob.
        return {"sdp": self.pc.localDescription.sdp, "type": self.pc.localDescription.type}

    async def add_ice(self, candidate: Dict[str, Any]) -> None:
        cand_str = candidate.get("candidate")
        if not cand_str:
            return
        try:
            cand = candidate_from_sdp(cand_str.split(":", 1)[1] if cand_str.startswith("candidate:") else cand_str)
            cand.sdpMid = candidate.get("sdpMid")
            cand.sdpMLineIndex = candidate.get("sdpMLineIndex")
            await self.pc.addIceCandidate(cand)
        except (ValueError, RuntimeError, AttributeError) as exc:
            _log.warning("[%s] addIceCandidate failed: %s", self.sid, exc)

    async def close(self) -> None:
        if self._bitrate_ctrl is not None:
            try:
                await self._bitrate_ctrl.stop()
            except (RuntimeError, asyncio.CancelledError):
                pass
            self._bitrate_ctrl = None
        try:
            await self.pc.close()
        except (RuntimeError, AttributeError) as exc:
            _log.warning("[%s] pc.close error: %s", self.sid, exc)

    async def enable_adaptive_bitrate(self, initial_bps: int, on_target_bps: Callable[[int], None]) -> None:
        from .bitrate_controller import BitrateController

        if self._bitrate_ctrl is not None:
            await self._bitrate_ctrl.stop()
        self._bitrate_ctrl = BitrateController(
            self.pc,
            initial_bps=initial_bps,
            on_change=on_target_bps,
            device_id=self.device_id,
        )
        self._bitrate_ctrl.start()

    async def disable_adaptive_bitrate(self) -> None:
        if self._bitrate_ctrl is None:
            return
        await self._bitrate_ctrl.stop()
        self._bitrate_ctrl = None

    # ----- scrcpy listener wiring (called from the request-handling thread) -----
    def attach_video_source(self, scrcpy_client: Any) -> None:
        """Subscribe the track to the scrcpy client's raw H.264 Annex-B stream.

        Replays the cached SPS+PPS+IDR bootstrap (if any) to the track *before*
        the live listener is registered, so the browser has parameter sets
        when the next live frame arrives — no decoder stall, no waiting for
        scrcpy's next periodic keyframe.
        """
        with self._lock:
            if self._scrcpy_client is scrcpy_client:
                return
            if self._scrcpy_client is not None:
                self.detach_video_source()

            track = self.video_track
            bootstrap_done = [False]    # closure-mutable flag
            # Diagnostic counter — proves the listener is actually getting
            # called for THIS session post-attach. Pairs with the frontend
            # "boot trace" to spot the case where post-refresh the new PC
            # is alive but no frames arrive at it (silent track stall).
            frame_count = [0]
            sid_local = self.sid

            def _try_push_bootstrap() -> bool:
                """Idempotent: push SPS+PPS+IDR to the track once the scrcpy
                cache has all three NALs. Called from the listener on every
                early frame so cold-start (cache empty at attach time)
                eventually delivers a bootstrap exactly once.

                Returns True iff this call was the one that performed the
                push — used by the listener to suppress the duplicate IDR
                push that would otherwise follow.
                """
                if bootstrap_done[0] or track is None:
                    return False
                blob = scrcpy_client.get_h264_bootstrap()
                if not blob:
                    return False
                track.push_bootstrap(blob)
                bootstrap_done[0] = True
                _log.info("[%s] sent H.264 bootstrap (%d bytes)", self.sid, len(blob))
                return True

            # NO eager bootstrap push — see ``_listener`` below for the
            # full reasoning. tl;dr: on refresh attach, the cached IDR
            # is STALE (from before refresh) and the live frames coming
            # next are P-frames that reference the encoder's current
            # state, not the cached IDR. Pushing stale bootstrap then
            # live P-frames gives the decoder garbage references.
            # Instead, we wait for the FIRST IDR after attach and push
            # bootstrap from THAT (cache is updated before listener
            # fires, so the blob will contain the fresh IDR bytes).
            attach_idr_seen = [False]

            def _listener(annexb: bytes, pts_us: int, is_keyframe: bool) -> None:
                if track is None:
                    return
                # Log the first 3 frames + every 60th after, so we can
                # spot a listener that stops getting called (or never gets
                # called) after a page-refresh re-attach. INFO-level so it
                # shows in default backend output without --verbose-logs.
                frame_count[0] += 1
                fc = frame_count[0]
                if fc <= 3 or fc % 60 == 0:
                    _log.info(
                        "[%s] H264 frame #%d (keyframe=%s, len=%d)",
                        sid_local, fc, is_keyframe, len(annexb),
                    )
                # Until we've seen the first IDR after THIS attach, drop
                # everything. P-frames before our anchor IDR reference
                # frames the decoder doesn't have (the encoder is mid -
                # stream from a previous session), so feeding them to
                # the browser produces garbage pixels — visually black,
                # but ``requestVideoFrameCallback`` still fires for each
                # one. ``reset_video`` (issued after add_listener below)
                # forces scrcpy to emit a fresh IDR within ~50 ms so
                # this drop window is short on refresh; on cold start
                # the natural startup IDR arrives within one frame.
                if not attach_idr_seen[0]:
                    if not is_keyframe:
                        return
                    attach_idr_seen[0] = True
                    # First IDR after attach: scrcpy already updated its
                    # ``_last_keyframe_chunk`` cache with these very
                    # bytes BEFORE calling us (see core.py
                    # __process_h264_frame ordering). So
                    # ``_try_push_bootstrap`` now returns a clean
                    # SPS+PPS+freshIDR blob — push it as the dedicated
                    # bootstrap packet and suppress the duplicate
                    # push_frame for this same IDR (sending the IDR
                    # twice — once via bootstrap, once via the live
                    # queue — confuses the decoder's reference state).
                    if _try_push_bootstrap():
                        return
                    # Fallback (parameter sets cache missing for some
                    # reason): fall through and push the IDR as a live
                    # frame; the ``if is_keyframe and not
                    # _has_parameter_sets`` branch below will prepend
                    # SPS+PPS if it can.
                # For every IDR (except the one we just suppressed above),
                # prepend cached SPS+PPS unless they are already present in
                # the frame bytes. Scrcpy emits parameter sets exactly once
                # (as a config packet) and never re-prefixes subsequent
                # keyframes — so without this, the browser decoder cannot
                # recover from mid-stream desync (packet loss, GPU stall,
                # tab background → foreground) and freezes until the
                # connection is rebuilt. Splicing SPS+PPS in front of every
                # IDR makes each keyframe self-decodable.
                payload = annexb
                if is_keyframe and not _has_parameter_sets(payload):
                    ps = scrcpy_client.get_h264_parameter_sets()
                    if ps:
                        payload = ps + payload
                track.push_frame(payload, pts_us, is_keyframe)

            scrcpy_client.add_listener(EVENT_H264, _listener)
            self._scrcpy_listener = _listener
            self._scrcpy_client = scrcpy_client
            _log.info("[%s] attached to scrcpy client %s", self.sid, id(scrcpy_client))

            # Force a fresh IDR right after attach. With
            # ``i-frame-interval=2``, the natural cadence of IDRs is
            # one every ~2 seconds — so without this, a refresh-attach
            # joining mid-cycle would drop ~2 seconds of P-frames (see
            # listener's ``attach_idr_seen`` gate) before the picture
            # painted anything. ``reset_video`` (opcode 17) cuts that
            # window to ~50 ms. One-shot per attach, well below the
            # 30/sec storm-warn threshold in control.py.
            try:
                ctl = getattr(scrcpy_client, "control", None)
                if ctl is not None:
                    ctl.reset_video()
                    _log.info("[%s] requested fresh IDR via reset_video", self.sid)
            except (OSError, AttributeError, RuntimeError) as reset_exc:
                _log.debug("[%s] reset_video request failed: %s", self.sid, reset_exc)

    def detach_video_source(self) -> None:
        with self._lock:
            if self._scrcpy_client is None or self._scrcpy_listener is None:
                return
            try:
                self._scrcpy_client.remove_listener(EVENT_H264, self._scrcpy_listener)
            except (KeyError, ValueError, AttributeError) as exc:
                _log.warning("[%s] detach listener error: %s", self.sid, exc)
            self._scrcpy_listener = None
            self._scrcpy_client = None



class PeerManager:
    """Thread-safe registry of PeerSessions keyed by ``(sid, device_id)``.

    A single browser tab (sid) can hold multiple PeerSessions, one per
    device it's concurrently streaming.
    """

    def __init__(self) -> None:
        self._sessions: Dict[Tuple[str, str], PeerSession] = {}
        self._lock = threading.Lock()
        self._runner: Optional[AsyncRunner] = None

    def runner(self) -> AsyncRunner:
        if self._runner is None:
            self._runner = get_runner()
        return self._runner

    def get_or_create(self, sid: str, device_id: str) -> PeerSession:
        if not _AIORTC_OK:
            raise RuntimeError("aiortc not installed; install requirements-webrtc.txt")
        if not device_id:
            raise ValueError("device_id required")
        key = (sid, device_id)
        with self._lock:
            sess = self._sessions.get(key)
            if sess is None:
                # PeerSession constructs an RTCPeerConnection, which needs the
                # asyncio loop in the runner thread.
                sess = self.runner().run_sync(self._make_session(sid))
                sess.device_id = device_id
                self._sessions[key] = sess
            return sess

    async def _make_session(self, sid: str) -> PeerSession:
        return PeerSession(sid, self.runner())

    def get(self, sid: str, device_id: str) -> Optional[PeerSession]:
        return self._sessions.get((sid, device_id))

    def handle_offer(self, sid: str, device_id: str, sdp: str) -> Dict[str, str]:
        sess = self.get_or_create(sid, device_id)
        return self.runner().run_sync(sess.handle_offer(sdp), timeout=15)

    def handle_ice(self, sid: str, device_id: str, candidate: Dict[str, Any]) -> None:
        sess = self.get(sid, device_id)
        if sess is None:
            return
        self.runner().submit(sess.add_ice(candidate))

    def attach_video(self, sid: str, device_id: str, scrcpy_client: Any) -> None:
        sess = self.get(sid, device_id)
        if sess is None:
            return
        sess.attach_video_source(scrcpy_client)

    def set_adaptive(self, device_id: str, enabled: bool, *, initial_bps: int = 8_000_000,
                     on_target_bps: Optional[Callable[[int], None]] = None) -> int:
        """Toggle TWCC-driven adaptive bitrate on every PC bound to ``device_id``."""
        with self._lock:
            keys = [k for k, s in self._sessions.items() if s.device_id == device_id]
        count = 0
        for key in keys:
            sess = self._sessions.get(key)
            if sess is None:
                continue
            if enabled and on_target_bps is not None:
                self.runner().run_sync(sess.enable_adaptive_bitrate(initial_bps, on_target_bps))
            else:
                self.runner().run_sync(sess.disable_adaptive_bitrate())
            count += 1
        return count

    def reattach_for_device(self, device_id: str, new_client: Any) -> int:
        """Detach all sessions bound to ``device_id`` and re-attach to ``new_client``."""
        count = 0
        with self._lock:
            keys = [k for k, s in self._sessions.items() if s.device_id == device_id]
        for key in keys:
            sess = self._sessions.get(key)
            if sess is None:
                continue
            sess.detach_video_source()
            sess.attach_video_source(new_client)
            count += 1
        return count

    def close(self, sid: str, device_id: Optional[str] = None) -> None:
        """Close one session (when ``device_id`` is given) or every session
        for this tab (when omitted — used on socket disconnect)."""
        if device_id is None:
            # Close-all-for-sid path (browser disconnect, explicit "close tab").
            with self._lock:
                keys = [k for k in self._sessions if k[0] == sid]
                sessions = [self._sessions.pop(k) for k in keys]
            for sess in sessions:
                self._teardown(sess)
            return
        key = (sid, device_id)
        with self._lock:
            sess = self._sessions.pop(key, None)
        if sess is None:
            return
        self._teardown(sess)

    def _teardown(self, sess: PeerSession) -> None:
        sess.detach_video_source()
        try:
            self.runner().run_sync(sess.close(), timeout=5)
        except (RuntimeError, TimeoutError, asyncio.CancelledError) as exc:
            _log.warning("[%s/%s] close error: %s", sess.sid, sess.device_id, exc)

    def close_all(self) -> None:
        with self._lock:
            sids = sorted({k[0] for k in self._sessions})
        for sid in sids:
            self.close(sid)

    def close_device(self, device_id: str) -> int:
        """Tear down every PeerSession bound to ``device_id`` regardless of sid.

        Used by the reservation layer to forcibly disconnect every browser
        watching a device when its reservation is released/expired.
        """
        with self._lock:
            keys = [k for k, s in self._sessions.items() if s.device_id == device_id]
            sessions = [self._sessions.pop(k) for k in keys]
        for sess in sessions:
            self._teardown(sess)
        return len(sessions)


_singleton: Optional[PeerManager] = None
_singleton_lock = threading.Lock()


def get_peer_manager() -> PeerManager:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = PeerManager()
        return _singleton
