"""HTTP routes for device reservations (occupancy).

Thin adapters over :mod:`services.reservations`. Listing is open to any
logged-in user (so the UI shows who hold what); claim/release act on the
current user; force-release is admin-only.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from services import authentication, reservations

bp = Blueprint("reservations_api", __name__)


def _serialize(res: dict | None, viewer: dict) -> dict | None:
    if res is None:
        return None
    return {
        "device_id": res["device_id"],
        "username": res["username"],
        "expires_at": res["expires_at"],
        "is_mine": res["user_id"] == viewer["id"],
    }


@bp.route("/reservations", methods=["GET"])
@authentication.login_required
def list_reservations():
    viewer = authentication.current_user()
    items = [_serialize(r, viewer) for r in reservations.list_all()]
    return jsonify(
        {
            "status": "ok",
            "reservations": items,
            "max_minutes": reservations.MAX_RESERVATION_MINUTES,
            "default_minutes": reservations.DEFAULT_RESERVATION_MINUTES,
        }
    )


@bp.route("/reservations", methods=["POST"])
@authentication.login_required
def claim_reservation():
    body = request.get_json(silent=True) or {}
    device_id = (body.get("device_id") or "").strip()
    minutes = body.get("minutes", reservations.DEFAULT_RESERVATION_MINUTES)
    user = authentication.current_user()
    try:
        res = reservations.claim(device_id, user, minutes)
    except reservations.ReservationError as exc:
        return jsonify({"status": "failed", "error": str(exc)}), 409
    return jsonify({"status": "ok", "reservation": _serialize(res, user)})


@bp.route("/reservations/<device_id>", methods=["DELETE"])
@authentication.login_required
def release_reservation(device_id: str):
    user = authentication.current_user()
    res = reservations.get(device_id)
    if res is None:
        return jsonify({"status": "ok"})  # already free — idempotent
    is_admin = user.get("role") in ("admin", "super_admin")
    if res["user_id"] != user["id"] and not is_admin:
        return jsonify({"status": "failed", "error": "无权释放他人占用的设备"}), 403
    reason = "force_released" if (is_admin and res["user_id"] != user["id"]) else "released"
    reservations.release(device_id, reason=reason)
    return jsonify({"status": "ok"})
