"""HTTP control plane for the experimental master→slave sync feature.

This blueprint is only registered when ``ENABLE_SYNC=1`` (see ``app.py``),
so when the lab feature is off these routes don't exist at all.

The *data plane* (actually mirroring input) lives in
:mod:`services.sync.sync_dispatcher`, invoked from the WebRTC input
dispatcher. These routes only manage group membership and the batch
reservation that backs it.

Reservation model
-----------------
Creating a group batch-reserves the master + every slave for the caller
via ``reservations.claim(..., allow_multi=True)`` — the one opt-in that
lets a regular user hold more than one device. Dissolving a group releases
the *slaves* (the extra devices) but leaves the master reserved, so the
user keeps their primary stream and the one-device-per-user rule is
restored for normal claims.
"""

from __future__ import annotations

import logging
import sqlite3

from flask import Blueprint, jsonify, request
from adbutils import AdbError

import scrcpy.scrcpyclients as scrcpy_clients
from services import authentication, reservations
from services.sync import (
    semantic_event_builder,
    sync_dispatcher,
    sync_groups,
    u2_inspect,
)
from services.sync.u2_pool import U2Error

_log = logging.getLogger(__name__)

bp = Blueprint("sync_api", __name__)


def _serialize(group: sync_groups.SyncGroup, viewer: dict) -> dict:
    data = group.to_dict()
    data["is_mine"] = group.owner_user_id == viewer["id"]
    return data


def _can_manage(group: sync_groups.SyncGroup, user: dict) -> bool:
    return group.owner_user_id == user["id"] or user.get("role") in (
        "admin",
        "super_admin",
    )


@bp.route("/sync-groups", methods=["GET"])
@authentication.login_required
def list_groups():
    user = authentication.current_user()
    is_admin = user.get("role") in ("admin", "super_admin")
    groups = sync_groups.list_groups(None if is_admin else user["id"])
    return jsonify(
        {
            "status": "ok",
            "groups": [_serialize(g, user) for g in groups],
            "live": sync_dispatcher.status_snapshot(),
        }
    )


@bp.route("/sync-groups", methods=["POST"])
@authentication.login_required
def create_group():
    user = authentication.current_user()
    body = request.get_json(silent=True) or {}
    master = (body.get("master_device_id") or "").strip()
    slaves = body.get("slave_device_ids") or []
    if not isinstance(slaves, list):
        return jsonify({"status": "failed", "error": "slave_device_ids 必须是数组"}), 400

    mode = "semantic" if body.get("mode") == "semantic" else "coordinate"
    flags = {
        "mode": mode,
        "sync_touch": bool(body.get("sync_touch", True)),
        "sync_keyevent": bool(body.get("sync_keyevent", True)),
        "sync_text": bool(body.get("sync_text", True)),
    }

    # 1) Ownership guard: every target device must be free or already ours.
    members = [master, *[s for s in slaves if isinstance(s, str)]]
    for dev in members:
        dev = (dev or "").strip()
        if not dev:
            continue
        existing = reservations.get(dev)
        if existing is not None and existing["user_id"] != user["id"]:
            return (
                jsonify(
                    {
                        "status": "failed",
                        "error": f"设备 {dev} 已被 {existing['username']} 占用",
                    }
                ),
                409,
            )

    # 2) Create the group (validates membership conflicts in-memory).
    try:
        group = sync_groups.create_group(
            user["id"], master, slaves, **flags
        )
    except sync_groups.SyncGroupError as exc:
        return jsonify({"status": "failed", "error": str(exc)}), 409

    # 3) Batch-reserve master + slaves for the caller (multi-device opt-in).
    claimed: list[str] = []
    try:
        for dev in group.member_ids():
            reservations.claim(
                dev, user, reservations.DEFAULT_RESERVATION_MINUTES, allow_multi=True
            )
            claimed.append(dev)
    except reservations.ReservationError as exc:
        # Roll back: release whatever we grabbed and drop the group so we
        # never leave a half-reserved, unusable group behind.
        for dev in claimed:
            try:
                reservations.release(dev, reason="sync_rollback")
            except (sqlite3.Error, OSError, RuntimeError, AttributeError):
                pass
        sync_groups.delete_group(group.id)
        return jsonify({"status": "failed", "error": str(exc)}), 409

    _log.info(
        "Sync group %s created by %s (master=%s, slaves=%s)",
        group.id, user["username"], master, group.slave_device_ids,
    )
    return jsonify({"status": "ok", "group": _serialize(group, user)})


