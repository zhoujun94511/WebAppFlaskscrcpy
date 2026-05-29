import socket
import logging

_log = logging.getLogger(__name__)


def get_local_ip():
    """获取本机局域网IP地址，优先用8.8.8.8探测"""
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        local_ip = sock.getsockname()[0]
        _log.info(f"本地IP: {local_ip}")
        return local_ip
    except Exception as e:
        _log.error("获取本地IP失败: %s", e)
        return "127.0.0.1"
    finally:
        if sock:
            sock.close()