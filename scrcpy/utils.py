import adbutils
from .core import Client

_clients = {}  # device_id: Client实例

def get_devices():
    adb = adbutils.AdbClient()
    return [d.serial for d in adb.device_list()]

def get_client(device_id):
    if device_id not in _clients or not _clients[device_id].alive:
        adb = adbutils.AdbClient()
        device = adb.device(device_id)
        _clients[device_id] = Client(device)
        _clients[device_id].start(threaded=True)
    return _clients[device_id]

def stop_client(device_id):
    if device_id in _clients:
        _clients[device_id].stop()
        del _clients[device_id]