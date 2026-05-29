"""Flask + Socket.IO entry point.

This is the development server. All non-trivial logic lives in service
modules; this file just wires up Flask blueprints, Socket.IO signaling
namespaces and serves the prebuilt Vue frontend.

Video / input / file-transfer all run over **WebRTC** (RTP + DataChannel)
once the per-client PeerConnection negotiates — Socket.IO is reserved for
SDP signaling, the terminal session and the REST-style HTTP routes.

For production deployment see ``docs/WEBRTC_MIGRATION_PLAN.md`` — a real
WSGI server + aiortc subprocess separation is recommended.
"""

import logging
import os
import threading
import time
import webbrowser
from pathlib import Path

from services import logging_setup

_log = logging.getLogger(__name__)

logging_setup.configure()

# ADB bootstrap MUST run before anything imports adbutils (api.devices →
# scrcpy.scrcpyclients → adbutils all chain into the singleton AdbClient on
# first access). If the user doesn't have Platform Tools installed, we
# extract our bundled copy from resources/re_adb/ into resources/runpath/
# and prepend it to PATH so adbutils picks it up.
from services import adb_bootstrap

adb_bootstrap.ensure_adb()

# Adaptive anti-mosaic controller. The heal loop reverts L2/L3 tightened
# tunables after a sustained healthy window. Cheap (~ one dict scan every
# 10 s), starting it unconditionally keeps the wiring simple.
from services import quality_controller as _quality_controller

_quality_controller.start_heal_loop()

from datetime import timedelta

from api.authentication import bp as auth_bp
from api.devices import bp as devices_bp
from api.reservations import bp as reservations_bp
from api.streams import bp as streams_bp
from flask import Flask, abort, jsonify, request, send_from_directory
from flask_socketio import SocketIO
from scrcpy.scrcpynetwork import get_local_ip
from services import authentication as auth_service
from services import database as db_module
from services import reservations as reservations_service

FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"

# Static / templates folders disabled — the Vue dist is the entire UI.
app = Flask(__name__, template_folder=None, static_folder=None)

# Secret key signs the session cookie that carries the opaque auth token.
# Set FLASK_SECRET_KEY in the environment for a stable key across restarts
# (otherwise every restart invalidates sessions — acceptable for dev, not
# for prod). A random fallback keeps dev working out of the box.
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(32).hex()
app.permanent_session_lifetime = timedelta(hours=auth_service.SESSION_TTL_HOURS)

# Bootstrap the account/reservation database before any request can hit it.
db_module.init_db()

# Why ``threading`` and not ``eventlet``? aiortc (our WebRTC stack) runs in
# a dedicated asyncio thread and needs the real OS socket module. Eventlet's
# monkey_patch() hijacks ``socket`` process-wide, which deadlocks asyncio's
# selector. Threading mode keeps sockets native.
#
# Trade-off: Werkzeug + threading mode cannot complete a WebSocket upgrade
# cleanly (simple-websocket vs Werkzeug strictness). We compensate by
# forcing Socket.IO clients to HTTP long-polling — see ``useScrcpySession``
# in the frontend. Polling is plenty for signaling traffic; the heavy data
# planes (video, input, files) run over WebRTC's own RTP/DataChannel.
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
app.register_blueprint(auth_bp, url_prefix="/api")
app.register_blueprint(devices_bp, url_prefix="/api")
app.register_blueprint(streams_bp, url_prefix="/api")
app.register_blueprint(reservations_bp, url_prefix="/api")

# Expire reservations in the background (stop scrcpy + drop peers + notify).
reservations_service.start_sweeper()

# Endpoints reachable without a session. Everything else under /api/ is
# gated by the before_request hook below. Static SPA routes (/, /assets,
# /<file>) are intentionally public so the shell can load and then probe
# /api/auth/check-auth to decide login vs app.
_PUBLIC_API_PATHS = {
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/check-auth",
}


@app.before_request
def _require_login_for_api():
    """Central login gate for the whole REST surface.

    Per-route ownership checks (reservation enforcement) live in the device
    routes; this hook only enforces *authentication*, not authorization.
    """
    path = request.path
    if not path.startswith("/api/"):
        return None
    if request.method == "OPTIONS":  # CORS preflight
        return None
    if path in _PUBLIC_API_PATHS:
        return None
    if auth_service.current_user() is None:
        return jsonify({"status": "failed", "error": "未登录或会话已过期"}), 401
    return None

# WebRTC signaling. ``ENABLE_WEBRTC=0`` only acts as a forced kill-switch
# — the default behaviour already auto-falls-back when aiortc is missing.
if os.environ.get("ENABLE_WEBRTC", "1") != "0":
    try:
        from api.webrtc import register as register_webrtc

        register_webrtc(socketio)
        _log.info("WebRTC signaling registered")
    except Exception as _webrtc_exc:  # noqa: BLE001
        _log.warning("WebRTC disabled: %s", _webrtc_exc)

# Embedded ADB terminal (xterm.js). Same opt-out semantics as WebRTC above.
if os.environ.get("ENABLE_TERMINAL", "1") != "0":
    try:
        from terminal.routes import register as register_terminal

        register_terminal(socketio)
        _log.info("ADB terminal registered")
    except Exception as _term_exc:  # noqa: BLE001
        _log.warning("Terminal disabled: %s", _term_exc)


@app.route("/")
def index():
    _log.info("Serving index page /")
    if not FRONTEND_DIST.exists():
        abort(503, "frontend/dist not found. Run `npm run build` inside frontend/.")
    return send_from_directory(FRONTEND_DIST, "index.html")


@app.route("/assets/<path:filename>")
def frontend_assets(filename):
    if not FRONTEND_DIST.exists():
        abort(404)
    return send_from_directory(FRONTEND_DIST / "assets", filename)


@app.route("/<path:filename>")
def frontend_root_asset(filename):
    """Serve files placed in ``frontend/public/`` (favicon, placeholder svg…)."""
    if FRONTEND_DIST.exists():
        target = FRONTEND_DIST / filename
        if target.is_file():
            return send_from_directory(FRONTEND_DIST, filename)
    abort(404)


def open_browser(host: str, port: int) -> None:
    """Open the local web UI in the system browser after the server is ready."""
    time.sleep(1)
    open_host = "127.0.0.1" if host in ("127.0.0.1", "0.0.0.0", "::") else host
    url = f"http://{open_host}:{port}"
    _log.info("Opening browser: %s", url)
    webbrowser.open_new_tab(url)


if __name__ == "__main__":
    bind_host = os.environ.get("HOST") or get_local_ip()
    bind_port = int(os.environ.get("PORT", "5001"))

    if not hasattr(app, "browser_opened") or not app.browser_opened:
        if os.environ.get("OPEN_BROWSER", "1") != "0":
            browser_thread = threading.Thread(
                target=open_browser, args=(bind_host, bind_port), daemon=True
            )
            browser_thread.start()
        app.browser_opened = True

    _log.info("Service started: http://%s:%d", bind_host, bind_port)
    # Flask-SocketIO 5.x demands ``allow_unsafe_werkzeug=True`` for threading
    # mode (it refuses Werkzeug otherwise). Dev-only — production needs a
    # real WSGI server.
    socketio.run(
        app,
        host=bind_host,
        port=bind_port,
        debug=False,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )
