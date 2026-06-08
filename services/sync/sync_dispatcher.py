"""Fan-out of master input events to slave devices (V1 coordinate sync).

Called from :func:`services.scrcpy_input.dispatch` *after* the master's own
control action has run. Given the master ``device_id`` and the already-parsed
event, it looks up the enabled sync group, maps coordinates from the master's
resolution to each slave's resolution, and injects the same action into every
slave's scrcpy control channel.

Hard rules (this is a lab feature — it must never disturb the master path):

* Every public entry point swallows all exceptions; the worst outcome is
  "this event wasn't mirrored", logged to ``logs/sync.log``.
* Fast events (touch/key/scroll/text) are injected inline — they're single
  socket writes. ``swipe`` walks an interpolated sequence with sleeps, so it
  is offloaded to a worker thread per slave (mirroring the master path in
  ``scrcpy_input``) to keep the aiortc asyncio loop responsive.
* Slaves that have no live scrcpy client are silently skipped (``peek_client``
  never boots one).

All execution/diagnostic output goes to a rotating **log file**, never the
database — per the project's "logs to files, not DB" rule.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from typing import Any, Optional

import scrcpy.scrcpyclients as scrcpy_clients
from config import config
from services.sync import (
    failure_collector,
    semantic_event_builder,
    sync_groups,
    u2_executor,
)

# ── dedicated file logger (no DB) ───────────────────────────────────────
# One line per fan-out attempt, JSON-ish so the future debug panel can read
# it back without a schema. Kept separate from the root logger so sync
# chatter doesn't drown the main console log. Path/rotation come from the
# central config (config/config.py).
_log = logging.getLogger("sync")
_log.setLevel(logging.INFO)
_log.propagate = False


def _ensure_handler() -> None:
    if _log.handlers:
        return
    try:
        config.SYNC_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            str(config.SYNC_LOG_PATH),
            maxBytes=config.SYNC_LOG_MAX_BYTES,
            backupCount=config.SYNC_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        # cspell:ignore asctime levelname
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        _log.addHandler(handler)
    except OSError:
        # Disk/permission problem — fall back to a null handler so logging
        # calls never raise into the dispatch path.
        _log.addHandler(logging.NullHandler())


def _record(event: str, **fields: Any) -> None:
    try:
        _ensure_handler()
        _log.info("%s %s", event, json.dumps(fields, ensure_ascii=False))
    except (OSError, TypeError, ValueError):
        pass


# In-memory last-result per slave (for the live UI). File log is the durable
# record; this is just the latest outcome so the panel can colour each slave.
_results: dict[str, dict] = {}
_results_lock = threading.Lock()


def _set_result(slave_id: str, success: bool, strategy: str) -> None:
    with _results_lock:
        _results[slave_id] = {
            "success": bool(success),
            "strategy": strategy,
            "ts": time.time(),
        }


def recent_results() -> dict:
    with _results_lock:
        return {k: dict(v) for k, v in _results.items()}


# ── semantic-tap throttle (storm protection) ────────────────────────────
# A semantic tap triggers an expensive master UI dump. Allow at most one
# in-flight worker per master, and drop taps that arrive within the throttle
# interval, so rapid taps can't pile up dumps.
_inflight: set = set()
_last_tap: dict[str, float] = {}
_throttle_lock = threading.Lock()

# Per-master touch-down position, so semantic mode can tell a tap (→ control
# click) from a drag (→ coordinate-mirrored swipe). Only touched on the aiortc
# loop thread (fanout), which is single-threaded, so no lock needed.
_touch_down: dict[str, tuple] = {}


def _allow_semantic_tap(master_device_id: str) -> bool:
    now = time.time()
    interval = config.SYNC_TAP_THROTTLE_MS / 1000.0
    with _throttle_lock:
        if master_device_id in _inflight:
            return False
        if now - _last_tap.get(master_device_id, 0.0) < interval:
            return False
        _inflight.add(master_device_id)
        _last_tap[master_device_id] = now
        return True


def _release_inflight(master_device_id: str) -> None:
    with _throttle_lock:
        _inflight.discard(master_device_id)


# ── coordinate mapping ──────────────────────────────────────────────────

def _resolution(device_id: str) -> Optional[tuple[int, int]]:
    client = scrcpy_clients.peek_client(device_id)
    if client is None:
        return None
    res = client.resolution
    if not res:
        return None
    try:
        w, h = int(res[0]), int(res[1])
    except (TypeError, ValueError, IndexError):
        return None
    if w <= 0 or h <= 0:
        return None
    return w, h


def _map_point(
    x: float, y: float, master: tuple[int, int], slave: tuple[int, int]
) -> tuple[int, int]:
    """Scale a master-pixel point into the slave's pixel space by ratio."""
    mw, mh = master
    sw, sh = slave
    nx = int(round((x / mw) * sw))
    ny = int(round((y / mh) * sh))
    # Clamp inside the slave screen so a rounding overshoot can't land off-bounds.
    nx = max(0, min(sw - 1, nx))
    ny = max(0, min(sh - 1, ny))
    return nx, ny


