import functools
import logging
import struct
import threading
import time

from adbutils import AdbError

from . import scrcpyconst as const

_log = logging.getLogger(__name__)

# Rate-throttled counters for the two control-channel paths most likely
# to "storm" — wheel scroll injection and TYPE_RESET_VIDEO. If either
# fires more than ``_STORM_WARN_PER_SEC`` times in a 1-second window,
# we emit a WARN log so a tight loop becomes visible in the backend log
# without having to capture and grep logcat from the device. The
# trigger threshold is well above any legitimate human interaction
# (rapid two-finger touchpad scroll is at most ~30/sec).
_STORM_WARN_PER_SEC = 30
_storm_lock = threading.Lock()
_storm_state: dict[str, dict] = {}  # key -> {"count", "window_start", "last_warn"}


def _track_dispatch_rate(key: str) -> None:
    now = time.monotonic()
    with _storm_lock:
        st = _storm_state.setdefault(key, {"count": 0, "window_start": now, "last_warn": 0.0})
        st["count"] += 1
        elapsed = now - st["window_start"]
        if elapsed >= 1.0:
            rate = st["count"] / elapsed
            if rate > _STORM_WARN_PER_SEC and now - st["last_warn"] > 1.0:
                _log.warning(
                    "control dispatch storm: %s fired %.1f times/sec (window=%.2fs)",
                    key, rate, elapsed,
                )
                st["last_warn"] = now
            st["count"] = 0
            st["window_start"] = now

def inject(control_type: int):
    def wrapper(f):
        @functools.wraps(f)
        def inner(self, *args, **kwargs):
            package = struct.pack(">B", control_type) + f(self, *args, **kwargs)
            if self.parent.control_socket is not None:
                with self.parent.control_socket_lock:
                    self.parent.control_socket.send(package)
            return package
        return inner
    return wrapper

