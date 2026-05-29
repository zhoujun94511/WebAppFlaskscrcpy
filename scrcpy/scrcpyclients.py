import hashlib
import threading
import time
from pathlib import Path

from adbutils import adb

from .scrcpycore import Client

_clients = {}
_client_lock = threading.Lock()
# Per-device reconfigure lock. The global ``_client_lock`` only protects
# the ``_clients`` dict; it gets released across the multi-second
# old.stop() + new Client.start() window of reconfigure(), so two
# concurrent reconfigures for the same device (e.g. BitrateController
# adjusting bitrate while QualityController promotes L2) could race
# their teardown / restart sequences and orphan a scrcpy server
# process. This per-device lock serialises the WHOLE reconfigure
# operation per device so the two callers queue up. Different devices
# remain independent (no global serialisation).
_reconfigure_locks: dict = {}
_reconfigure_locks_guard = threading.Lock()
# Per-device runtime config persisted across reconfigure() calls; the next
# get_client() will honour these.
_device_config: dict = {}


def _attach_lifecycle(client, device_id: str) -> None:
    """Best-effort lifecycle wiring for any freshly obtained client."""
    if client is None:
        return
    try:
        from services import scrcpy_lifecycle

        scrcpy_lifecycle.attach(client, device_id)
    except (ImportError, AttributeError, RuntimeError):
        pass


def _reconfigure_lock_for(device_id: str):
    """Lazily create + return the per-device reconfigure lock.

    ``RLock`` (not plain ``Lock``) because ``reconfigure()`` holds it
    across a call to ``get_client()``, which now ALSO acquires this
    lock for the build path. A non-reentrant lock would self-deadlock
    on the same thread. The lock is per-device, so reentrancy doesn't
    weaken cross-device parallelism.
    """
    with _reconfigure_locks_guard:
        lock = _reconfigure_locks.get(device_id)
        if lock is None:
            lock = threading.RLock()
            _reconfigure_locks[device_id] = lock
        return lock
SCRCPY_SERVER_REMOTE_PATH = "/data/local/tmp/scrcpy-server.jar"
SCRCPY_SERVER_LOCAL_PATH = (
    Path(__file__).resolve().parent.parent / "resources" / "scrcpy-server.jar"
)


def _sha256_file(path: Path):
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_devices():
    return [d.serial for d in adb.device_list()]


def get_local_server_info():
    exists = SCRCPY_SERVER_LOCAL_PATH.exists()
    return {
        "path": str(SCRCPY_SERVER_LOCAL_PATH),
        "exists": exists,
        "size": SCRCPY_SERVER_LOCAL_PATH.stat().st_size if exists else None,
        "sha256": _sha256_file(SCRCPY_SERVER_LOCAL_PATH),
    }


# TTL cache for the per-device scrcpy-server jar fingerprint. The frontend
# calls this endpoint 3-4 times per page load (server health card,
# stream-config probe, post-start re-check, etc.). Each call does two adb
# shell roundtrips (ls + sha256sum) which adds up to 50-175 ms per call on
# USB and worse on Wi-Fi adb. The jar on the device only changes when the
# user explicitly hits "Push server" — caching for a few seconds is safe
# and cuts the cumulative wait from ~400 ms to ~100 ms on a fresh load.
#
# Invalidated explicitly in :func:`push_server_to_device` so a re-push
# returns the new sha256 immediately.
_REMOTE_SERVER_INFO_TTL = 5.0  # seconds
_remote_server_info_cache: dict = {}
_remote_server_info_cache_lock = threading.Lock()


def _invalidate_remote_server_info(device_id: str) -> None:
    with _remote_server_info_cache_lock:
        _remote_server_info_cache.pop(device_id, None)