# ── event injection per slave ───────────────────────────────────────────

def _inject(
    slave_id: str,
    t: str,
    evt: dict,
    master_res: tuple[int, int],
) -> None:
    client = scrcpy_clients.peek_client(slave_id)
    if client is None:
        _record("skip", slave=slave_id, type=t, reason="no_client")
        return
    slave_res = _resolution(slave_id)
    if slave_res is None:
        _record("skip", slave=slave_id, type=t, reason="no_resolution")
        return

    control = client.control
    if t == "touch":
        nx, ny = _map_point(evt["x"], evt["y"], master_res, slave_res)
        control.touch(nx, ny, evt["action"])
    elif t == "scroll":
        nx, ny = _map_point(evt["x"], evt["y"], master_res, slave_res)
        control.scroll(nx, ny, int(evt.get("h", 0)), int(evt.get("v", 0)))
    elif t == "key":
        control.keycode(evt["code"], evt["action"])
    elif t == "text":
        control.text(evt["text"])
    elif t == "swipe":
        sx, sy = _map_point(evt["startX"], evt["startY"], master_res, slave_res)
        ex, ey = _map_point(evt["endX"], evt["endY"], master_res, slave_res)
        dur = evt.get("duration", config.SYNC_SWIPE_DEFAULT_MS)

        def _run_swipe(
            c=control,
            start_x=sx,
            start_y=sy,
            end_x=ex,
            end_y=ey,
            duration_ms=dur,
            sid=slave_id,
        ):
            try:
                c.swipe(start_x, start_y, end_x, end_y, duration_ms=duration_ms)
            except (OSError, AttributeError, RuntimeError) as exc:
                _record("error", slave=sid, type="swipe", error=str(exc))

        threading.Thread(
            target=_run_swipe, name=f"sync-swipe-{slave_id[:8]}", daemon=True
        ).start()
        return  # swipe logged by its worker on failure only
    else:
        return
    _record("ok", slave=slave_id, type=t)
    _set_result(slave_id, True, f"coordinate:{t}")


# Which group flag gates each event type.
_FLAG_FOR_TYPE = {
    "touch": "sync_touch",
    "scroll": "sync_touch",
    "swipe": "sync_touch",
    "key": "sync_keyevent",
    "text": "sync_text",
}


