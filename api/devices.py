"""HTTP routes for device-level operations (list / start / stop / configure / upload).

Thin adapters only — every non-trivial step lives in ``services/`` or in
``scrcpy.scrcpyclients``. Responsibilities here are:

1. parse the request,
2. delegate to a service,
3. shape the JSON response.
"""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request, stream_with_context

# cspell:ignore scrcpyclients
import scrcpy.scrcpyclients as scrcpy_clients
from services import (
    app_manager,
    authentication,
    device_info,
    device_rotation,
    device_watch,
    file_browser,
    input_shell,
    logcat,
    reservations,
    uploads,
)

bp = Blueprint('device_api', __name__)


def _sync_fanout_keyevent(device_id: str, code: int) -> None:
    """Mirror a quick keyevent to sync-group slaves (experimental, guarded).

    Fully isolated: any failure here must never affect the master's own
    keyevent response above.
    """
    try:
        from services import sync as _sync
        if _sync.is_enabled():
            from services.sync import sync_dispatcher
            sync_dispatcher.fanout_keyevent(device_id, code)
    except (ImportError, AttributeError, RuntimeError, OSError, ValueError):
        pass


def _sync_fanout_swipe(device_id: str, direction, duration) -> None:
    """Mirror a directional swipe to sync-group slaves (experimental, guarded)."""
    try:
        from services import sync as _sync
        if _sync.is_enabled():
            from services.sync import sync_dispatcher
            sync_dispatcher.fanout_swipe_direction(device_id, direction, duration)
    except (ImportError, AttributeError, RuntimeError, OSError, ValueError):
        pass


def _require_owner(device_id: str):
    """Reservation gate for control actions.

    Returns an error (response, status) tuple when the caller may not
    control ``device_id`` (not reserved, or held by someone else and the
    caller is not an admin), else ``None``. Authentication itself is
    already guaranteed by the app-level before_request hook.
    """
    try:
        reservations.assert_owner(device_id, authentication.current_user())
    except reservations.ReservationError as exc:
        return jsonify({'status': 'failed', 'message': str(exc)}), 403
    return None


@bp.route('/devices', methods=['GET'])
def list_devices():
    serials = scrcpy_clients.get_devices()
    # Per-device screen geometry. Lets the UI render correctly-shaped
    # card placeholders before any scrcpy stream exists — previously
    # the card chrome was hardcoded 9:16 (then a localStorage hack)
    # which visibly skewed on modern phones (Pixel 10 = 9:20.2). The
    # underlying ``wm size`` lookup is cached per-device in
    # input_shell, so this is ~free after the first call.
    geometry: dict[str, dict[str, int]] = {}
    for serial in serials:
        dims = input_shell.get_screen_size(serial)
        if dims is not None:
            geometry[serial] = {'width': dims[0], 'height': dims[1]}
    return jsonify({
        'devices': serials,
        # Friendly "Brand Model" name keyed by serial — backward-compatible:
        # callers that ignore this still see the same ``devices`` array.
        'names': device_watch.names_for(serials),
        # Map ``serial -> {width, height}``. Entry missing iff ``wm size``
        # failed (device disappearing / permission weirdness); the UI
        # falls back gracefully to its default aspect.
        'geometry': geometry,
    })


@bp.route('/device/<device_id>/keyevent', methods=['POST'])
def device_keyevent_route(device_id: str):
    """Send an Android keycode via raw ``adb she'll input keyevent``.

    This path is used by the always-visible quick-action buttons in the
    main view — it deliberately does NOT require an active scrcpy stream
    so users can press Wake / Home before they hit Play. For high-frequency
    typing (keypad panel) use the input DataChannel instead.
    """
    err = _require_device(device_id)
    if err is not None:
        return err
    body = request.json or {}
    try:
        code = int(body.get('code'))
    except (TypeError, ValueError):
        return jsonify({'status': 'failed', 'message': 'missing or invalid code'}), 400
    if code < 0 or code > 0x7FFFFFFF:
        return jsonify({'status': 'failed', 'message': 'code out of range'}), 400
    try:
        from adbutils import adb
        adb.device(device_id).shell(f'input keyevent {code}', timeout=4)
        _sync_fanout_keyevent(device_id, code)
        return jsonify({'status': 'ok'})
    except (RuntimeError, OSError, TimeoutError) as exc:
        return jsonify({'status': 'failed', 'message': str(exc)}), 500


