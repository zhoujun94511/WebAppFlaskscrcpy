"""Interactive ``adb shell`` session backing the xterm.js terminal.

Implementation
--------------
We talk to ``adbd`` over its ``shell:v2`` service directly. That gives us:

* a real device-side PTY (prompt, echo, line editing, Ctrl+C all work);
* a window-size message (frame id=5) so the device's PTY width tracks
  xterm's column count — without it ``ls`` / ``top`` / ``vim`` format
  for a hard-coded 80×24 and the output wraps badly inside the narrow
  xterm host;
* separate stdout (id=1) / stderr (id=2) streams + a final exit code
  (id=3) frame on clean shell exit.

Wire format (one frame):

    [ id : 1 byte ] [ length : 4 bytes LE ] [ payload : length bytes ]

Message IDs we care about (host ↔ device):

    0  stdin       → write to child
    1  stdout      ← read from child
    2  stderr      ← read from child
    3  exit code   ← 1-byte payload, child terminated
    4  close stdin → write
    5  window size → write, payload is "ROWSxCOLS,XPIXxYPIX" ASCII

We bypass adbutils' high-level ``shell()`` wrapper (which sends plain
``shell:`` — no PTY on modern adbd) and grab the raw transport with
``open_transport()`` + ``send_command('shell,v2,raw,pty:')``. The order
``raw,pty`` matters: some adbd builds reject ``pty,raw``. We've tested
this on MIUI/Android 14 — works.

Sessions are keyed by Socket.IO sid (one terminal per browser tab).
"""

from __future__ import annotations

import logging
import struct
import threading
from typing import Callable, Dict, Optional

import adbutils

_log = logging.getLogger(__name__)

# shell:v2 message IDs
_MSG_STDIN = 0
_MSG_STDOUT = 1
_MSG_STDERR = 2
_MSG_EXIT = 3
_MSG_CLOSE_STDIN = 4
_MSG_WINDOW_SIZE = 5


class AdbShellSession:
    """One interactive adb shell connection + a reader thread.

    ``device_id`` is part of the public state because the registry uses
    it to decide whether to reuse an existing session when the same sid
    re-opens for a *different* device (it shouldn't — drop the old).
    """

    def __init__(
        self,
        session_id: str,
        device_id: str,
        on_output: Callable[[bytes], None],
        on_close: Callable[[str], None],
    ) -> None:
        self.session_id = session_id
        self.device_id = device_id
        self._on_output = on_output
        self._on_close = on_close
        self._conn: Optional[adbutils.AdbConnection] = None  # type: ignore[name-defined]
        self._reader: Optional[threading.Thread] = None
        self._alive = False
        self._closed_emitted = False
        self._write_lock = threading.Lock()
        # Cached so a resize() before the shell is fully started doesn't
        # silently no-op — we apply it once the connection is up.
        self._cols = 0
        self._rows = 0

    @property
    def alive(self) -> bool:
        return self._alive and self._conn is not None and not self._conn.closed

    def start(self) -> None:
        if self._conn is not None:
            return
        _log.info("[%s] opening shell:v2 on %s", self.session_id, self.device_id)
        try:
            device = adbutils.adb.device(serial=self.device_id)
            conn = device.open_transport()
            # ``v2`` = framed protocol with window-size + exit code support
            # ``raw`` = no CR/LF translation at the host side
            # ``pty`` = allocate a PTY on the device side
            # Order matters on some adbd builds (raw,pty works; pty,raw hangs).
            conn.send_command("shell,v2,raw,pty:")
            conn.check_okay()
            self._conn = conn
        except adbutils.AdbError as exc:
            raise RuntimeError(f"adb shell open failed: {exc}") from exc
        except (OSError, ConnectionError) as exc:
            raise RuntimeError(f"adb shell open failed: {exc}") from exc

        self._alive = True
        # If resize() was called before start() (xterm's ResizeObserver
        # can fire either order depending on mount timing), apply the
        # cached geometry now.
        if self._cols and self._rows:
            self._send_frame(_MSG_WINDOW_SIZE, f"{self._rows}x{self._cols},0x0")

        self._reader = threading.Thread(
            target=self._pump, name=f"adb-shell-{self.session_id[:8]}", daemon=True
        )
        self._reader.start()

    # ----- frame I/O -------------------------------------------------

    def _send_frame(self, msg_id: int, payload) -> None:
        """Write one shell:v2 frame to the wire. Must hold ``_write_lock``."""
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        elif payload is None:
            payload = b""
        header = struct.pack("<BI", msg_id, len(payload))
        # Direct socket sendall — adbutils' .send() goes through a wrapper
        # we don't need here, and we already serialise via _write_lock at
        # the public-API level.
        self._conn.conn.sendall(header + payload)  # type: ignore[union-attr]

    def _pump(self) -> None:
        """Read frames forever, demux stdout/stderr → on_output, exit → close."""
        conn = self._conn
        if conn is None:
            return
        sock = conn.conn
        buf = bytearray()

        def fill(n: int) -> bool:
            """Block until ``buf`` has at least ``n`` bytes. Returns False on EOF."""
            while len(buf) < n:
                try:
                    chunk = sock.recv(4096)
                except (OSError, ConnectionError):
                    return False
                if not chunk:
                    return False
                buf.extend(chunk)
            return True

        # Defensive cap on a single frame's payload length. The wire
        # format declares ``length`` as an u32, so without a cap a
        # malformed or hostile adbd response could send length=
        # 0xFFFFFFFF and either block ``sock.recv`` forever waiting
        # for 4 GiB of data or OOM the process growing ``buf``. 1 MiB
        # is far above any legitimate terminal frame (stdout chunks
        # come in as ≤4 KiB on real Android shells).
        _MAX_FRAME_PAYLOAD = 1 << 20

        try:
            while self._alive:
                if not fill(5):
                    break
                msg_id = buf[0]
                length = int.from_bytes(buf[1:5], "little")
                if length > _MAX_FRAME_PAYLOAD:
                    _log.warning(
                        "[%s] shell frame length %d exceeds cap %d — closing",
                        self.session_id, length, _MAX_FRAME_PAYLOAD,
                    )
                    break
                if not fill(5 + length):
                    break
                payload = bytes(buf[5:5 + length])
                del buf[:5 + length]

                if msg_id == _MSG_STDOUT or msg_id == _MSG_STDERR:
                    # We don't demux stderr separately — xterm renders one
                    # combined stream and stderr usually gets colored by
                    # the program itself (e.g. red ``ls`` errors). Saves
                    # adding a second pipe to the frontend for now.
                    try:
                        self._on_output(payload)
                    except Exception as exc:  # noqa: BLE001
                        _log.warning("[%s] on_output error: %s", self.session_id, exc)
                elif msg_id == _MSG_EXIT:
                    # Child terminated cleanly. payload[0] is the exit code.
                    code = payload[0] if payload else 0
                    _log.info("[%s] shell exit code %d", self.session_id, code)
                    break
                # Other IDs (1=stdout already handled, plus any vendor
                # extension) we silently skip — we still consumed the
                # frame so the stream stays aligned.
        finally:
            self._mark_closed(reason="eof")

    # ----- public write API -----------------------------------------

    def write(self, data: bytes) -> None:
        """Forward keystrokes from xterm to the device shell's stdin."""
        with self._write_lock:
            if not self.alive or self._conn is None:
                return
            try:
                self._send_frame(_MSG_STDIN, data)
            except (OSError, ConnectionError) as exc:
                _log.warning("[%s] write error: %s", self.session_id, exc)
                self.close(reason="broken_pipe")

    def resize(self, cols: int, rows: int) -> None:
        """Tell the device PTY about the host xterm geometry.

        Previously a no-op. Now sends a shell:v2 window-size frame, which
        adbd forwards as ``ioctl(TIOCSWINSZ)`` on the slave PTY. Tools
        that read ``stty size`` (ls auto-columns, top, vim, less, …)
        pick up the new size immediately.
        """
        cols, rows = int(cols or 0), int(rows or 0)
        if cols <= 0 or rows <= 0:
            return
        self._cols, self._rows = cols, rows
        with self._write_lock:
            if not self.alive or self._conn is None:
                return  # cached; will be sent in start()
            try:
                self._send_frame(_MSG_WINDOW_SIZE, f"{rows}x{cols},0x0")
            except (OSError, ConnectionError) as exc:
                _log.warning("[%s] resize error: %s", self.session_id, exc)

    def close(self, reason: str = "client_close") -> None:
        with self._write_lock:
            if not self._alive:
                self._mark_closed(reason)
                return
            self._alive = False
        if self._conn is not None:
            try:
                self._conn.close()
            except (OSError, ValueError):
                pass
        self._mark_closed(reason)

    def _mark_closed(self, reason: str) -> None:
        if self._closed_emitted:
            return
        self._closed_emitted = True
        self._alive = False
        try:
            self._on_close(reason)
        except Exception as exc:  # noqa: BLE001
            _log.warning("[%s] on_close error: %s", self.session_id, exc)