def fanout(master_device_id: str, t: str, evt: dict) -> None:
    """Mirror one master event to every slave of its enabled group.

    Never raises: this runs on the aiortc loop thread and must not be able
    to break the master's own input handling.
    """
    try:
        if t not in _FLAG_FOR_TYPE:
            return
        group = sync_groups.get_by_master(master_device_id)
        if group is None:
            return
        if not getattr(group, _FLAG_FOR_TYPE[t], False):
            return

        # Semantic-first mode: a tap (touch release) is mirrored as an u2
        # control click with coordinate fallback. The master-side UI dump is
        # slow (100ms-2s), so the whole semantic path is offloaded to a worker
        # thread — it must NEVER run on the aiortc loop. Down/move touches are
        # suppressed (only the release becomes a click) to avoid double input.
        # Non-tap events (swipe/scroll/key/text) fall through to coordinate.
        if getattr(group, "mode", "coordinate") == "semantic":
            if t == "touch":
                action = int(evt.get("action", -1))
                if action == 0:  # DOWN — remember start to classify on release
                    _touch_down[master_device_id] = (evt["x"], evt["y"], time.time())
                    return
                if action != 1:  # MOVE etc. — net gesture handled on release
                    return
                # UP — tap vs drag.
                down = _touch_down.pop(master_device_id, None)
                ux, uy = evt["x"], evt["y"]
                slaves = list(group.slave_device_ids)
                if down is not None:
                    dx, dy = ux - down[0], uy - down[1]
                    if (dx * dx + dy * dy) >= (config.SYNC_SWIPE_MIN_PX ** 2):
                        # A drag/swipe — semantic has no "drag a control" concept,
                        # so mirror it as a coordinate swipe to every slave.
                        dur = max(50, min(1000, int((time.time() - down[2]) * 1000)))
                        threading.Thread(
                            target=_coordinate_swipe_worker,
                            name="sync-swipe",
                            args=(master_device_id, slaves, down[0], down[1], ux, uy, dur),
                            daemon=True,
                        ).start()
                        return
                # A tap — semantic control click (throttled).
                if not _allow_semantic_tap(master_device_id):
                    _record("throttled", master=master_device_id)
                    return
                threading.Thread(
                    target=_semantic_tap_worker,
                    name="sync-semantic",
                    args=(master_device_id, slaves, dict(evt)),
                    daemon=True,
                ).start()
                return
            if t == "text":
                txt = evt.get("text")
                if txt:
                    slaves = list(group.slave_device_ids)
                    threading.Thread(
                        target=_text_worker,
                        name="sync-text",
                        args=(master_device_id, slaves, txt),
                        daemon=True,
                    ).start()
                return
            # key / scroll / swipe fall through to the coordinate path below.

        master_res = _resolution(master_device_id)
        if master_res is None:
            _record("skip", master=master_device_id, type=t, reason="no_master_resolution")
            return
        for slave_id in list(group.slave_device_ids):
            try:
                _inject(slave_id, t, evt, master_res)
            except (KeyError, ValueError, TypeError, OSError, AttributeError) as exc:
                _record("error", slave=slave_id, type=t, error=str(exc))
    except (KeyError, ValueError, TypeError, OSError, AttributeError, RuntimeError) as exc:
        _record("fatal", master=master_device_id, type=t, error=str(exc))


def _run_concurrent(slaves: list[str], fn) -> None:
    """Run ``fn(slave)`` for each slave concurrently (bounded). Per-device u2
    calls stay serialised by their own lock, so parallel here is safe."""
    if not slaves:
        return
    workers = min(config.SYNC_MAX_CONCURRENCY, len(slaves))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sync-fan") as pool:
        for slave_id in slaves:
            pool.submit(fn, slave_id)


def _coordinate_text(slave_id: str, text: str) -> bool:
    """Fallback text injection via scrcpy control."""
    client = scrcpy_clients.peek_client(slave_id)
    if client is None:
        return False
    try:
        client.control.text(text)
        return True
    except (OSError, AttributeError, RuntimeError):
        return False


def _coordinate_tap(slave_id: str, master_res: tuple[int, int], mx: float, my: float) -> bool:
    """Fallback: ratio-map the master point and inject a full tap on the slave."""
    if master_res is None:
        return False
    client = scrcpy_clients.peek_client(slave_id)
    if client is None:
        return False
    slave_res = _resolution(slave_id)
    if slave_res is None:
        return False
    nx, ny = _map_point(mx, my, master_res, slave_res)
    try:
        client.control.touch(nx, ny, 0)  # ACTION_DOWN
        client.control.touch(nx, ny, 1)  # ACTION_UP
        return True
    except (OSError, AttributeError, RuntimeError):
        return False


def _slave_still_valid(master_device_id: str, slave_id: str) -> bool:
    """Concurrency guard: confirm this slave should STILL be driven right now.

    Between the tap firing and this worker injecting, another user/admin may
    have released the device, the reservation may have expired, or the group
    may have changed. Re-check (fresh) that: the group still exists + enabled,
    the slave is still a member, and it's still reserved by the group's owner.
    """
    group = sync_groups.get_by_master(master_device_id)
    if group is None or not group.enabled:
        return False
    if slave_id not in group.slave_device_ids:
        return False
    try:
        from services import reservations

        res = reservations.get(slave_id)
    except (ImportError, ModuleNotFoundError, sqlite3.Error, OSError, RuntimeError):
        return True
    if res is None or res.get("user_id") != group.owner_user_id:
        return False
    return True


