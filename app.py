import eventlet
eventlet.monkey_patch()
import os
import cv2
import time
import base64
import logging
import threading
import webbrowser
import scrcpy.utils as scrcpy_utils
from universal_utils import get_local_ip
from flask_socketio import SocketIO, emit
from api.device_api import bp as device_api_bp
from flask import Flask, render_template, send_from_directory

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='eventlet')
app.register_blueprint(device_api_bp, url_prefix='/api')

# 配置全局日志格式和级别
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# 设备id到推流线程的映射
stream_threads = {}
stream_thread_stop_flags = {}

@app.route('/')
def index():
    logging.info("访问首页 /")
    return render_template('index.html')

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static', 'wresource'), 'favicon.ico')

@socketio.on('scrcpy_event')
def handle_scrcpy_event(data):
    device_id = data.get('device_id')
    logging.info(f"收到 scrcpy_event: {data}")
    client = scrcpy_utils.get_client(device_id)
    if not client or not client.alive:
        logging.warning(f"设备 {device_id} 未就绪或未连接")
        return
    tp = data.get('type')
    try:
        if tp == 'key':
            client.control.keycode(data['keycode'], data['action'])
            logging.info(f"发送按键事件到设备 {device_id}: keycode={data['keycode']} action={data['action']}")
        elif tp == 'touch':
            client.control.touch(data['x'], data['y'], data['action'])
            logging.info(f"发送触摸事件到设备 {device_id}: x={data['x']}, y={data['y']}, action={data['action']}")
        elif tp == 'swipe':
            start = data.get('start')
            end = data.get('end')
            duration = data.get('duration', 200)
            if start and end:
                client.control.swipe(start[0], start[1], end[0], end[1], duration_ms=duration)
                logging.info(f"发送滑动事件到设备 {device_id}: {start} -> {end}, duration={duration}ms")
            else:
                logging.warning(f"swipe事件缺少参数: {data}")
        elif tp == 'text':
            client.control.text(data['text'])
            logging.info(f"发送文本到设备 {device_id}: {data['text']}")
        elif tp == 'clipboard_set':
            client.control.set_clipboard(data['text'])
            logging.info(f"设置剪贴板到设备 {device_id}: {data['text']}")
        elif tp == 'clipboard_get':
            clipboard_content = client.control.get_clipboard()
            emit('scrcpy_clipboard', {'text': clipboard_content})
            logging.info(f"获取剪贴板内容: {clipboard_content}")
        elif tp == 'expand_notification':
            client.control.expand_notification_panel()
            logging.info(f"展开通知栏: {device_id}")
        elif tp == 'expand_settings':
            client.control.expand_settings_panel()
            logging.info(f"展开设置面板: {device_id}")
        elif tp == 'collapse_panels':
            client.control.collapse_panels()
            logging.info(f"收起面板: {device_id}")
        elif tp == 'rotate_device':
            client.control.rotate_device()
            logging.info(f"旋转屏幕: {device_id}")
        elif tp == 'set_power_mode':
            client.control.set_screen_power_mode(data.get('mode', 2))
            logging.info(f"设置电源模式: {device_id} mode={data.get('mode', 2)}")
        else:
            logging.warning(f"未知类型事件: {tp}")
    except Exception as e:
        logging.error(f"处理 scrcpy_event {tp} 时发生异常: {e}")

@socketio.on('start_stream')
def start_stream(data):
    device_id = data.get('device_id')
    logging.info(f"收到 start_stream 请求: device_id={device_id}")
    if not device_id:
        logging.warning("start_stream: 未指定 device_id")
        return
    client = scrcpy_utils.get_client(device_id)
    if not client or not client.alive:
        logging.warning(f"start_stream: 设备 {device_id} 未就绪")
        return
    if device_id in stream_threads and stream_threads[device_id].is_alive():
        logging.info(f"start_stream: 设备 {device_id} 已在推流中")
        return  # 已在推流
    stream_thread_stop_flags[device_id] = False
    def stream_loop():
        logging.info(f"推流线程启动: {device_id}")
        try:
            while client.alive and not stream_thread_stop_flags[device_id]:
                frame = client.last_frame
                if frame is not None:
                    _, buf = cv2.imencode('.jpg', frame)
                    b64img = base64.b64encode(buf).decode('utf-8')
                    socketio.emit('scrcpy_frame', {'img': b64img, 'device_id': device_id})
                time.sleep(0.04)  # ~25fps
        except Exception as e:
            logging.error(f"推流线程异常: {e}")
        logging.info(f"推流线程退出: {device_id}")
    t = threading.Thread(target=stream_loop, daemon=True)
    stream_threads[device_id] = t
    t.start()

@socketio.on('stop_stream')
def stop_stream(data):
    device_id = data.get('device_id')
    logging.info(f"收到 stop_stream: {device_id}")
    if device_id in stream_thread_stop_flags:
        stream_thread_stop_flags[device_id] = True

use_local_ip = get_local_ip()

def open_browser():
    """自动打开网页到本地服务地址"""
    time.sleep(1)
    url = f'http://{use_local_ip}:5001'
    logging.info(f"自动打开浏览器: {url}")
    webbrowser.open_new_tab(url)

if __name__ == '__main__':
    # 启动时自动打开浏览器
    if not hasattr(app, 'browser_opened') or not app.browser_opened:
        browser_thread = threading.Thread(target=open_browser)
        browser_thread.start()
        app.browser_opened = True
    local_ip = get_local_ip()
    logging.info(f"服务启动: http://{local_ip}:5001")
    socketio.run(app, host=local_ip, port=5001, debug=True, use_reloader=False)