@bp.route('/device/<device_id>/swipe', methods=['POST'])
def device_swipe_route(device_id: str):
    """Issue a directional swipe — thin adapter over :mod:`services.input_shell`.

    Body: ``{"direction": "up"|"down"|"left"|"right", "duration"?: ms}``.
    Like ``/keyevent`` this works without a live scrcpy stream because the
    underlying service uses plain ``adb shell input swipe``.
    """
    err = _require_device(device_id)
    if err is not None:
        return err
    body = request.json or {}
    direction = body.get('direction')
    duration = body.get('duration', input_shell.DEFAULT_DURATION_MS)
    try:
        duration = int(duration)
    except (TypeError, ValueError):
        duration = input_shell.DEFAULT_DURATION_MS
    try:
        input_shell.swipe_direction(device_id, direction, duration)
        _sync_fanout_swipe(device_id, direction, duration)
        return jsonify({'status': 'ok'})
    except input_shell.InputShellError as exc:
        return jsonify({'status': 'failed', 'message': str(exc)}), 400
    except (RuntimeError, OSError, TimeoutError) as exc:
        return jsonify({'status': 'failed', 'message': str(exc)}), 500


@bp.route('/device/<device_id>/info', methods=['GET'])
def device_info_route(device_id: str):
    """Snapshot of static + dynamic device info for the Drawer's Info panel."""
    device_id = (device_id or '').strip()
    if not device_id:
        return jsonify({'status': 'failed', 'message': 'missing device_id'}), 400
    if device_id not in scrcpy_clients.get_devices():
        return jsonify({'status': 'failed', 'message': 'device not connected'}), 404
    return jsonify({'status': 'ok', 'info': device_info.collect(device_id)})


@bp.route('/device/<device_id>/logcat', methods=['GET'])
def device_logcat_route(device_id: str):
    """SSE stream of ``adb logcat`` output for the device's Logcat panel."""
    device_id = (device_id or '').strip()
    if not device_id:
        return jsonify({'status': 'failed', 'message': 'missing device_id'}), 400
    if device_id not in scrcpy_clients.get_devices():
        return jsonify({'status': 'failed', 'message': 'device not connected'}), 404
    level = request.args.get('level', 'I')
    tag = request.args.get('tag') or None
    pid_arg = request.args.get('pid')
    pid = int(pid_arg) if pid_arg and pid_arg.isdigit() else None
    resp = Response(
        stream_with_context(logcat.stream(device_id, level=level, tag=tag, pid=pid)),
        mimetype='text/event-stream',
    )
    # Disable buffering by proxies + tell the browser this is a stream.
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp


@bp.route('/device/<device_id>/logcat/clear', methods=['POST'])
def device_logcat_clear_route(device_id: str):
    device_id = (device_id or '').strip()
    if not device_id:
        return jsonify({'status': 'failed', 'message': 'missing device_id'}), 400
    ok = logcat.clear(device_id)
    return jsonify({'status': 'ok' if ok else 'failed'})


# ──────────────────────────────────────────────────────────────────────
# File browser
# ──────────────────────────────────────────────────────────────────────

def _require_device(device_id: str):
    """Reject calls with a missing / unknown device. Returns either a
    (response, status) error tuple or ``None`` when the device is good."""
    device_id = (device_id or '').strip()
    if not device_id:
        return jsonify({'status': 'failed', 'message': 'missing device_id'}), 400
    if device_id not in scrcpy_clients.get_devices():
        return jsonify({'status': 'failed', 'message': 'device not connected'}), 404
    return _require_owner(device_id)


@bp.route('/device/<device_id>/files', methods=['GET'])
def device_files_list(device_id: str):
    err = _require_device(device_id)
    if err is not None:
        return err
    path = request.args.get('path', '/sdcard')
    return jsonify({'status': 'ok', **file_browser.list_directory(device_id, path)})