def _process_slave(master_device_id, group_id, slave_id, sem, selector, use_semantic, master_res, mx, my) -> None:
    """Drive one slave: re-validate, semantic click, coordinate fallback, evidence."""
    # Re-validate ownership/membership just before touching the device.
    if not _slave_still_valid(master_device_id, slave_id):
        _record("skip_revalidate", slave=slave_id)
        return
    result = None
    if use_semantic:
        result = u2_executor.execute_click(slave_id, selector)
        if result.get("success"):
            _record("semantic_ok", slave=slave_id, strategy=result.get("strategy"))
            _set_result(slave_id, True, f"semantic:{result.get('strategy')}")
            return
    # Coordinate fallback (semantic disabled, no control, or click failed).
    ok = _coordinate_tap(slave_id, master_res, mx, my)
    _record(
        "fallback_ok" if ok else "fallback_fail",
        slave=slave_id,
        reason=(result or {}).get("error") if result else sem.get("mode"),
    )
    _set_result(slave_id, ok, "fallback" if ok else "failed")
    if not ok:
        # Total failure (semantic + coordinate both failed) → capture evidence.
        evidence = failure_collector.capture(
            group_id,
            master_device_id,
            slave_id,
            {
                "point": [int(mx), int(my)],
                "semantic_mode": sem.get("mode"),
                "semantic_result": result,
            },
        )
        if evidence:
            _record("evidence", slave=slave_id, **evidence)


def _semantic_tap_worker(master_device_id: str, slaves: list[str], evt: dict) -> None:
    """Off-loop: build the semantic event from the master once, then drive all
    slaves concurrently (per-device u2 calls stay serialised by their own lock).
    Always releases the throttle in-flight flag for this master."""
    try:
        group = sync_groups.get_by_master(master_device_id)
        if group is None:
            return  # group dissolved/disabled between tap and worker
        group_id = group.id
        mx, my = evt["x"], evt["y"]
        sem = semantic_event_builder.build_tap(master_device_id, mx, my)
        master_res = _resolution(master_device_id)
        selector = sem.get("selector")
        use_semantic = sem.get("mode") == "semantic" and bool(selector)

        if not slaves:
            return
        workers = min(config.SYNC_MAX_CONCURRENCY, len(slaves))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sync-slave") as pool:
            for slave_id in slaves:
                pool.submit(
                    _process_slave,
                    master_device_id, group_id, slave_id,
                    sem, selector, use_semantic, master_res, mx, my,
                )
    except (KeyError, TypeError, ValueError, OSError, RuntimeError, AttributeError) as exc:
        _record("semantic_fatal", master=master_device_id, error=str(exc))
    finally:
        _release_inflight(master_device_id)


def _coordinate_swipe_worker(master_device_id, slaves, sx, sy, ex, ey, duration) -> None:
    """Mirror a master screen drag as a ratio-mapped swipe on each slave
    (used by semantic mode when a touch turns out to be a drag, not a tap)."""
    master_res = _resolution(master_device_id)
    if master_res is None:
        _record("skip", master=master_device_id, type="swipe", reason="no_master_resolution")
        return
    evt = {"startX": sx, "startY": sy, "endX": ex, "endY": ey, "duration": duration}

    def _do(slave_id):
        if not _slave_still_valid(master_device_id, slave_id):
            _record("skip_revalidate", slave=slave_id)
            return
        try:
            _inject(slave_id, "swipe", evt, master_res)
            _record("drag_swipe_ok", slave=slave_id)
            _set_result(slave_id, True, "swipe")
        except (KeyError, ValueError, TypeError, OSError, AttributeError) as exc:
            _record("error", slave=slave_id, type="swipe", error=str(exc))

    _run_concurrent(slaves, _do)