def get_remote_server_info(device_id):
    # Cache hit — return the previous answer without going back to adb. The
    # TTL is short enough that a re-push from another client wouldn't
    # actually starve the UI; the cache exists purely to dedupe the 3-4
    # near-simultaneous calls that happen during page load.
    now = time.monotonic()
    with _remote_server_info_cache_lock:
        entry = _remote_server_info_cache.get(device_id)
        if entry is not None and now - entry[0] < _REMOTE_SERVER_INFO_TTL:
            return entry[1]

    device = adb.device(device_id)
    try:
        # ONE shell roundtrip instead of two. The previous version called
        # ``device.shell()`` twice in a row; each call goes through the adb
        # daemon transport and pays a ~30-100 ms round-trip cost. Chaining
        # ls + sha256sum + a separator (echo) into a single command lets us
        # split the output client-side and saves a roundtrip per call.
        combined = device.shell(
            [
                "sh", "-c",
                f"ls -l {SCRCPY_SERVER_REMOTE_PATH} 2>/dev/null; "
                f"echo '---SHA256---'; "
                f"sha256sum {SCRCPY_SERVER_REMOTE_PATH} 2>/dev/null",
            ]
        ) or ""
        ls_output, _, sha_output = combined.partition("---SHA256---")
        exists = bool(ls_output.strip())
        remote_sha = sha_output.split()[0] if sha_output.strip() else None
        info = {
            "path": SCRCPY_SERVER_REMOTE_PATH,
            "exists": exists,
            "ls": ls_output.strip(),
            "sha256": remote_sha,
        }
    except Exception as exc:
        info = {
            "path": SCRCPY_SERVER_REMOTE_PATH,
            "exists": False,
            "error": str(exc),
        }

    with _remote_server_info_cache_lock:
        _remote_server_info_cache[device_id] = (now, info)
    return info


def push_server_to_device(device_id):
    if not SCRCPY_SERVER_LOCAL_PATH.exists():
        return False, f"missing local jar: {SCRCPY_SERVER_LOCAL_PATH}"
    device = adb.device(device_id)
    device.sync.push(str(SCRCPY_SERVER_LOCAL_PATH), SCRCPY_SERVER_REMOTE_PATH)
    # The remote jar just changed — drop any cached fingerprint so the
    # next health check fetches the new sha256 instead of returning stale.
    _invalidate_remote_server_info(device_id)
    return True, "pushed"


# Defaults tuned for high-refresh devices (Pixel 10 = 120 Hz, Xiaomi 14
# Ultra = 120 Hz, ROG = 165 Hz). Old defaults (8 Mbps + uncapped fps)
# starved the encoder on these screens: ~67 kbit/frame at 120 fps cannot
# resolve a 2k+ display without macroblock-skip cascades, which read on
# screen as persistent mosaic / reference-frame divergence.
#
# Bitrate was bumped to 16 Mbps for visual quality on 2k screens, but
# field-testing on POCO + Pixel 10 showed that pairing 16 Mbps @ 60 fps
# @ native resolution (1156×2510 / 1080×2424) with an image-heavy app
# in the foreground (e.g. Gallery's Glide loaders) saturated the
# binder + media pipeline enough to trigger HyperOS's silent userspace
# reboot. Dropped to 4 Mbps as a safe baseline — still well above the
# ~270 kbit/frame budget x264 needs for clean P-frames at 60 fps, and
# four times lower memory churn on the encoder side. Users with
# bandwidth to spare can override via ``/api/reconfigure``.
#
# ``i_frame_interval`` is the IDR cadence in seconds. 2 s is the default
# sweet spot (corrupted P-frames self-heal within 2 s). The adaptive
# quality controller (services/quality_controller.py) drops this to 1 s
# under sustained mosaic pressure (L2) and restores it after a healthy
# window (L4).
_DEFAULT_CLIENT_KWARGS = {
    "bitrate": 4000000,
    "max_width": 0,
    "max_fps": 60,
    "i_frame_interval": 2,
}

_ALLOWED_CONFIG_KEYS = {"bitrate", "max_width", "max_fps", "i_frame_interval"}


def _client_kwargs(device_id):
    overrides = _device_config.get(device_id, {})
    return {**_DEFAULT_CLIENT_KWARGS, **overrides}


def get_device_config(device_id):
    """Read-only snapshot of the per-device runtime config."""
    return dict(_client_kwargs(device_id))


def list_active_clients():
    """Snapshot of (serial, info) for every live scrcpy client."""
    out = []
    with _client_lock:
        for serial, client in list(_clients.items()):
            if client is None or not client.alive:
                continue
            res = client.resolution
            out.append({
                "device_id": serial,
                "alive": True,
                "device_name": client.device_name,
                "resolution": [int(res[0]), int(res[1])] if res else None,
                "config": {**_client_kwargs(serial)},
            })
    return out


def get_last_frame(device_id):
    """Return the most recently decoded BGR frame for the device, or None."""
    with _client_lock:
        client = _clients.get(device_id)
    if client is None or not client.alive:
        return None
    return getattr(client, "last_frame", None)