class ControlSender:
    def __init__(self, parent):
        self.parent = parent

    def _shell(self, *args):
        try:
            self.parent.device.shell(list(args))
        except (AdbError, OSError):
            return False
        return True

    def _shell_text(self, text: str):
        escaped = text.replace(" ", "%s")
        return self._shell("input", "text", escaped)

    @inject(const.TYPE_INJECT_KEYCODE)
    # noinspection SpellCheckingInspection
    def keycode(self, keycode, action=const.ACTION_DOWN, repeat=0, meta_state=0):
        if self.parent.control_socket is None:
            if action == const.ACTION_DOWN:
                self._shell("input", "keyevent", str(keycode))
            return struct.pack(">Biii", action, keycode, repeat, meta_state)
        return struct.pack(">Biii", action, keycode, repeat, meta_state)

    @inject(const.TYPE_INJECT_TEXT)
    def text(self, text):
        if self.parent.control_socket is None:
            self._shell_text(text)
        buf = text.encode("utf-8")
        return struct.pack(">i", len(buf)) + buf

    @inject(const.TYPE_INJECT_TOUCH_EVENT)
    # noinspection SpellCheckingInspection
    def touch(self, x, y, action=const.ACTION_DOWN, touch_id=0x1234567887654321):
        w, h = self.parent.resolution or (1080, 1920)
        if self.parent.control_socket is None:
            if action == const.ACTION_UP:
                self._shell("input", "tap", str(int(x)), str(int(y)))
            return struct.pack(">BqiiHHHii", action, touch_id, int(x), int(y), w, h, 0xFFFF, 1, 1)
        return struct.pack(">BqiiHHHii", action, touch_id, int(x), int(y), w, h, 0xFFFF, 1, 1)

    @inject(const.TYPE_INJECT_SCROLL_EVENT)
    def scroll(self, x, y, h, v):
        _track_dispatch_rate("scroll")
        if self.parent.control_socket is None:
            duration = max(100, min(1000, int((abs(h) + abs(v)) * 8)))
            self._shell(
                "input",
                "swipe",
                str(int(x)),
                str(int(y)),
                str(int(x + h)),
                str(int(y + v)),
                str(duration),
            )
        w, h_ = self.parent.resolution or (1080, 1920)
        # scrcpy server (v2.0+) wire format for TYPE_INJECT_SCROLL_EVENT:
        #
        #   x:i32   y:i32   w:u16   h:u16   hscroll:i16   vscroll:i16   buttons:i32
        #
        # hscroll / vscroll are I16 fixed-point: ±32767 represents ±1.0
        # screens worth of scroll (one full "wheel notch"). The legacy
        # format here packed them as raw i32 deltas; the server then
        # read 2-byte halves of those deltas as fixed-point + reinterpreted
        # our vscroll int as ``buttons`` — a bit-mask of MotionEvent
        # buttons. Garbage button bits (often including BACK/FORWARD plus
        # reserved positions) injected on every wheel tick eventually
        # crashed ``system_server`` on some devices (POCO, Pixel 10), which
        # presented to the user as the phone rebooting.
        #
        # Normalise the raw wheel delta (frontend passes deltaX/Y * 12,
        # i.e. ~1200 for one notch) into [-1.0, 1.0] then to i16 fixed -
        # point. ``buttons`` is always 0 — we don't simulate held mouse
        # buttons during scroll.
        def _to_fixed(raw):
            normalised = max(-1.0, min(1.0, raw / 1200.0))
            return max(-32767, min(32767, int(round(normalised * 32767))))
        return struct.pack(
            ">iiHHhhi",
            int(x),
            int(y),
            w,
            h_,
            _to_fixed(h),
            _to_fixed(v),
            0,
        )

    @inject(const.TYPE_BACK_OR_SCREEN_ON)
    def back_or_turn_screen_on(self, action=const.ACTION_DOWN):
        if self.parent.control_socket is None and action == const.ACTION_DOWN:
            self._shell("input", "keyevent", str(const.KEYCODE_BACK))
        return struct.pack(">B", action)

    @inject(const.TYPE_EXPAND_NOTIFICATION_PANEL)
    def expand_notification_panel(self):
        if self.parent.control_socket is None:
            self._shell("cmd", "statusbar", "expand-notifications")
        return b""

    @inject(const.TYPE_EXPAND_SETTINGS_PANEL)
    def expand_settings_panel(self):
        if self.parent.control_socket is None:
            self._shell("cmd", "statusbar", "expand-settings")
        return b""

    @inject(const.TYPE_COLLAPSE_PANELS)
    def collapse_panels(self):
        if self.parent.control_socket is None:
            self._shell("cmd", "statusbar", "collapse")
        return b""

    def get_clipboard(self):
        s = self.parent.control_socket
        if s is None:
            return ""
        with self.parent.control_socket_lock:
            self.parent.clipboard_event.clear()
            package = struct.pack(">BB", const.TYPE_GET_CLIPBOARD, 0)
            s.send(package)
        if self.parent.clipboard_event.wait(self.parent.connection_timeout / 1000.0):
            return self.parent.clipboard_text or ""
        return ""

    def set_clipboard(self, text, paste=False, sequence=const.SEQUENCE_INVALID):
        if self.parent.control_socket is None:
            return False
        buf = text.encode("utf-8")
        payload = struct.pack(">BQ?i", const.TYPE_SET_CLIPBOARD, sequence, paste, len(buf)) + buf
        if sequence != const.SEQUENCE_INVALID:
            self.parent.clipboard_ack_event.clear()
            self.parent.clipboard_ack_sequence = const.SEQUENCE_INVALID
        with self.parent.control_socket_lock:
            self.parent.control_socket.send(payload)
        if sequence != const.SEQUENCE_INVALID:
            if not self.parent.clipboard_ack_event.wait(self.parent.connection_timeout / 1000.0):
                return False
            return self.parent.clipboard_ack_sequence == sequence
        return True

    @inject(const.TYPE_SET_SCREEN_POWER_MODE)
    def set_screen_power_mode(self, mode=const.POWER_MODE_NORMAL):
        if self.parent.control_socket is None:
            self._shell("input", "keyevent", str(const.KEYCODE_POWER))
        return struct.pack(">b", mode)

    @inject(const.TYPE_ROTATE_DEVICE)
    def rotate_device(self):
        if self.parent.control_socket is None:
            return b""
        return b""

    @inject(const.TYPE_RESET_VIDEO)
    def reset_video(self):
        """Force scrcpy server to emit a fresh SPS/PPS/IDR.

        Non-destructive force-keyframe path: the wire stays open, the
        scrcpy process stays up, only the MediaCodec encoder gets a
        ``CaptureControl.reset()`` poke. Used by the adaptive quality
        controller (services/quality_controller.py) as L1 — replaces
        the old "restart scrcpy at the same bitrate" hack which had a
        nasty feedback loop because reconfigure tore down the PC.

        No payload — the opcode byte itself is the whole message.
        """
        _track_dispatch_rate("reset_video")
        return b""

    def swipe(self, start_x, start_y, end_x, end_y, duration_ms=200):
        """
        优化后的滑动手势，duration_ms为手势总时长(ms)
        """
        import time
        dx = end_x - start_x
        dy = end_y - start_y
        dist = (dx ** 2 + dy ** 2) ** 0.5
        steps = max(8, min(int(dist // 25), 25))  # 最少8步，最多25步
        if steps == 0:
            steps = 1
        move_steps_delay = duration_ms / steps / 1000.0

        self.touch(start_x, start_y, const.ACTION_DOWN)
        for i in range(1, steps + 1):
            x = int(start_x + dx * i / steps)
            y = int(start_y + dy * i / steps)
            self.touch(x, y, const.ACTION_MOVE)
            time.sleep(move_steps_delay)
        self.touch(end_x, end_y, const.ACTION_UP)
