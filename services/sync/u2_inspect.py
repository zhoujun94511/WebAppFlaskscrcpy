"""Read-only uiautomator2 introspection (Lab V2.1).

Builds on :mod:`services.sync.u2_pool` to expose:

* :func:`ping`            — connectivity + window size + current app
* :func:`dump_hierarchy` — the raw UI hierarchy XML
* :func:`inspect_point`  — reverse-lookup the control under a (x, y) pixel and
                           generate a selector (resourceId / text / desc /
                           xpath / bounds). This is the seed of the V2 semantic
                           event builder.

Everything is defensive: failures raise :class:`U2Error` (mapped to a clean
JSON error by the API layer) rather than propagating raw u2/lxml exceptions.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from services.sync import u2_pool
from services.sync.u2_pool import U2Error

_log = logging.getLogger("sync")

_BOUNDS_RE = re.compile(r"-?\d+")


def ping(serial: str) -> dict:
    """Connectivity probe: triggers provisioning on first call."""
    dev = u2_pool.get(serial)
    try:
        w, h = dev.window_size()
        current = dev.app_current() or {}
    except Exception as exc:  # noqa: BLE001
        raise U2Error(f"u2 探测失败: {exc}") from exc
    return {
        "connected": True,
        "window_size": [int(w), int(h)],
        "current_app": {
            "package": current.get("package"),
            "activity": current.get("activity"),
        },
    }


def dump_hierarchy(serial: str) -> str:
    dev = u2_pool.get(serial)
    try:
        return dev.dump_hierarchy()
    except Exception as exc:  # noqa: BLE001
        raise U2Error(f"dump_hierarchy 失败: {exc}") from exc


def _parse_bounds(bounds: str) -> Optional[tuple[int, int, int, int]]:
    nums = _BOUNDS_RE.findall(bounds or "")
    if len(nums) < 4:
        return None
    left, top, right, bottom = (int(n) for n in nums[:4])
    return left, top, right, bottom


def _contains(b: tuple[int, int, int, int], x: float, y: float) -> bool:
    return b[0] <= x <= b[2] and b[1] <= y <= b[3]


def _xpath_for(node) -> Optional[str]:
    rid = node.get("resource-id")
    if rid:
        return f"//*[@resource-id='{rid}']"
    text = node.get("text")
    if text:
        return f"//*[@text='{text}']"
    desc = node.get("content-desc")
    if desc:
        return f"//*[@content-desc='{desc}']"
    return None


def _selector_from_node(node, bounds: tuple[int, int, int, int]) -> dict:
    return {
        "resourceId": node.get("resource-id") or None,
        "text": node.get("text") or None,
        "className": node.get("class") or None,
        "description": node.get("content-desc") or None,
        "clickable": node.get("clickable") == "true",
        "xpath": _xpath_for(node),
        "bounds": list(bounds),
    }


def find_node_by_point(xml: str, x: float, y: float) -> Optional[dict]:
    """Return the best-matching control under (x, y), or None.

    Picks the highest-scoring node whose bounds contain the point, preferring
    clickable nodes with a resource-id / text / content-desc, and among ties
    the smallest area (most specific). Mirrors the plan's scoring rule.
    """
    try:
        from lxml import etree
    except ImportError as exc:  # pragma: no cover
        raise U2Error("lxml 未安装") from exc
    try:
        root = etree.fromstring(xml.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise U2Error(f"解析 hierarchy 失败: {exc}") from exc

    candidates = []
    for node in root.iter("node"):
        b = _parse_bounds(node.get("bounds", ""))
        if b is None or not _contains(b, x, y):
            continue
        area = max(1, (b[2] - b[0]) * (b[3] - b[1]))
        score = 0
        if node.get("clickable") == "true":
            score += 100
        if node.get("resource-id"):
            score += 80
        if node.get("text"):
            score += 60
        if node.get("content-desc"):
            score += 40
        candidates.append((score, -area, node, b))

    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    _, _, node, bounds = candidates[0]
    return _selector_from_node(node, bounds)


def inspect_point(serial: str, x: float, y: float) -> dict:
    """Reverse-lookup the control under a master-screen pixel."""
    xml = dump_hierarchy(serial)
    selector = find_node_by_point(xml, x, y)
    return {"point": [int(x), int(y)], "selector": selector}
