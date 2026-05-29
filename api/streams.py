"""HTTP routes for stream-level operations (matrix list, snapshot, adaptive toggle).

Thin adapters only — business logic lives in ``services/snapshots``,
``services/adaptive`` and ``scrcpy.scrcpyclients``.
"""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

import scrcpy.scrcpyclients as scrcpy_clients
from services import adaptive, snapshots

bp = Blueprint('streams_api', __name__)


@bp.route('/active-streams', methods=['GET'])
def list_active_streams():
    """List every device with a live scrcpy client (used by the matrix view)."""
    return jsonify({'streams': scrcpy_clients.list_active_clients()})


@bp.route('/snapshot', methods=['GET'])
def device_snapshot():
    """Return a JPEG snapshot of a device's current screen."""
    device_id = request.args.get('device_id', '').strip()
    if not device_id:
        return jsonify({'status': 'failed', 'message': 'missing device_id'}), 400
    quality = int(request.args.get('quality', 60) or 60)

    jpeg = snapshots.capture_jpeg(device_id, quality=quality)
    if jpeg is None:
        return jsonify({'status': 'failed', 'message': 'no frame available'}), 503
    return Response(jpeg, mimetype='image/jpeg', headers={'Cache-Control': 'no-store'})


@bp.route('/adaptive', methods=['POST'])
def toggle_adaptive_bitrate():
    """Enable/disable TWCC-driven adaptive bitrate for the device's PCs."""
    payload = request.json or {}
    device_id = (payload.get('device_id') or '').strip()
    if not device_id:
        return jsonify({'status': 'failed', 'message': 'missing device_id'}), 400

    try:
        _affected, body = adaptive.set_adaptive(device_id, bool(payload.get('enabled', False)))
    except (ImportError, ModuleNotFoundError, RuntimeError) as exc:
        return jsonify({'status': 'failed', 'message': f'webrtc unavailable: {exc}'}), 503
    return jsonify(body)