def peek_client(device_id):
    """Return the live client for ``device_id`` if one exists, else None.

    Unlike :func:`get_client`, never starts a new scrcpy session — used
    by callers that only want to interact with an already-active stream
    (e.g. the adaptive quality controller's L1 ``reset_video`` path,
    which must NOT boot a stream just because the user wandered onto an
    inactive device card).
    """
    with _client_lock:
        client = _clients.get(device_id)
        if client and client.alive:
            _attach_lifecycle(client, device_id)
            return client
    return None


def get_client(device_id):
    """Return the live client for ``device_id``, building one if needed.

    Concurrency model
    -----------------
    The global ``_client_lock`` is held ONLY for fast dict reads / writes
    — never during the multi-second ``Client()`` + ``start(threaded=True)``
    bootstrap. The original implementation held the lock through the whole
    bootstrap, which froze every other consumer (``peek_client``,
    ``list_active_clients``, ``get_device_config``, even other devices'
    reconfigure pop step) for 2-5 seconds while one device pushed the jar
    and accepted sockets — the entire device matrix appeared to hang.

    Serialisation for the "build a fresh client" path uses the SAME
    per-device ``_reconfigure_lock_for`` as ``reconfigure``, so concurrent
    ``get_client`` + ``reconfigure`` (or two ``get_client`` for the same
    device) can't both spawn fresh Client instances and leak one. The
    reconfigure lock is per-device, so different devices remain fully
    parallel.
    """
    # Fast path: already alive in the registry.
    with _client_lock:
        client = _clients.get(device_id)
        if client and client.alive:
            _attach_lifecycle(client, device_id)
            return client

    # Slow path: needs build (or stop+rebuild). Serialised per device.
    with _reconfigure_lock_for(device_id):
        # Re-check inside the build lock — another thread may have just
        # finished building one while we waited.
        with _client_lock:
            client = _clients.get(device_id)
        if client and client.alive:
            _attach_lifecycle(client, device_id)
            return client

        # Stop a dead-but-still-registered client OUTSIDE ``_client_lock``.
        # stop() fires EVENT_DISCONNECT, whose listeners may call back
        # into scrcpy_clients (peek_client, list_active_clients) — doing
        # this under a non-reentrant lock would self-deadlock.
        if client is not None:
            try:
                client.stop()
            except (RuntimeError, OSError, AttributeError):
                pass
            with _client_lock:
                # Only evict if the dead client is still the one in the
                # registry. Another concurrent build may have already
                # replaced it.
                if _clients.get(device_id) is client:
                    _clients.pop(device_id, None)

        # Build + start outside ``_client_lock``. This is the multi-second
        # work; while it runs every other consumer keeps fast read access.
        device = adb.device(device_id)
        new_client = Client(device, **_client_kwargs(device_id))
        if not new_client.start(threaded=True):
            new_client.stop()
            return None

        # Register atomically.
        with _client_lock:
            _clients[device_id] = new_client
        _attach_lifecycle(new_client, device_id)
        return new_client


def reconfigure(device_id, **kwargs):
    """Restart the scrcpy client for ``device_id`` with new params.

    Accepts ``bitrate`` (bps), ``max_width`` (px, 0 = device native), ``max_fps``
    (fps, 0 = uncapped). Unknown keys are ignored. Returns the new Client
    (or ``None`` if the device couldn't be brought back up).

    Caller is responsible for re-attaching downstream consumers (e.g. WebRTC
    peer sessions) to the new client.

    Concurrency: the per-device reconfigure lock serialises the whole
    pop → stop → start cycle so two callers (e.g. BitrateController and
    QualityController L2/L3 racing) can't interleave their teardown +
    restart, which previously could leave orphan scrcpy server
    processes and ADB tunnels.
    """
    sanitized = {k: int(v) for k, v in kwargs.items() if k in _ALLOWED_CONFIG_KEYS and v is not None}
    with _reconfigure_lock_for(device_id):
        with _client_lock:
            _device_config.setdefault(device_id, {}).update(sanitized)
            old = _clients.pop(device_id, None)

        if old is not None:
            try:
                old.stop()
            except (RuntimeError, OSError, AttributeError):
                pass

        new_client = get_client(device_id)

        return new_client, old


def stop_client(device_id):
    with _client_lock:
        client = _clients.pop(device_id, None)
    if client is not None:
        client.stop()
