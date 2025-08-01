import os
import cv2
import time
import socket
import struct
import logging
import threading
import numpy as np
from time import sleep
from av.codec import CodecContext
from av.error import InvalidDataError
from typing import Any, Callable, Optional, Tuple, Union
from adbutils import AdbConnection, AdbDevice, AdbError, Network, adb

from .const import (
    EVENT_DISCONNECT,
    EVENT_FRAME,
    EVENT_INIT,
    LOCK_SCREEN_ORIENTATION_UNLOCKED,
)
from .control import ControlSender

class Client:
    def __init__(
        self,
        device: Optional[Union[AdbDevice, str, any]] = None,
        max_width: int = 0,
        bitrate: int = 8000000,
        max_fps: int = 0,
        flip: bool = False,
        block_frame: bool = False,
        stay_awake: bool = False,
        lock_screen_orientation: int = LOCK_SCREEN_ORIENTATION_UNLOCKED,
        connection_timeout: int = 3000,
        encoder_name: Optional[str] = None,
    ):
        assert max_width >= 0, "max_width must be greater than or equal to 0"
        assert bitrate >= 0, "bitrate must be greater than or equal to 0"
        assert max_fps >= 0, "max_fps must be greater than or equal to 0"
        assert (
            -1 <= lock_screen_orientation <= 3
        ), "lock_screen_orientation must be LOCK_SCREEN_ORIENTATION_*"
        assert (
            connection_timeout >= 0
        ), "connection_timeout must be greater than or equal to 0"
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
        self.block_frame = block_frame
        self.stay_awake = stay_awake
        self.lock_screen_orientation = lock_screen_orientation
        self.connection_timeout = connection_timeout
        self.encoder_name = encoder_name

        # Connect to device
        if device is None:
            device = adb.device_list()[0]
        elif isinstance(device, str):
            device = adb.device(serial=device)

        self.device = device
        self.listeners = dict(frame=[], init=[], disconnect=[])

        # User accessible
        self.last_frame: Optional[np.ndarray] = None
        self.resolution: Optional[Tuple[int, int]] = None
        self.device_name: Optional[str] = None
        self.control = ControlSender(self)

        # Need to destroy
        self.alive = False
        self.__server_stream: Optional[AdbConnection] = None
        self.__video_socket: Optional[socket.socket] = None
        self.control_socket: Optional[socket.socket] = None
        self.control_socket_lock = threading.Lock()

        # Available if start with threaded or daemon_threaded
        self.stream_loop_thread = None

    def __init_server_connection(self) -> None:
        connected = False
        for _ in range(self.connection_timeout // 100):
            try:
                self.__video_socket = self.device.create_connection(
                    Network.LOCAL_ABSTRACT, "scrcpy"
                )
                connected = True
                break
            except AdbError:
                sleep(0.1)
        if not connected:
            logging.error("Failed to connect scrcpy-server after %d ms", self.connection_timeout)
            return

        dummy_byte = self.__video_socket.recv(1)
        if not len(dummy_byte) or dummy_byte != b"\x00":
            logging.error("Did not receive Dummy Byte!")
            return

        try:
            self.control_socket = self.device.create_connection(
                Network.LOCAL_ABSTRACT, "scrcpy"
            )
        except Exception as e:
            logging.error(f"Failed to create control socket: {e}")
            return
        self.device_name = self.__video_socket.recv(64).decode("utf-8").rstrip("\x00")
        if not len(self.device_name):
            logging.error("Did not receive Device Name!")
            return

        res = self.__video_socket.recv(4)
        self.resolution = struct.unpack(">HH", res)
        self.__video_socket.setblocking(False)

    def __deploy_server(self) -> None:
        jar_name = "scrcpy-server.jar"
        server_file_path = os.path.join(
            os.path.abspath(os.path.dirname(__file__)), jar_name
        )
        logging.info("==== __deploy_server 正在被调用 ====")
        logging.info(f"准备从路径: {server_file_path}推送scrcpy-server.jar文件到到手机/data/local/tmp/路径下")
        if not os.path.exists(server_file_path):
            logging.error(f"scrcpy-server.jar 文件不存在: {server_file_path}")
            return
        try:
            self.device.sync.push(server_file_path, f"/data/local/tmp/{jar_name}")
            logging.info("已将scrcpy-server.jar文件push 到手机 /data/local/tmp/路径下")
        except Exception as e:
            logging.error(f"push scrcpy-server.jar 到手机失败: {e}")
            return
        commands = [
            f"CLASSPATH=/data/local/tmp/{jar_name}",
            "app_process",
            "/",
            "com.genymobile.scrcpy.Server",
            "2.4",  # Scrcpy server version
            "log_level=info",
            f"max_size={self.max_width}",
            f"max_fps={self.max_fps}",
            f"video_bit_rate={self.bitrate}",
            "tunnel_forward=true",
            "send_frame_meta=false",
            "control=true",
            "audio=false",
            "show_touches=false",
            "stay_awake=false",
            "power_off_on_close=false",
            "clipboard_autosync=false"
        ]

        try:
            self.__server_stream: AdbConnection = self.device.shell(
                commands,
                stream=True,
            )
            # Wait for server to start
            self.__server_stream.read(10)
            logging.info(f"scrcpy server 启动命令: {commands}")
        except Exception as e:
            logging.error(f"启动scrcpy server失败: {e}")
            return

    def start(self, threaded: bool = False, daemon_threaded: bool = False) -> None:
        if self.alive:
            logging.warning("Client already started")
            return

        self.__deploy_server()
        self.__init_server_connection()
        self.alive = True
        self.__send_to_listeners(EVENT_INIT)

        if threaded or daemon_threaded:
            self.stream_loop_thread = threading.Thread(
                target=self.__stream_loop, daemon=daemon_threaded
            )
            self.stream_loop_thread.start()
        else:
            self.__stream_loop()

    def stop(self) -> None:
        self.alive = False

        if self.__server_stream is not None:
            try:
                self.__server_stream.close()
                logging.info("已关闭 __server_stream")
            except (OSError, IOError) as e:
                logging.warning(f"关闭 __server_stream 时发生异常: {e}")
            finally:
                self.__server_stream = None

        if self.control_socket is not None:
            try:
                self.control_socket.close()
                logging.info("已关闭 control_socket")
            except (OSError, socket.error) as e:
                logging.warning(f"关闭 control_socket 时发生异常: {e}")
            finally:
                self.control_socket = None

        if self.__video_socket is not None:
            try:
                self.__video_socket.close()
                logging.info("已关闭 __video_socket")
            except (OSError, socket.error) as e:
                logging.warning(f"关闭 __video_socket 时发生异常: {e}")
            finally:
                self.__video_socket = None

    def __stream_loop(self) -> None:
        codec = CodecContext.create("h264", "r")
        while self.alive:
            try:
                raw_h264 = self.__video_socket.recv(0x10000)
                if raw_h264 == b"":
                    logging.error("Video stream is disconnected")
                    break
                packets = codec.parse(raw_h264)
                for packet in packets:
                    frames = codec.decode(packet)
                    for frame in frames:
                        frame = frame.to_ndarray(format="bgr24")
                        if self.flip:
                            frame = cv2.flip(frame, 1)
                        self.last_frame = frame
                        self.resolution = (frame.shape[1], frame.shape[0])
                        self.__send_to_listeners(EVENT_FRAME, frame)
            except (BlockingIOError, InvalidDataError):
                time.sleep(0.01)
                if not self.block_frame:
                    self.__send_to_listeners(EVENT_FRAME, None)
            except (ConnectionError, OSError) as e:
                if self.alive:
                    self.__send_to_listeners(EVENT_DISCONNECT)
                    self.stop()
                logging.error(f"推流异常: {e}")
                break

    def add_listener(self, cls: str, listener: Callable[..., Any]) -> None:
        self.listeners[cls].append(listener)

    def remove_listener(self, cls: str, listener: Callable[..., Any]) -> None:
        self.listeners[cls].remove(listener)

    def __send_to_listeners(self, cls: str, *args, **kwargs) -> None:
        for fun in self.listeners[cls]:
            fun(*args, **kwargs)