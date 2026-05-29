import os
import cv2
import time
import socket
import struct
import logging
import threading
import random
import numpy as np
from contextlib import suppress
from av.codec import CodecContext
from av.error import InvalidDataError
from typing import Any, Callable, Optional, Tuple, Union
from adbutils import AdbConnection, AdbDevice, adb

from . import scrcpyconst as const
from .scrcpyconst import (
    EVENT_AUDIO,
    EVENT_DISCONNECT,
    EVENT_FRAME,
    EVENT_H264,
    EVENT_INIT,
    SCRCPY_AUDIO_CHANNELS,
    SCRCPY_AUDIO_CODEC,
    SCRCPY_AUDIO_SAMPLE_RATE,
    LOCK_SCREEN_ORIENTATION_UNLOCKED,
    SCRCPY_SERVER_PATH,
    SCRCPY_SERVER_VERSION,
)
from .scrcpycontrol import ControlSender

_log = logging.getLogger(__name__)

# ── scrcpy video-stream wire-format constants ─────────────────────────
# Shared by ``Client._consume_video_packets``; promoted to module level
# so the linter doesn't flag them as in-function uppercase locals.
# Layout per upstream ``doc/develop.md``:
#
#   Session packet (MSB=1): 12 bytes, no payload, carries new dims.
#   Media packet   (MSB=0): 12-byte header + payload of ``size`` bytes.
_VIDEO_HEADER_SIZE = 12
_VIDEO_FLAG_SESSION = 1 << 63
_VIDEO_FLAG_CONFIG = 1 << 62
_VIDEO_FLAG_KEYFRAME = 1 << 61
_VIDEO_PTS_MASK = (1 << 61) - 1
# Largest media payload we'll accept before treating the size field as
# corrupted. 64 MiB is well above any reasonable single-frame H.264
# payload (a Pixel 10 IDR at 16 Mbps is ~2 MiB).
_VIDEO_MAX_PAYLOAD = 64 * 1024 * 1024