@bp.route('/device/<device_id>/rotate', methods=['POST'])
def device_rotate(device_id: str):
    """Fallback rotation — thin adapter over :mod:`services.device_rotation`.

    Body: ``{"target": 0|1|2|3}`` — absolute Surface.ROTATION_* enum. The
    front-end tracks the desired rotation locally and sends the absolute
    value so we don't have to read the (sometimes stale) device state.

    Returns ``{"status": "ok", "rotation": N}`` so the front-end can
    confirm what was applied and resync its local counter.
    """
    err = _require_device(device_id)
    if err is not None:
        return err
    body = request.json or {}
    try:
        target = int(body.get('target'))
    except (TypeError, ValueError):
        return jsonify({'status': 'failed', 'message': 'missing or invalid target'}), 400
    try:
        applied = device_rotation.apply(device_id, target)
        return jsonify({'status': 'ok', 'rotation': applied})
    except device_rotation.RotationError as exc:
        return jsonify({'status': 'failed', 'message': str(exc)}), 400
    except (RuntimeError, OSError, TimeoutError) as exc:
        return jsonify({'status': 'failed', 'message': str(exc)}), 500


@bp.route('/device/<device_id>/files/stat', methods=['GET'])
def device_files_stat(device_id: str):
    err = _require_device(device_id)
    if err is not None:
        return err
    path = request.args.get('path', '')
    if not path:
        return jsonify({'status': 'failed', 'message': 'missing path'}), 400
    info = file_browser.stat_path(device_id, path)
    if info is None:
        return jsonify({'status': 'failed', 'message': 'not found'}), 404
    return jsonify({'status': 'ok', 'info': info})


@bp.route('/device/<device_id>/files/pull', methods=['GET'])
def device_files_pull(device_id: str):
    """Pull a single file off the device.

    ``?inline=1`` switches the response from a forced download
    (``Content-Disposition: attachment``) to inline display with a
    best-effort Content-Type guessed from the file extension. The
    in-browser preview modal uses this for text/image/PDF/audio
    rendering. Default (no query param) keeps the original download
    behavior so existing callers don't change.
    """
    err = _require_device(device_id)
    if err is not None:
        return err
    path = request.args.get('path', '')
    if not path:
        return jsonify({'status': 'failed', 'message': 'missing path'}), 400
    inline = (request.args.get('inline') or '').lower() in ('1', 'true', 'yes')

    info = file_browser.stat_path(device_id, path)
    if info is None or info['is_dir']:
        return jsonify({'status': 'failed', 'message': 'not a file'}), 404

    filename = info['name']
    data = file_browser.pull_to_buffer(device_id, path)
    if data is None:
        return jsonify({'status': 'failed', 'message': 'pull failed or too large'}), 500

    if inline:
        import mimetypes
        mime = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        resp = Response(data, mimetype=mime)
        # No attachment header — browser decides how to render based on the
        # MIME type (text/* → text view, image/* → image, application/pdf →
        # built-in PDF viewer, audio/* → media element).
        resp.headers['Content-Disposition'] = f'inline; filename="{filename}"'
    else:
        resp = Response(data, mimetype='application/octet-stream')
        resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    resp.headers['Content-Length'] = str(len(data))
    # Allow the inline iframe / img / audio elements to actually fetch the
    # bytes when the dev server is on a different origin than the SPA.
    resp.headers['Access-Control-Allow-Origin'] = '*'
    return resp


@bp.route('/device/<device_id>/files/push', methods=['POST'])
def device_files_push(device_id: str):
    """Upload one or more files into a directory on the device."""
    err = _require_device(device_id)
    if err is not None:
        return err
    remote_dir = (request.form.get('dir') or '/sdcard/Download').rstrip('/')
    uploaded = request.files.getlist('file')
    if not uploaded:
        return jsonify({'status': 'failed', 'message': 'no files'}), 400
    results = []
    for f in uploaded:
        name = (f.filename or '').strip().replace('/', '_').replace('\\', '_')
        if not name:
            continue
        remote_path = f'{remote_dir}/{name}'
        ok, msg = file_browser.push_bytes(device_id, remote_path, f.read())
        results.append({'name': name, 'path': msg if ok else remote_path, 'ok': ok, 'error': None if ok else msg})
    overall = all(r['ok'] for r in results)
    return jsonify({'status': 'ok' if overall else 'partial', 'results': results})