def _text_worker(master_device_id: str, slaves: list[str], text: str) -> None:
    """Semantic text: type via u2 on each slave (targets focused field), with
    scrcpy text injection as fallback. Concurrent + re-validated per slave."""
    def _do(slave_id):
        if not _slave_still_valid(master_device_id, slave_id):
            _record("skip_revalidate", slave=slave_id)
            return
        res = u2_executor.execute_text(slave_id, text)
        if res.get("success"):
            _record("text_ok", slave=slave_id, strategy=res.get("strategy"))
            _set_result(slave_id, True, "semantic:text_input")
            return
        ok = _coordinate_text(slave_id, text)
        _record("text_fallback_ok" if ok else "text_fallback_fail",
                slave=slave_id, reason=res.get("error"))
        _set_result(slave_id, ok, "text" if ok else "failed")

    try:
        _run_concurrent(slaves, _do)
    except (OSError, RuntimeError) as exc:
        _record("text_fatal", master=master_device_id, error=str(exc))


# ── HTTP-triggered fan-out (quick-action buttons: keyevent / directional swipe).
# Device-level (no coordinate identity), work via adb shell, so they apply in
# BOTH modes and don't need a scrcpy stream on the slaves. Called (guarded)
# from the api.devices routes. ──

def fanout_keyevent(master_device_id: str, code: int) -> None:
    """Mirror a quick keyevent (back/home/recent/…) to every slave."""
    try:
        group = sync_groups.get_by_master(master_device_id)
        if group is None or not group.enabled or not group.sync_keyevent:
            return
        slaves = list(group.slave_device_ids)
        threading.Thread(
            target=_keyevent_worker, name="sync-key",
            args=(master_device_id, slaves, int(code)), daemon=True,
        ).start()
    except (ValueError, TypeError, RuntimeError) as exc:
        _record("keyevent_fanout_error", master=master_device_id, error=str(exc))


def _keyevent_worker(master_device_id: str, slaves: list[str], code: int) -> None:
    from adbutils import adb

    def _do(slave_id):
        if not _slave_still_valid(master_device_id, slave_id):
            _record("skip_revalidate", slave=slave_id)
            return
        try:
            adb.device(slave_id).shell(f"input keyevent {code}", timeout=4)
            _record("key_ok", slave=slave_id, code=code)
            _set_result(slave_id, True, f"key:{code}")
        except (RuntimeError, OSError, TimeoutError) as exc:
            _record("key_fail", slave=slave_id, error=str(exc))
            _set_result(slave_id, False, "key_failed")

    _run_concurrent(slaves, _do)


def fanout_swipe_direction(master_device_id: str, direction: str, duration: int) -> None:
    """Mirror a directional swipe; each slave recomputes coords from its own
    screen size (adaptive), so it works across resolutions."""
    try:
        group = sync_groups.get_by_master(master_device_id)
        if group is None or not group.enabled or not group.sync_touch:
            return
        slaves = list(group.slave_device_ids)
        threading.Thread(
            target=_swipe_dir_worker, name="sync-swipedir",
            args=(master_device_id, slaves, direction, duration), daemon=True,
        ).start()
    except (ValueError, TypeError, RuntimeError) as exc:
        _record("swipe_fanout_error", master=master_device_id, error=str(exc))


def _swipe_dir_worker(master_device_id: str, slaves: list[str], direction: str, duration: int) -> None:
    from services import input_shell

    def _do(slave_id):
        if not _slave_still_valid(master_device_id, slave_id):
            _record("skip_revalidate", slave=slave_id)
            return
        try:
            input_shell.swipe_direction(slave_id, direction, duration)
            _record("swipe_ok", slave=slave_id, direction=direction)
            _set_result(slave_id, True, f"swipe:{direction}")
        except (input_shell.InputShellError, RuntimeError, OSError, TimeoutError) as exc:
            _record("swipe_fail", slave=slave_id, error=str(exc))
            _set_result(slave_id, False, "swipe_failed")

    _run_concurrent(slaves, _do)


def status_snapshot() -> dict:
    """Lightweight liveness + last-result view for the API/UI."""
    out = {"ts": time.time(), "groups": [], "results": recent_results()}
    for group in sync_groups.list_groups():
        members = {}
        for dev in group.member_ids():
            client = scrcpy_clients.peek_client(dev)
            members[dev] = bool(client is not None)
        out["groups"].append({"id": group.id, "members_live": members})
    return out