class Client:
    def __init__(
        self,
        device: Optional[Union[AdbDevice, str, any]] = None,
        max_width: int = 0,
        bitrate: int = 8000000,
        max_fps: int = 0,
        i_frame_interval: int = 2,
        flip: bool = False,
        block_frame: bool = False,
        stay_awake: bool = False,
        lock_screen_orientation: int = LOCK_SCREEN_ORIENTATION_UNLOCKED,
        connection_timeout: int = 3000,
        encoder_name: Optional[str] = None,
        # Audio is currently unused (no JPEG/WS hub forwarding it; the
        # WebRTC track is video-only). Default off to spare device CPU
        # and network. Set ``audio_enabled=True`` explicitly to re-enable
        # once a WebRTC audio track is wired up.
        audio_enabled: bool = False,
    ):
        assert max_width >= 0, "max_width must be greater than or equal to 0"
        assert bitrate >= 0, "bitrate must be greater than or equal to 0"
        assert max_fps >= 0, "max_fps must be greater than or equal to 0"
        assert -1 <= lock_screen_orientation <= 3, (
            "lock_screen_orientation must be LOCK_SCREEN_ORIENTATION_*"
        )
        assert connection_timeout >= 0, (
            "connection_timeout must be greater than or equal to 0"
        )
        assert encoder_name in [
            None,
            "OMX.google.h264.encoder",
            "OMX.qcom.video.encoder.avc",
            "c2.qti.avc.encoder",
            "c2.android.avc.encoder",
        ]

        self.flip = flip
        self.max_width = max_width
        self.bitrate = bitrate
        self.max_fps = max_fps
        # IDR cadence (seconds). The adaptive quality controller mutates
        # this between 1 s (tightened) and 2 s (default) under sustained
        # mosaic pressure. See services/quality_controller.py.
        self.i_frame_interval = max(1, int(i_frame_interval) or 2)
        self.block_frame = block_frame
        self.stay_awake = stay_awake
        self.lock_screen_orientation = lock_screen_orientation
        self.connection_timeout = connection_timeout
        self.encoder_name = encoder_name
        self.audio_enabled = audio_enabled
        self.control_enabled = True
        self.control_available = False
        self._control_fallback_attempted = False

        # Connect to device
        if device is None:
            device = adb.device_list()[0]
        elif isinstance(device, str):
            device = adb.device(serial=device)

        self.device = device
        self.listeners = dict(frame=[], init=[], disconnect=[], audio=[], h264=[])
        # Guards listeners list mutations vs. reads from the stream
        # loop. Without this lock, ``reconfigure`` -> reattach paths can
        # call ``add_listener`` / ``remove_listener`` while
        # ``__send_to_listeners`` is iterating, which on CPython
        # eventually raises "list changed during iteration" or worse,
        # silently skips listeners. The lock is held only across the
        # list-slice copy in ``__send_to_listeners`` so listener
        # callbacks themselves run outside the lock — they're allowed
        # to mutate freely.
        self._listeners_lock = threading.Lock()
        self.scid = random.randint(0, 0x7FFFFFFF)
        self.scid_hex = f"{self.scid:08x}"
        # Lifecycle epoch: a fresh UUID4 per Client instance, used by
        # ``services.scrcpy_lifecycle`` to tag every ``scrcpy_status``
        # broadcast with the originating Client's identity. The frontend
        # uses it to ignore late-arriving ``disconnected`` events from a
        # PREVIOUS Client whose ``stop()`` only flushed its event after
        # a refresh-triggered ``get_client()`` already built a fresh one
        # — without this, the stale disconnect would tear down the
        # freshly restored peer (see ``test_refresh_joint_pipeline.py``).
        import uuid as _uuid_mod
        self.lifecycle_epoch = _uuid_mod.uuid4().hex
        self.socket_name = f"scrcpy_{self.scid:08x}"

        # User accessible
        self.last_frame: Optional[np.ndarray] = None
        self.resolution: Optional[Tuple[int, int]] = None
        self.device_name: Optional[str] = None
        self.control = ControlSender(self)

        # H.264 bootstrap cache so late-joining WebRTC peers can initialise
        # their decoders without waiting for the next periodic keyframe.
        # Filled by ``__stream_loop`` from the raw Annex-B bytes.
        self._last_sps_nal: Optional[bytes] = None
        self._last_pps_nal: Optional[bytes] = None
        self._last_keyframe_chunk: Optional[bytes] = None

        # Need to destroy
        self.alive = False
        self.__server_stream: Optional[AdbConnection] = None
        self.__listener_socket: Optional[socket.socket] = None
        self.__video_socket: Optional[socket.socket] = None
        self.__audio_socket: Optional[socket.socket] = None
        self.control_socket: Optional[socket.socket] = None
        self.control_socket_lock = threading.Lock()
        self.audio_codec: Optional[str] = None
        self.video_codec: Optional[str] = None
        self.audio_sample_rate = SCRCPY_AUDIO_SAMPLE_RATE
        self.audio_channels = SCRCPY_AUDIO_CHANNELS
        self.clipboard_text: Optional[str] = None
        self.clipboard_event = threading.Event()
        self.clipboard_ack_event = threading.Event()
        self.clipboard_ack_sequence: int = const.SEQUENCE_INVALID
        self._device_message_loop_thread = None

        # Available if start with threaded or daemon_threaded
        self.stream_loop_thread = None
        self.audio_loop_thread = None

    def __open_listen_socket(self) -> int:
        listen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen_socket.bind(("127.0.0.1", 0))
        listen_socket.listen(3)
        listen_socket.settimeout(self.connection_timeout / 1000.0)
        self.__listener_socket = listen_socket
        return listen_socket.getsockname()[1]

    def __prepare_reverse_tunnel(self) -> bool:
        try:
            listen_port = self.__open_listen_socket()
            remote = f"localabstract:{self.socket_name}"
            local = f"tcp:{listen_port}"
            self.device.reverse(remote, local)
            _log.info("Prepared adb reverse tunnel: %s -> %s", remote, local)
            return True
        except Exception as exc:
            _log.error("Failed to prepare adb reverse tunnel: %s", exc)
            if self.__listener_socket is not None:
                try:
                    self.__listener_socket.close()
                except (OSError, socket.error):
                    pass
                finally:
                    self.__listener_socket = None
            return False

    def __cleanup_partial_connection(self) -> None:
        if self.control_socket is not None:
            try:
                self.control_socket.close()
            except (OSError, socket.error):
                pass
            finally:
                self.control_socket = None

        if self.__video_socket is not None:
            try:
                self.__video_socket.close()
            except (OSError, socket.error):
                pass
            finally:
                self.__video_socket = None

        if self.__audio_socket is not None:
            try:
                self.__audio_socket.close()
            except (OSError, socket.error):
                pass
            finally:
                self.__audio_socket = None

        if self.__listener_socket is not None:
            try:
                self.__listener_socket.close()
            except (OSError, socket.error):
                pass
            finally:
                self.__listener_socket = None

    @staticmethod
    def __recv_exact(sock: socket.socket, size: int) -> bytes:
        buf = bytearray()
        while len(buf) < size:
            chunk = sock.recv(size - len(buf))
            if not chunk:
                break
            buf.extend(chunk)
        return bytes(buf)

    def __handle_device_message(self, msg_type: int, payload: bytes) -> bool:
        if msg_type == const.DEVICE_MSG_TYPE_CLIPBOARD:
            if len(payload) < 4:
                _log.warning("Incomplete clipboard device message")
                return False
            (length,) = struct.unpack(">I", payload[:4])
            text_bytes = payload[4:4 + length]
            if len(text_bytes) != length:
                _log.warning("Clipboard device message truncated")
                return False
            try:
                self.clipboard_text = text_bytes.decode("utf-8")
            except UnicodeDecodeError:
                self.clipboard_text = text_bytes.decode("utf-8", errors="replace")
            self.clipboard_event.set()
            _log.info("Received clipboard text from device (%d bytes)", length)
            return True

        if msg_type == const.DEVICE_MSG_TYPE_ACK_CLIPBOARD:
            if len(payload) < 8:
                _log.warning("Incomplete clipboard ack message")
                return False
            (sequence,) = struct.unpack(">Q", payload[:8])
            self.clipboard_ack_sequence = sequence
            self.clipboard_ack_event.set()
            _log.info("Received clipboard ack: %d", sequence)
            return True

        if msg_type == const.DEVICE_MSG_TYPE_UHID_OUTPUT:
            _log.debug("Ignoring UHID output device message")
            return True

        _log.warning("Unknown device message type: %d", msg_type)
        return False

    def __device_message_loop(self) -> None:
        if self.control_socket is None:
            return

        sock = self.control_socket
        sock.settimeout(1.0)
        while self.alive and self.control_socket is sock:
            try:
                head = self.__recv_exact(sock, 1)
                if len(head) != 1:
                    continue
                msg_type = head[0]

                if msg_type == const.DEVICE_MSG_TYPE_CLIPBOARD:
                    length_bytes = self.__recv_exact(sock, 4)
                    if len(length_bytes) != 4:
                        _log.warning("Failed to read clipboard length")
                        continue
                    (length,) = struct.unpack(">I", length_bytes)
                    payload = self.__recv_exact(sock, length)
                    if len(payload) != length:
                        _log.warning("Failed to read clipboard payload")
                        continue
                    self.__handle_device_message(msg_type, length_bytes + payload)
                    continue

                if msg_type == const.DEVICE_MSG_TYPE_ACK_CLIPBOARD:
                    payload = self.__recv_exact(sock, 8)
                    if len(payload) != 8:
                        _log.warning("Failed to read clipboard ack payload")
                        continue
                    self.__handle_device_message(msg_type, payload)
                    continue

                if msg_type == const.DEVICE_MSG_TYPE_UHID_OUTPUT:
                    payload = self.__recv_exact(sock, 4)
                    if len(payload) != 4:
                        _log.warning("Failed to read UHID payload header")
                        continue
                    uhid_id, uhid_size = struct.unpack(">HH", payload)
                    data = self.__recv_exact(sock, uhid_size)
                    if len(data) != uhid_size:
                        _log.warning("Failed to read UHID payload data")
                        continue
                    self.__handle_device_message(msg_type, payload + data)
                    continue

                _log.warning("Received unhandled device message type: %d", msg_type)
            except socket.timeout:
                continue
            except (ConnectionError, OSError) as e:
                if self.alive:
                    _log.error(f"Device message loop exception: {e}")
                break

    def __init_server_connection(self) -> bool:
        if self.__listener_socket is None:
            _log.error("Listen socket is not prepared")
            return False

        try:
            self.__video_socket, _ = self.__listener_socket.accept()
            self.__video_socket.settimeout(self.connection_timeout / 1000.0)

            self.device_name = self.__recv_exact(self.__video_socket, 64).decode("utf-8").rstrip("\x00")
            if not len(self.device_name):
                _log.error("Did not receive Device Name!")
                self.__cleanup_partial_connection()
                return False

            # scrcpy 3.x/4.x video preamble (per Genymobile/scrcpy develop.md):
            #   4 bytes  codec id (ASCII: "h264" / "h265" / " av1")
            #   12 bytes session packet:
            #       byte 0  : 0b1000_0000 (session-packet flag, top bit)
            #                 + bit-0 = client_resized_flag
            #       bytes 1-3: padding
            #       bytes 4-7: video width  (u32 BE)
            #       bytes 8-11: video height (u32 BE)
            # Older clients used to read 12 bytes here treating the layout as
            # codec_id(4) + width(4) + height(4) �?that lost 4 bytes and
            # offset every subsequent frame header by 4 bytes.
            preamble = self.__recv_exact(self.__video_socket, 16)
            if len(preamble) != 16:
                _log.error("Did not receive video codec metadata!")
                self.__cleanup_partial_connection()
                return False

            self.video_codec = preamble[:4].decode("ascii", errors="ignore").strip("\x00")
            session_flags = preamble[4]
            width, height = struct.unpack(">II", preamble[8:16])
            _log.info(
                "Video codec accepted: %s (%dx%d) session_flags=0x%02x",
                self.video_codec or "unknown",
                width,
                height,
                session_flags,
            )
            if 0 < width <= 10000 and 0 < height <= 10000:
                self.resolution = (width, height)
            else:
                _log.warning(
                    "Ignoring suspicious video metadata resolution: %dx%d",
                    width,
                    height,
                )
            self.__video_socket.setblocking(False)

            if self.audio_enabled:
                self.__audio_socket, _ = self.__listener_socket.accept()
                self.__audio_socket.settimeout(self.connection_timeout / 1000.0)
                if self.__audio_socket is not None:
                    codec_id = self.__recv_exact(self.__audio_socket, 4)
                    if len(codec_id) != 4:
                        _log.warning("Audio socket opened but codec metadata was incomplete")
                        try:
                            self.__audio_socket.close()
                        except (OSError, socket.error):
                            pass
                        finally:
                            self.__audio_socket = None
                            self.audio_enabled = False
                    else:
                        self.audio_codec = codec_id.decode("ascii", errors="ignore").strip("\x00")
                        if self.audio_codec != "raw":
                            _log.warning(
                                "Unsupported audio codec from server: %s", self.audio_codec
                            )
                            try:
                                self.__audio_socket.close()
                            except (OSError, socket.error):
                                pass
                            finally:
                                self.__audio_socket = None
                                self.audio_enabled = False
                        else:
                            _log.info("Audio codec accepted: %s", self.audio_codec)
                            self.__audio_socket.setblocking(True)
                            self.audio_sample_rate = SCRCPY_AUDIO_SAMPLE_RATE
                            self.audio_channels = SCRCPY_AUDIO_CHANNELS

            if self.control_enabled:
                self.control_socket, _ = self.__listener_socket.accept()
                self.control_socket.setblocking(True)
            else:
                self.control_socket = None
            return True
        except Exception as e:
            _log.error(f"Failed to initialize scrcpy connection: {e}")
            self.__cleanup_partial_connection()
            return False

    def __deploy_server(self) -> bool:
        server_file_path = SCRCPY_SERVER_PATH
        jar_name = os.path.basename(server_file_path)
        _log.info("==== __deploy_server called ====")
        _log.info(
            f"Preparing to push {server_file_path} to /data/local/tmp/{jar_name}"
        )

        if not os.path.exists(server_file_path):
            _log.error(f"scrcpy-server.jar file does not exist: {server_file_path}")
            return False

        try:
            self.device.sync.push(server_file_path, f"/data/local/tmp/{jar_name}")
            _log.info("scrcpy-server.jar pushed successfully")
        except Exception as e:
            _log.error(f"Failed to push scrcpy-server.jar: {e}")
            return False

        if not self.__prepare_reverse_tunnel():
            return False

        # IMPORTANT: keep this option list MINIMAL — pass ONLY options that
        # differ from the scrcpy server's own defaults, exactly like the
        # official scrcpy client does. Restating defaults is not merely
        # redundant: the scrcpy 4.0 server has a fixed-size native stack
        # buffer in its argument handling, and a long option list overflows
        # it on some ROMs. The Samsung SM-G9860 (One UI / Android 13,
        # OMX.qcom.video.encoder.avc) reliably SIGABRTs with
        # "stack corruption detected (-fstack-protector) -> Aborted" the
        # instant we pad the command with default-valued keys
        # (send_frame_meta=true, send_device_meta=true, tunnel_forward=false,
        # show_touches=false, stay_awake=false, power_off_on_close=false,
        # the invalid send_codec_meta, ...). The
        # server then sends nothing and the client reports
        # "Did not receive video codec metadata!". Other devices (e.g. POCO)
        # tolerate the longer list, which is why it looked device-specific.
        # The trimmed list below matches the proven-good official invocation.
        # Verified streaming on SM-G9860 (R5CN30EQKNM) on 2026-05-29.
        #
        # Defaults we deliberately rely on instead of restating them:
        #   send_frame_meta=true   (we need per-frame PTS/keyframe flags)
        #   send_device_meta=true  (we read the 64-byte device name first)
        #   tunnel_forward=false   (we set up an adb reverse tunnel)
        #   send_codec_meta is NOT a valid 4.0 option at all — codec metadata
        #   is always part of the video header (our 16-byte preamble read).
        commands = [
            f"CLASSPATH=/data/local/tmp/{jar_name}",
            "app_process",
            "/",
            "com.genymobile.scrcpy.Server",
            SCRCPY_SERVER_VERSION,
            f"scid={self.scid_hex}",
            "log_level=info",
            # Only override the encoder knobs we actually care about; omit
            # any that equal the server default (max_size=0, max_fps=0).
            f"max_size={self.max_width}" if self.max_width else None,
            f"max_fps={self.max_fps}" if self.max_fps else None,
            f"video_bit_rate={self.bitrate}" if self.bitrate else None,
            # Force the H.264 encoder to emit an IDR at our chosen cadence
            # (default MediaCodec interval is ~10 s; the adaptive quality
            # controller tightens this to 1 s under mosaic pressure).
            f"video_codec_options=i-frame-interval={self.i_frame_interval}",
            f"control={'true' if self.control_enabled else 'false'}",
            f"audio={'true' if self.audio_enabled else 'false'}",
            f"audio_codec={SCRCPY_AUDIO_CODEC}" if self.audio_enabled else None,
            "audio_source=output" if self.audio_enabled else None,
            # NOT redundant: required for the "read device clipboard" feature.
            # autosync=true (scrcpy default) dedupes clipboard messages, so an
            # explicit TYPE_GET_CLIPBOARD of an unchanged clipboard gets no
            # reply and ControlSender.get_clipboard() times out returning "".
            "clipboard_autosync=false",
        ]
        commands = [cmd for cmd in commands if cmd is not None]

        try:
            self.__server_stream = self.device.shell(commands, stream=True)
            # Let the server bootstrap before we start connecting sockets.
            self.__server_stream.read(10)
            _log.info(f"scrcpy server command started: {commands}")
            return True
        except Exception as e:
            _log.error(f"Failed to start scrcpy server: {e}")
            return False

    def __drain_server_log(self) -> None:
        """Read whatever scrcpy-server printed (stdout+stderr are merged on the
        adb shell stream) and log it. This is the device-side error message —
        the only place a capture/encoder failure (SIGABRT, "Encoding error",
        codec/display issues) is actually reported."""
        stream = self.__server_stream
        if stream is None:
            return
        sock = getattr(stream, "conn", None)
        data = b""
        try:
            if sock is not None:
                sock.settimeout(1.0)
                try:
                    while len(data) < 16384:
                        chunk = sock.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                except (socket.timeout, OSError):
                    pass
            else:
                data = stream.read(8192) or b""
        except Exception as exc:  # noqa: BLE001
            _log.warning("Could not read scrcpy-server output: %s", exc)
            return
        text = data.decode("utf-8", errors="replace").strip()
        if text:
            _log.error("scrcpy-server (device-side) said:\n%s", text)
        else:
            _log.error(
                "scrcpy-server produced no diagnostic output before failing "
                "(likely a native abort on this device/ROM)."
            )

    def start(self, threaded: bool = False, daemon_threaded: bool = False) -> bool:
        if self.alive:
            _log.warning("Client already started")
            return True

        for attempt in range(2):
            if not self.__deploy_server():
                self.stop()
                return False

            if self.__init_server_connection():
                break

            # Connection/handshake failed (e.g. "Did not receive video codec
            # metadata!"). The REAL cause is whatever scrcpy-server printed on
            # the device — surface it before we tear the stream down, otherwise
            # device-specific failures (some Samsung/One UI builds SIGABRT on
            # capture init) are invisible and look like a generic timeout.
            self.__drain_server_log()
            self.stop()
            if self.control_enabled and not self._control_fallback_attempted:
                _log.warning(
                    "scrcpy control channel is unavailable on this device; retrying with control disabled"
                )
                self._control_fallback_attempted = True
                self.control_enabled = False
                continue
            return False

        self.alive = True
        self.control_available = self.control_socket is not None
        self.__send_to_listeners(EVENT_INIT)

        if self.control_available:
            self._device_message_loop_thread = threading.Thread(
                target=self.__device_message_loop, daemon=True
            )
            self._device_message_loop_thread.start()

        if self.audio_enabled and self.__audio_socket is not None:
            self.audio_loop_thread = threading.Thread(
                target=self.__stream_audio_loop, daemon=True
            )
            self.audio_loop_thread.start()

        if threaded or daemon_threaded:
            self.stream_loop_thread = threading.Thread(
                target=self.__stream_loop, daemon=daemon_threaded
            )
            self.stream_loop_thread.start()
            return True
        else:
            self.__stream_loop()
            return True

    def stop(self) -> None:
        # Fire EVENT_DISCONNECT for callers that initiated an orderly
        # shutdown. The stream loop only fires it on socket EOF �?without
        # this hook the listener chain (scrcpy_lifecycle �?scrcpy_status
        # broadcast �?frontend PC teardown) never runs on explicit stop.
        was_alive = self.alive
        self.alive = False
        if was_alive:
            with suppress(Exception):
                self.__send_to_listeners(EVENT_DISCONNECT)

        if self.__server_stream is not None:
            try:
                self.__server_stream.close()
                _log.info("Closed __server_stream")
            except (OSError, IOError) as e:
                _log.warning(f"Error closing __server_stream: {e}")
            finally:
                self.__server_stream = None

        if self.control_socket is not None:
            try:
                self.control_socket.close()
                _log.info("Closed control_socket")
            except (OSError, socket.error) as e:
                _log.warning(f"Error closing control_socket: {e}")
            finally:
                self.control_socket = None

        if self.__video_socket is not None:
            try:
                self.__video_socket.close()
                _log.info("Closed __video_socket")
            except (OSError, socket.error) as e:
                _log.warning(f"Error closing __video_socket: {e}")
            finally:
                self.__video_socket = None

        if self.__audio_socket is not None:
            try:
                self.__audio_socket.close()
                _log.info("Closed __audio_socket")
            except (OSError, socket.error) as e:
                _log.warning(f"Error closing __audio_socket: {e}")
            finally:
                self.__audio_socket = None

        # Drop every listener callback. Without this the closures
        # registered by ``services.scrcpy_lifecycle`` (and any future
        # listener-based wiring) hold a strong reference back to
        # ``self`` via captured locals — a reference cycle that keeps
        # the dead Client around until Python's cycle GC catches up,
        # and meanwhile the old client could still receive listener
        # callbacks if anything still referenced it. Idempotent:
        # clearing an already-empty dict is harmless.
        with self._listeners_lock:
            for cls in self.listeners:
                self.listeners[cls].clear()

    def __stream_loop(self) -> None:
        if self.__video_socket is None:
            _log.error("Video socket is not available, stream loop will exit")
            return

        codec = CodecContext.create("h264", "r")
        buf = bytearray()  # accumulates bytes across non-blocking recv() calls
        # Per-packet layout (12-byte header) lives in
        # ``Client._consume_video_packets``; the loop here just buffers
        # recv() chunks and delegates parsing.

        while self.alive and self.__video_socket is not None:
            try:
                chunk = self.__video_socket.recv(0x10000)
                if chunk == b"":
                    _log.error("Video stream is disconnected")
                    break
                buf.extend(chunk)

                # Delegate the per-packet parsing to a static helper so
                # the logic is testable in isolation (see
                # tests/test_video_parser.py). The helper:
                #   - Consumes session packets (MSB=1) silently while
                #     updating ``self.resolution`` so downstream metrics
                #     refresh on rotation / encoder reset.
                #   - Returns a list of ready media packets — each a
                #     (frame_bytes, pts_us, is_keyframe, is_config) tuple.
                #   - Mutates ``buf`` in place to remove consumed bytes.
                #   - Returns ``None`` if a header looked bogus and the
                #     buffer was dropped for recovery.
                ready = Client._consume_video_packets(buf, self)
                if ready is None:
                    continue
                for frame_bytes, pts_us, is_keyframe, is_config in ready:
                    self._frame_count = getattr(self, "_frame_count", 0) + 1
                    if self._frame_count <= 3:
                        _log.info(
                            "video frame #%d size=%d pts=%dus key=%s cfg=%s starts=%s",
                            self._frame_count, len(frame_bytes), pts_us,
                            is_keyframe, is_config, frame_bytes[:8].hex(),
                        )
                    elif (is_config or is_keyframe) and _log.isEnabledFor(logging.DEBUG):
                        _log.debug(
                            "video frame #%d size=%d pts=%dus key=%s cfg=%s",
                            self._frame_count, len(frame_bytes), pts_us,
                            is_keyframe, is_config,
                        )
                    self.__handle_video_frame(
                        codec, frame_bytes, pts_us, is_keyframe, is_config
                    )
            except (BlockingIOError, InvalidDataError):
                time.sleep(0.01)
                if not self.block_frame:
                    self.__send_to_listeners(EVENT_FRAME, None)
            except (ConnectionError, OSError) as e:
                if self.alive:
                    # stop() is responsible for firing EVENT_DISCONNECT
                    # itself; we used to fire it here too, which made
                    # downstream listeners (scrcpy_lifecycle broadcaster,
                    # peer_manager teardown) run twice on every socket
                    # error — the frontend received two "disconnected"
                    # events in quick succession.
                    self.stop()
                _log.error(f"Stream exception: {e}")
                break

    @staticmethod
    def _consume_video_packets(buf: bytearray, owner: "Client | None" = None):
        """Drain complete scrcpy video packets from ``buf``.

        Per scrcpy server protocol (``doc/develop.md``), the first byte
        of every 12-byte header dispatches between two packet types:

          Session packet (MSB = 1):
            byte 0:   1......R (top bit = session flag, R = client_resized_flag,
                                middle bits 1-6 are padding)
            bytes 1-3: padding
            bytes 4-7: new video width (u32 BE)
            bytes 8-11: new video height (u32 BE)
            NO payload follows — 12 bytes total. Emitted at startup
            (preamble) AND whenever the capture session resets mid-stream — typically on device rotation or after
            TYPE_RESET_VIDEO. Reference: ``Streamer.writeSessionMeta``
            / ``SurfaceCapture.consumeReset(CLIENT_RESIZED)``.

          Media packet (MSB = 0):
            byte 0:   0CK_____ (C = config, K = keyframe)
            bits 60-0 (across bytes 0-7): PTS in microseconds
            bytes 8-11: u32 BE H.264 payload size
            [payload] follows for ``size`` bytes.

        Returns
        -------
        list of (frame_bytes, pts_us, is_keyframe, is_config) tuples
            One entry per complete media packet drained from ``buf``.
            Session packets are consumed silently; if ``owner`` is
            provided its ``.resolution`` is updated.
        ``None`` on bogus header (caller should ``continue`` and let
            the next ``recv()`` resync the stream).

        Pre-fix the loop blindly treated every 12-byte header as a
        media packet — mid-stream session packets decoded to junk
        sizes (~3.7 GB), the recovery dropped the whole buffer, and
        downstream consumers lost every frame until the next IDR.
        """
        ready: list = []
        while True:
            if len(buf) < _VIDEO_HEADER_SIZE:
                return ready
            head_lo, head_hi = struct.unpack(">QI", buf[:_VIDEO_HEADER_SIZE])
            if head_lo & _VIDEO_FLAG_SESSION:
                # Session packet — bytes 4-7 = new width, bytes 8-11 =
                # new height. Width sits in the low 32 bits of head_lo
                # (because we unpacked the whole 8-byte chunk together);
                # height is in ``head_hi``.
                new_w = head_lo & 0xFFFFFFFF
                new_h = head_hi
                if owner is not None and new_w and new_h:
                    owner.resolution = (int(new_w), int(new_h))
                    _log.info(
                        "Mid-stream session packet: resolution=%dx%d "
                        "(client_resized=%d)",
                        new_w, new_h, (head_lo >> 56) & 1,
                    )
                del buf[:_VIDEO_HEADER_SIZE]
                continue
            size = head_hi
            if size == 0 or size > _VIDEO_MAX_PAYLOAD:
                _log.warning(
                    "Video header looks bogus (size=%d, first16=%s); "
                    "buffered=%d. Dropping buffer.",
                    size, bytes(buf[:16]).hex(), len(buf),
                )
                buf.clear()
                return None
            if len(buf) < _VIDEO_HEADER_SIZE + size:
                return ready  # incomplete — wait for more bytes
            frame_bytes = bytes(buf[_VIDEO_HEADER_SIZE : _VIDEO_HEADER_SIZE + size])
            del buf[: _VIDEO_HEADER_SIZE + size]
            is_config = bool(head_lo & _VIDEO_FLAG_CONFIG)
            is_keyframe = bool(head_lo & _VIDEO_FLAG_KEYFRAME)
            pts_us = head_lo & _VIDEO_PTS_MASK
            ready.append((frame_bytes, pts_us, is_keyframe, is_config))

    def __handle_video_frame(
        self,
        codec,
        frame_bytes: bytes,
        pts_us: int,
        is_keyframe: bool,
        is_config: bool = False,
    ) -> None:
        """One scrcpy access unit (single complete H.264 frame) just arrived.

        Fans out to two consumers:

        * ``EVENT_H264``: raw Annex-B bytes + PTS �?the WebRTC passthrough
          track wraps these into ``av.Packet`` for aiortc.
        * ``EVENT_FRAME``: decoded BGR ndarray �?only computed when somebody
          is listening (the snapshot endpoint is the sole long-term consumer).
        """
        # Cache the parameter-set + IDR bootstrap so late-joining WebRTC peers
        # can initialise their decoders without waiting for scrcpy's next
        # periodic keyframe.
        #
        # The Annex-B NAL scan is only useful when SPS/PPS could actually be
        # present: in config packets, and in keyframes (which prepend
        # parameter sets via ``send_frame_meta=true``). Skipping it on
        # P-frames eliminates the full byte scan on every steady-state
        # frame — at 60 fps × N devices that's a real CPU saving in the
        # stream loop, where every microsecond counts.
        if is_config:
            self.__update_h264_bootstrap_cache(frame_bytes)
        elif is_keyframe:
            self._last_keyframe_chunk = frame_bytes
            self.__update_h264_bootstrap_cache(frame_bytes)
        # else: P-frame — no parameter sets possible, skip the scan.

        if self.listeners.get(EVENT_H264):
            # Pass PTS in microseconds; the track converts to RTP 90 kHz.
            self.__send_to_listeners(EVENT_H264, frame_bytes, pts_us, is_keyframe)

        # Decode only when somebody actually needs BGR pixels.
        packets = codec.parse(frame_bytes)
        if not self.listeners.get(EVENT_FRAME):
            return
        for packet in packets:
            for frame in codec.decode(packet):
                arr = frame.to_ndarray(format="bgr24")  # type: ignore[attr-defined]
                if self.flip:
                    arr = cv2.flip(arr, 1)
                self.last_frame = arr
                self.resolution = (arr.shape[1], arr.shape[0])
                self.__send_to_listeners(EVENT_FRAME, arr)

    def __update_h264_bootstrap_cache(self, frame_bytes: bytes) -> None:
        """Update cached SPS / PPS NALs by scanning the Annex-B frame bytes."""
        n = len(frame_bytes)
        i = 0
        starts = []
        while i + 3 <= n:
            if frame_bytes[i] == 0 and frame_bytes[i + 1] == 0:
                if frame_bytes[i + 2] == 1:
                    starts.append(i + 3)
                    i += 3
                    continue
                if i + 4 <= n and frame_bytes[i + 2] == 0 and frame_bytes[i + 3] == 1:
                    starts.append(i + 4)
                    i += 4
                    continue
            i += 1
        for idx, payload_start in enumerate(starts):
            end = starts[idx + 1] - 3 if idx + 1 < len(starts) else n
            # Trim trailing zero bytes that precede the next start code.
            while end > payload_start and frame_bytes[end - 1] == 0:
                end -= 1
            if end <= payload_start:
                continue
            nal = frame_bytes[payload_start:end]
            t = nal[0] & 0x1F
            if t == 7:
                self._last_sps_nal = nal
            elif t == 8:
                self._last_pps_nal = nal

    def get_h264_bootstrap(self) -> Optional[bytes]:
        """Annex-B blob (SPS + PPS + last IDR frame) for late-joining peers.

        Returns ``None`` if any piece is missing �?the caller should then
        simply wait for the next periodic keyframe from scrcpy.
        """
        sps, pps, idr = self._last_sps_nal, self._last_pps_nal, self._last_keyframe_chunk
        if not (sps and pps and idr):
            return None
        sc = b"\x00\x00\x00\x01"
        return sc + sps + sc + pps + (idr if idr.startswith(b"\x00\x00") else sc + idr)

    def get_h264_parameter_sets(self) -> Optional[bytes]:
        """Annex-B blob containing just the cached SPS + PPS NALs.

        Used by the WebRTC listener to prepend parameter sets to every IDR
        frame. Scrcpy emits SPS/PPS only as a one-shot config packet at the
        start of the stream; periodic IDRs that follow have no parameter
        sets attached. If the browser decoder ever loses state mid-stream
        (packet loss, GPU reset, tab background), it cannot recover on the
        next IDR alone — it needs SPS/PPS in-band. Returning them here
        lets the listener splice them in front of each keyframe.
        """
        sps, pps = self._last_sps_nal, self._last_pps_nal
        if not (sps and pps):
            return None
        sc = b"\x00\x00\x00\x01"
        return sc + sps + sc + pps

    def __stream_audio_loop(self) -> None:
        if self.__audio_socket is None:
            _log.error("Audio socket is not available, audio loop will exit")
            return

        try:
            while self.alive and self.__audio_socket is not None:
                header = self.__recv_exact(self.__audio_socket, 12)
                if len(header) < 12:
                    break
                _, packet_size = struct.unpack(">QI", header)
                if packet_size <= 0:
                    continue
                payload = self.__recv_exact(self.__audio_socket, packet_size)
                if len(payload) < packet_size:
                    break
                self.__send_to_listeners(EVENT_AUDIO, payload, self.audio_sample_rate, self.audio_channels)
        except (ConnectionError, OSError) as e:
            if self.alive:
                _log.error(f"Audio stream exception: {e}")

    def add_listener(self, cls: str, listener: Callable[..., Any]) -> None:
        with self._listeners_lock:
            self.listeners[cls].append(listener)

    def remove_listener(self, cls: str, listener: Callable[..., Any]) -> None:
        with self._listeners_lock:
            try:
                self.listeners[cls].remove(listener)
            except ValueError:
                # Already removed (e.g. double-detach from
                # scrcpy_lifecycle cleanup). Idempotent on purpose.
                pass

    def __send_to_listeners(self, cls: str, *args, **kwargs) -> None:
        # Snapshot inside the lock so we never iterate a list that's
        # being mutated. Callbacks run OUTSIDE the lock — they may
        # legitimately call back into add/remove_listener (e.g. the
        # disconnect path), which would deadlock with a non-reentrant
        # lock if held during iteration.
        with self._listeners_lock:
            callbacks = list(self.listeners[cls])
        for cb in callbacks:
            cb(*args, **kwargs)