@bp.route('/device/<device_id>/files/mkdir', methods=['POST'])
def device_files_mkdir(device_id: str):
    err = _require_device(device_id)
    if err is not None:
        return err
    path = ((request.json or {}).get('path') or '').strip()
    if not path:
        return jsonify({'status': 'failed', 'message': 'missing path'}), 400
    ok, msg = file_browser.mkdir(device_id, path)
    return jsonify({'status': 'ok' if ok else 'failed', 'message': msg, 'path': msg if ok else None})


@bp.route('/device/<device_id>/files/rename', methods=['POST'])
def device_files_rename(device_id: str):
    err = _require_device(device_id)
    if err is not None:
        return err
    body = request.json or {}
    src = (body.get('src') or '').strip()
    dst = (body.get('dst') or '').strip()
    if not src or not dst:
        return jsonify({'status': 'failed', 'message': 'missing src or dst'}), 400
    ok, msg = file_browser.rename(device_id, src, dst)
    return jsonify({'status': 'ok' if ok else 'failed', 'message': msg, 'path': msg if ok else None})


@bp.route('/device/<device_id>/files', methods=['DELETE'])
def device_files_delete(device_id: str):
    err = _require_device(device_id)
    if err is not None:
        return err
    body = request.json or {}
    path = (body.get('path') or '').strip()
    recursive = bool(body.get('recursive', False))
    if not path:
        return jsonify({'status': 'failed', 'message': 'missing path'}), 400
    ok, msg = file_browser.delete(device_id, path, recursive=recursive)
    return jsonify({'status': 'ok' if ok else 'failed', 'message': msg})


# ──────────────────────────────────────────────────────────────────────
# App / package manager
# ──────────────────────────────────────────────────────────────────────

@bp.route('/device/<device_id>/apps', methods=['GET'])
def device_apps_list(device_id: str):
    err = _require_device(device_id)
    if err is not None:
        return err
    scope = (request.args.get('scope') or 'third_party').strip()
    if scope not in ('all', 'third_party', 'system'):
        scope = 'third_party'
    return jsonify({'status': 'ok', **app_manager.list_packages(device_id, scope=scope)})


@bp.route('/device/<device_id>/apps/<package>', methods=['GET'])
def device_app_detail(device_id: str, package: str):
    err = _require_device(device_id)
    if err is not None:
        return err
    info = app_manager.package_info(device_id, package)
    if info is None:
        return jsonify({'status': 'failed', 'message': 'package not found'}), 404
    return jsonify({'status': 'ok', 'info': info})


@bp.route('/device/<device_id>/apps/<package>', methods=['DELETE'])
def device_app_uninstall(device_id: str, package: str):
    err = _require_device(device_id)
    if err is not None:
        return err
    keep = (request.args.get('keep_data') or '0') in ('1', 'true', 'yes')
    ok, msg = app_manager.uninstall(device_id, package, keep_data=keep)
    return jsonify({'status': 'ok' if ok else 'failed', 'message': msg})


@bp.route('/device/<device_id>/apps/<package>/apk', methods=['GET'])
def device_app_export(device_id: str, package: str):
    """Download an installed package's base APK."""
    err = _require_device(device_id)
    if err is not None:
        return err
    result = app_manager.pull_apk(device_id, package)
    if result is None:
        return jsonify({'status': 'failed', 'message': 'pull failed'}), 500
    data, filename = result
    resp = Response(data, mimetype='application/vnd.android.package-archive')
    resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    resp.headers['Content-Length'] = str(len(data))
    return resp


