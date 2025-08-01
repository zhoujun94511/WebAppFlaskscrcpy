from flask import Blueprint, jsonify, request
import scrcpy.utils as scrcpy_utils

bp = Blueprint('device_api', __name__)

@bp.route('/devices', methods=['GET'])
def list_devices():
    devices = scrcpy_utils.get_devices()
    return jsonify({'devices': devices})

@bp.route('/start', methods=['POST'])
def start_device():
    device_id = request.json.get('device_id')
    scrcpy_utils.get_client(device_id)
    return jsonify({'status': 'started'})

@bp.route('/stop', methods=['POST'])
def stop_device():
    device_id = request.json.get('device_id')
    scrcpy_utils.stop_client(device_id)
    return jsonify({'status': 'stopped'})