import struct
import functools
from . import const

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

    @inject(const.TYPE_INJECT_KEYCODE)
    def keycode(self, keycode, action=const.ACTION_DOWN, repeat=0):
        return struct.pack(">Biii", action, keycode, repeat, 0)

    @inject(const.TYPE_INJECT_TEXT)
    def text(self, text):
        buf = text.encode("utf-8")
        return struct.pack(">i", len(buf)) + buf

    @inject(const.TYPE_INJECT_TOUCH_EVENT)
    def touch(self, x, y, action=const.ACTION_DOWN, touch_id=0x1234567887654321):
        w, h = self.parent.resolution or (1080, 1920)
        return struct.pack(">BqiiHHHii", action, touch_id, int(x), int(y), w, h, 0xFFFF, 1, 1)

    @inject(const.TYPE_INJECT_SCROLL_EVENT)
    def scroll(self, x, y, h, v):
        w, h_ = self.parent.resolution or (1080, 1920)
        return struct.pack(">iiHHii", int(x), int(y), w, h_, int(h), int(v))

    @inject(const.TYPE_BACK_OR_SCREEN_ON)
    def back_or_turn_screen_on(self, action=const.ACTION_DOWN):
        return struct.pack(">B", action)

    @inject(const.TYPE_EXPAND_NOTIFICATION_PANEL)
    def expand_notification_panel(self):
        return b""

    @inject(const.TYPE_EXPAND_SETTINGS_PANEL)
    def expand_settings_panel(self):
        return b""

    @inject(const.TYPE_COLLAPSE_PANELS)
    def collapse_panels(self):
        return b""

    def get_clipboard(self):
        s = self.parent.control_socket
        with self.parent.control_socket_lock:
            s.setblocking(False)
            while True:
                try:
                    s.recv(1024)
                except BlockingIOError:
                    break
            s.setblocking(True)
            package = struct.pack(">B", const.TYPE_GET_CLIPBOARD)
            s.send(package)
            (code,) = struct.unpack(">B", s.recv(1))
            assert code == 0
            (length,) = struct.unpack(">i", s.recv(4))
            return s.recv(length).decode("utf-8")

    @inject(const.TYPE_SET_CLIPBOARD)
    def set_clipboard(self, text, paste=False):
        buf = text.encode("utf-8")
        return struct.pack(">?i", paste, len(buf)) + buf

    @inject(const.TYPE_SET_SCREEN_POWER_MODE)
    def set_screen_power_mode(self, mode=const.POWER_MODE_NORMAL):
        return struct.pack(">b", mode)

    @inject(const.TYPE_ROTATE_DEVICE)
    def rotate_device(self):
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