@bp.route('/device/<device_id>/apps/install', methods=['POST'])
def device_app_install(device_id: str):
    """Install an APK uploaded as multipart form-data.

    Stages the APK on the dev server's tempdir before running ``adb install``
    so we can both verify the upload and let the adb client stream it
    efficiently. Server-side cap of 500 MiB matches typical APK budgets.

    Form fields:
      file              — multipart file part, required
      grant_permissions — '1' to pass ``-g`` to ``pm install``
      force             — '1' to allow destructive recovery on
                          INSTALL_FAILED_UPDATE_INCOMPATIBLE (signature
                          mismatch). The frontend ONLY sets this after
                          the user explicitly confirms the existing app
                          will be uninstalled and its data lost.

    Response shape:
      success:
        { status: 'ok', message: 'installed', filename }
      generic failure:
        { status: 'failed', message: '<pm output>', filename }
      signature mismatch (special — frontend prompts for confirmation):
        { status: 'failed', message, filename,
          error_code: 'signature_mismatch',
          existing_package: 'com.foo.bar' }
    """
    err = _require_device(device_id)
    if err is not None:
        return err
    file_obj = request.files.get('file')
    if file_obj is None or not file_obj.filename:
        return jsonify({'status': 'failed', 'message': 'no file'}), 400
    grant = (request.form.get('grant_permissions') or '0') in ('1', 'true', 'yes')
    force = (request.form.get('force') or '0') in ('1', 'true', 'yes')

    import tempfile, os
    ok = False
    msg = 'upload failed'
    fd, path = tempfile.mkstemp(suffix='.apk')
    try:
        with os.fdopen(fd, 'wb') as f:
            file_obj.save(f)
        ok, msg = app_manager.install_from_local(
            device_id, path, grant_permissions=grant, force_replace=force,
        )
    finally:
        try: os.unlink(path)
        except OSError: pass

    body: dict = {
        'status': 'ok' if ok else 'failed',
        'message': msg,
        'filename': file_obj.filename,
    }
    if not ok:
        # If the failure is the signature-mismatch case, attach a
        # machine-readable hint so the frontend can offer the destructive
        # recovery path (uninstall + retry) instead of just showing an
        # opaque error string.
        offending_pkg = app_manager.detect_signature_mismatch(msg)
        if offending_pkg:
            body['error_code'] = 'signature_mismatch'
            body['existing_package'] = offending_pkg
    return jsonify(body)


@bp.route('/start', methods=['POST'])
def start_device():
    import logging
    _log = logging.getLogger('api.devices')
    body = request.json or {}
    device_id = body.get('device_id')
    boot_id = str(body.get('boot_id') or '-')
    _log.info('[boot=%s dev=%s] /api/start received', boot_id, device_id)
    err = _require_owner(device_id)
    if err is not None:
        return err
    client = scrcpy_clients.get_client(device_id)
    if not client or not client.alive:
        return jsonify({'status': 'failed', 'message': 'scrcpy client failed to start'}), 500
    return jsonify({'status': 'started'})


@bp.route('/stop', methods=['POST'])
def stop_device():
    device_id = (request.json or {}).get('device_id')
    err = _require_owner(device_id)
    if err is not None:
        return err
    scrcpy_clients.stop_client(device_id)
    return jsonify({'status': 'stopped'})


@bp.route('/stream-config', methods=['GET'])
def get_stream_config():
    device_id = request.args.get('device_id', '').strip()
    if not device_id:
        return jsonify({'status': 'failed', 'message': 'missing device_id'}), 400
    return jsonify({'status': 'ok', 'config': scrcpy_clients.get_device_config(device_id)})


@bp.route('/reconfigure', methods=['POST'])
def reconfigure_stream():
    """Restart scrcpy with new params, re-attaching any live WebRTC peers."""
    payload = request.json or {}
    device_id = (payload.get('device_id') or '').strip()
    if not device_id:
        return jsonify({'status': 'failed', 'message': 'missing device_id'}), 400
    err = _require_owner(device_id)
    if err is not None:
        return err

    kwargs = {k: payload[k] for k in ('bitrate', 'max_width', 'max_fps') if k in payload}
    new_client, _old = scrcpy_clients.reconfigure(device_id, **kwargs)
    if not new_client or not new_client.alive:
        return jsonify({'status': 'failed', 'message': 'scrcpy client failed to restart'}), 500

    reattached = _reattach_peers(device_id, new_client)
    return jsonify({
        'status': 'ok',
        'config': scrcpy_clients.get_device_config(device_id),
        'reattached_peers': reattached,
    })