@bp.route("/sync-groups/<group_id>", methods=["DELETE"])
@authentication.login_required
def delete_group(group_id: str):
    user = authentication.current_user()
    group = sync_groups.get(group_id)
    if group is None:
        return jsonify({"status": "ok"})  # idempotent
    if not _can_manage(group, user):
        return jsonify({"status": "failed", "error": "无权操作该同步组"}), 403

    sync_groups.delete_group(group_id)
    # Release the extra (slave) devices; keep the master reserved so the
    # owner keeps their primary stream and the one-device rule is restored.
    for dev in group.slave_device_ids:
        try:
            reservations.release(dev, reason="sync_group_deleted")
        except (sqlite3.Error, OSError, RuntimeError, AttributeError) as exc:
            _log.warning("release %s after group delete failed: %s", dev, exc)
    _log.info("Sync group %s deleted by %s", group_id, user["username"])
    return jsonify({"status": "ok"})


def _set_enabled(group_id: str, enabled: bool):
    user = authentication.current_user()
    group = sync_groups.get(group_id)
    if group is None:
        return jsonify({"status": "failed", "error": "同步组不存在"}), 404
    if not _can_manage(group, user):
        return jsonify({"status": "failed", "error": "无权操作该同步组"}), 403
    updated = sync_groups.set_enabled(group_id, enabled)
    return jsonify({"status": "ok", "group": _serialize(updated, user)})


@bp.route("/sync-groups/<group_id>/enable", methods=["POST"])
@authentication.login_required
def enable_group(group_id: str):
    return _set_enabled(group_id, True)


@bp.route("/sync-groups/<group_id>/disable", methods=["POST"])
@authentication.login_required
def disable_group(group_id: str):
    return _set_enabled(group_id, False)


@bp.route("/sync-groups/precheck", methods=["POST"])
@authentication.login_required
def precheck():
    """Fast pre-flight for the builder UI: per-device online / reservation /
    already-in-a-group status. Deliberately does NOT provision u2 (slow)."""
    user = authentication.current_user()
    body = request.get_json(silent=True) or {}
    ids = body.get("device_ids") or []
    if not isinstance(ids, list):
        return jsonify({"status": "failed", "error": "device_ids 必须是数组"}), 400
    try:
        online = set(scrcpy_clients.get_devices())
    except (AdbError, OSError, RuntimeError, AttributeError):
        online = set()
    out = []
    for dev in ids:
        dev = (dev or "").strip()
        if not dev:
            continue
        res = reservations.get(dev)
        out.append(
            {
                "device_id": dev,
                "online": dev in online,
                "reserved_by_me": bool(res and res["user_id"] == user["id"]),
                "reserved_by_other": bool(res and res["user_id"] != user["id"]),
                "in_group": bool(sync_groups.groups_for_device(dev)),
            }
        )
    return jsonify({"status": "ok", "devices": out})


# ── uiautomator2 read-only introspection (V2.1) ──────────────────────────
# These run u2's blocking IO on the Flask request thread (threading mode),
# never the aiortc loop. The device must be reserved by the caller.

def _u2_owner_guard(device_id: str):
    """Return (user, None) if the caller owns the device, else (None, resp)."""
    user = authentication.current_user()
    try:
        reservations.assert_owner(device_id, user)
    except reservations.ReservationError as exc:
        return None, (jsonify({"status": "failed", "error": str(exc)}), 403)
    return user, None


@bp.route("/devices/<device_id>/u2/ping", methods=["GET"])
@authentication.login_required
def u2_ping(device_id: str):
    _, err = _u2_owner_guard(device_id)
    if err:
        return err
    try:
        return jsonify({"status": "ok", **u2_inspect.ping(device_id)})
    except U2Error as exc:
        return jsonify({"status": "failed", "error": str(exc)}), 503


@bp.route("/devices/<device_id>/u2/hierarchy", methods=["GET"])
@authentication.login_required
def u2_hierarchy(device_id: str):
    _, err = _u2_owner_guard(device_id)
    if err:
        return err
    try:
        return jsonify({"status": "ok", "xml": u2_inspect.dump_hierarchy(device_id)})
    except U2Error as exc:
        return jsonify({"status": "failed", "error": str(exc)}), 503


@bp.route("/devices/<device_id>/u2/inspect", methods=["POST"])
@authentication.login_required
def u2_inspect_point(device_id: str):
    _, err = _u2_owner_guard(device_id)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    try:
        x = float(body.get("x"))
        y = float(body.get("y"))
    except (TypeError, ValueError):
        return jsonify({"status": "failed", "error": "缺少有效的 x / y 坐标"}), 400
    try:
        return jsonify({"status": "ok", **u2_inspect.inspect_point(device_id, x, y)})
    except U2Error as exc:
        return jsonify({"status": "failed", "error": str(exc)}), 503


@bp.route("/devices/<device_id>/u2/semantic", methods=["POST"])
@authentication.login_required
def u2_semantic(device_id: str):
    """Preview the semantic event a master tap at (x, y) would produce (V2.2)."""
    _, err = _u2_owner_guard(device_id)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    try:
        x = float(body.get("x"))
        y = float(body.get("y"))
    except (TypeError, ValueError):
        return jsonify({"status": "failed", "error": "缺少有效的 x / y 坐标"}), 400
    event = semantic_event_builder.build_tap(device_id, x, y)
    return jsonify({"status": "ok", "event": event})



