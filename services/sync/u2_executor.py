"""uiautomator2 semantic action executor (Lab V2.3).

Given a selector produced by the semantic event builder, locate and click the
matching control on a *slave* device. Tries strategies in decreasing
reliability — resourceId → resourceId+text → text → description → xpath — and
reports which one worked so the dispatcher can log it and decide on fallback.

Returns a plain dict ``{success, strategy, error}`` and never raises: any
u2/connection failure becomes ``success=False`` so the caller can fall back to
coordinate injection.
"""

from __future__ import annotations

import logging

from config import config
from services.sync import u2_pool
from services.sync.u2_pool import U2Error

_log = logging.getLogger("sync")


def _ok(strategy: str) -> dict:
    return {"success": True, "strategy": strategy, "error": None}


def _fail(error: str) -> dict:
    return {"success": False, "strategy": None, "error": error}


def execute_click(slave_id: str, selector: dict) -> dict:
    """Find the control described by ``selector`` on ``slave_id`` and click it.

    Serialised per device: the u2 jsonrpc connection isn't concurrency-safe, so
    overlapping clicks to the same slave (rapid taps) queue on its lock.
    """
    if not selector:
        return _fail("no_selector")
    with u2_pool.device_lock(slave_id):
        return _execute_click_locked(slave_id, selector)


def _execute_click_locked(slave_id: str, selector: dict) -> dict:
    try:
        dev = u2_pool.get(slave_id)
    except U2Error as exc:
        return _fail(f"connect:{exc}")

    timeout = config.U2_WAIT_TIMEOUT
    rid = selector.get("resourceId")
    text = selector.get("text")
    desc = selector.get("description")
    xpath = selector.get("xpath")

    try:
        if rid:
            obj = dev(resourceId=rid)
            if obj.exists(timeout=timeout):
                obj.click()
                return _ok("resourceId")
        if rid and text:
            obj = dev(resourceId=rid, text=text)
            if obj.exists(timeout=timeout):
                obj.click()
                return _ok("resourceId_text")
        if text:
            obj = dev(text=text)
            if obj.exists(timeout=timeout):
                obj.click()
                return _ok("text")
        if desc:
            obj = dev(description=desc)
            if obj.exists(timeout=timeout):
                obj.click()
                return _ok("description")
        if xpath:
            xp = dev.xpath(xpath)
            if xp.exists:
                xp.click()
                return _ok("xpath")
    except (OSError, RuntimeError, ValueError, AttributeError) as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001 — u2 raises assorted types; never propagate
        return _fail(str(exc))

    return _fail("selector_not_found")


def execute_text(slave_id: str, text: str) -> dict:
    """Type ``text`` on ``slave_id`` via u2 (targets the focused field).

    Serialised per device. Returns the usual {success, strategy, error}; the
    caller falls back to scrcpy text injection on failure.
    """
    if not text:
        return _fail("empty_text")
    with u2_pool.device_lock(slave_id):
        try:
            dev = u2_pool.get(slave_id)
        except U2Error as exc:
            return _fail(f"connect:{exc}")
        try:
            dev.send_keys(text)
            return _ok("send_keys")
        except (OSError, RuntimeError, ValueError, AttributeError) as exc:
            return _fail(str(exc))
        except Exception as exc:  # noqa: BLE001
            return _fail(str(exc))