@bp.route('/scrcpy-server', methods=['GET'])
def scrcpy_server_health():
    device_id = request.args.get('device_id', '').strip()
    if not device_id:
        return jsonify({'status': 'failed', 'message': 'missing device_id'}), 400

    local_info = scrcpy_clients.get_local_server_info()
    remote_info = scrcpy_clients.get_remote_server_info(device_id)
    matches = (
        local_info.get('exists')
        and remote_info.get('exists')
        and local_info.get('sha256')
        and local_info.get('sha256') == remote_info.get('sha256')
    )
    return jsonify({
        'status': 'ok',
        'device_id': device_id,
        'local': local_info,
        'remote': remote_info,
        'matches': bool(matches),
    })


@bp.route('/scrcpy-server/push', methods=['POST'])
def push_scrcpy_server():
    device_id = (request.json or {}).get('device_id')
    if not device_id:
        return jsonify({'status': 'failed', 'message': 'missing device_id'}), 400
    err = _require_owner(device_id)
    if err is not None:
        return err

    ok, message = scrcpy_clients.push_server_to_device(device_id)
    if not ok:
        return jsonify({'status': 'failed', 'message': message}), 500

    return jsonify({
        'status': 'ok',
        'message': message,
        'device_id': device_id,
        'remote': scrcpy_clients.get_remote_server_info(device_id),
    })


@bp.route('/upload', methods=['POST'])
def upload_file():
    """Stage an uploaded file then install (APK) or push to /sdcard/Download/.

    Form fields:
      device_id — required
      file      — multipart file part, required
      force     — '1' to allow destructive APK install recovery on
                  signature mismatch (uninstall existing, then reinstall)

    Signature-mismatch failure shape (HTTP 409, lets frontend prompt for
    user confirmation before retrying with force=1):
      { status: 'failed', message,
        error_code: 'signature_mismatch',
        existing_package: 'com.foo.bar' }
    """
    device_id = request.form.get('device_id', '').strip()
    file_obj = request.files.get('file')
    force = (request.form.get('force') or '0') in ('1', 'true', 'yes')

    err = _require_owner(device_id)
    if err is not None:
        return err

    try:
        result = uploads.stage_and_dispatch(device_id, file_obj, force_replace=force)
    except ValueError as exc:
        return jsonify({'status': 'failed', 'message': str(exc)}), 400
    except uploads.InstallSignatureMismatch as exc:
        # 409 Conflict: the installation was structurally fine but the device
        # state (existing app with mismatched signature) blocks it. The
        # frontend should ask the user whether to uninstall and retry.
        return jsonify({
            'status': 'failed',
            'message': exc.pm_message,
            'error_code': 'signature_mismatch',
            'existing_package': exc.existing_package,
            'filename': getattr(file_obj, 'filename', '') or '',
        }), 409
    except RuntimeError as exc:
        return jsonify({'status': 'failed', 'message': str(exc)}), 503
    except OSError as exc:  # adb push/install failure
        return jsonify({'status': 'failed', 'message': str(exc)}), 500

    body = {'status': 'ok', 'action': result.action, 'filename': result.filename}
    if result.remote_path is not None:
        body['remote_path'] = result.remote_path
    return jsonify(body)


def _reattach_peers(device_id: str, new_client) -> int:
    """Best-effort WebRTC peer re-attach. Falls back silently when WebRTC is off."""
    try:
        from scrcpy.webrtc.peer_manager import get_peer_manager

        return get_peer_manager().reattach_for_device(device_id, new_client)
    except (ImportError, ModuleNotFoundError, RuntimeError):
        return 0