class SessionRegistry:
    def __init__(self) -> None:
        self._sessions: Dict[str, AdbShellSession] = {}
        self._lock = threading.Lock()

    def open(
        self,
        session_id: str,
        device_id: str,
        on_output: Callable[[bytes], None],
        on_close: Callable[[str], None],
    ) -> AdbShellSession:
        """Open (or hand back) a session for ``session_id``.

        If a session already exists for this sid AND it targets the same
        device, return it. Otherwise, — different device, or the previous
        one is dead — tear down the old and start a fresh one.
        """
        to_close: Optional[AdbShellSession] = None
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                if existing.alive and existing.device_id == device_id:
                    return existing
                to_close = existing
                self._sessions.pop(session_id, None)
            sess = AdbShellSession(session_id, device_id, on_output, on_close)
            self._sessions[session_id] = sess

        if to_close is not None:
            try:
                to_close.close(reason="replaced")
            except Exception as exc:  # noqa: BLE001
                _log.warning("[%s] stale-session close error: %s", session_id, exc)

        sess.start()
        return sess

    def get(self, session_id: str) -> Optional[AdbShellSession]:
        return self._sessions.get(session_id)

    def write(self, session_id: str, data: bytes) -> bool:
        sess = self.get(session_id)
        if sess is None:
            return False
        sess.write(data)
        return True

    def resize(self, session_id: str, cols: int, rows: int) -> None:
        sess = self.get(session_id)
        if sess is not None:
            sess.resize(cols, rows)

    def close(self, session_id: str, reason: str = "client_close") -> None:
        with self._lock:
            sess = self._sessions.pop(session_id, None)
        if sess is not None:
            sess.close(reason)

    def shutdown_all(self) -> None:
        with self._lock:
            sids = list(self._sessions.keys())
        for sid in sids:
            self.close(sid, reason="shutdown")


_registry: Optional[SessionRegistry] = None
_registry_lock = threading.Lock()


def get_session_registry() -> SessionRegistry:
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = SessionRegistry()
        return _registry
