"""Semantic event builder (Lab V2.2).

Turns a raw master-screen tap into a *semantic event* that V2.3 can replay on
slaves of any resolution: it reverse-looks-up the control under the tap (via
:mod:`services.sync.u2_inspect`) and packages the resulting selector together
with the original coordinate as a fallback.

Shape produced::

    {
      "type": "click",
      "mode": "semantic" | "coordinate",   # coordinate = no control resolved
      "master_device_id": "<serial>",
      "raw": {"x": <px>, "y": <px>},
      "selector": { resourceId, text, className, description, xpath, bounds } | None,
      "fallback": {"type": "tap", "x": <px>, "y": <px>},
    }

Only tap/click events carry semantics; swipe / scroll / key / text have no
control identity and are left to the coordinate path (``build`` returns None
for them). Defensive: never raises — returns a coordinate-mode event (or
None) if u2 introspection fails, so the caller can always fall back.
"""

from __future__ import annotations

import logging
from typing import Optional

from services.sync import u2_inspect
from services.sync.u2_pool import U2Error

_log = logging.getLogger("sync")


def build_tap(master_device_id: str, x: float, y: float) -> dict:
    """Build a semantic click event for a tap at (x, y) on the master."""
    raw = {"x": int(x), "y": int(y)}
    fallback = {"type": "tap", "x": int(x), "y": int(y)}
    selector = None
    try:
        result = u2_inspect.inspect_point(master_device_id, x, y)
        selector = result.get("selector")
    except U2Error as exc:
        _log.warning("semantic build: inspect failed on %s: %s", master_device_id, exc)
    except Exception as exc:  # noqa: BLE001 — degrade to coordinate, never raise
        _log.warning("semantic build: unexpected error on %s: %s", master_device_id, exc)
    # Confidence gate: nearly every pixel hits SOME node (the root decor view),
    # so only call it "semantic" when the control is actually actionable —
    # clickable, or carrying text / content-desc. A resource-id-only layout
    # container isn't reliably re-locatable on a slave, so prefer coordinate.
    actionable = bool(
        selector
        and (selector.get("clickable") or selector.get("text") or selector.get("description"))
    )
    return {
        "type": "click",
        "mode": "semantic" if actionable else "coordinate",
        "master_device_id": master_device_id,
        "raw": raw,
        "selector": selector,
        "fallback": fallback,
    }


def _is_tap(evt: dict) -> Optional[tuple[float, float]]:
    """Return (x, y) if ``evt`` represents a discrete tap, else None.

    Accepts the input-dispatch touch schema ({'t':'touch','x','y','action'};
    a tap is the ACTION_UP=1 edge) and an explicit {'type':'tap'/'click'}.
    """
    t = evt.get("t") or evt.get("type")
    if t in ("tap", "click") and "x" in evt and "y" in evt:
        return float(evt["x"]), float(evt["y"])
    if t == "touch" and int(evt.get("action", -1)) == 1 and "x" in evt and "y" in evt:
        return float(evt["x"]), float(evt["y"])
    return None


def build(master_device_id: str, raw_event: dict) -> Optional[dict]:
    """Build a semantic event for ``raw_event`` if it's a tap, else None."""
    if not isinstance(raw_event, dict):
        return None
    point = _is_tap(raw_event)
    if point is None:
        return None
    return build_tap(master_device_id, point[0], point[1])
