"""Failure evidence capture + retention cleanup (Lab V2.4).

When a slave action fails *both* semantically and via coordinate fallback,
we snapshot the slave so the failure is debuggable after the fact:

    data/sync_failures/<group_id>/<YYYY-MM-DD>/<slave>_<ts>.png   # screenshot
                                              /<slave>_<ts>.xml   # UI hierarchy
                                              /<slave>_<ts>.json  # detail

These are discrete binary/text files with no rotation (unlike the sync log),
so they're the one place that genuinely needs an age-based cleanup — driven
here, throttled to roughly once an hour and triggered off capture, so no extra
thread or cross-module coupling is needed.

Everything is best-effort and defensive: capture failures never propagate.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime

from config import config
from services.sync import u2_pool

_log = logging.getLogger("sync")

_last_cleanup = 0.0
_CLEANUP_INTERVAL = 3600.0  # at most once per hour


def capture(group_id, master_id, slave_id, detail) -> dict | None:
    """Save screenshot + UI XML + JSON detail for a totally-failed slave action.

    Returns the written paths (or None if capture is disabled / failed). Never
    raises.
    """
    if not config.SYNC_FAILURE_CAPTURE:
        return None
    try:
        ts = int(time.time())
        day = datetime.now().strftime("%Y-%m-%d")
        out_dir = config.SYNC_FAILURE_DIR / str(group_id) / day
        out_dir.mkdir(parents=True, exist_ok=True)
        base = out_dir / f"{slave_id}_{ts}"
        png_path = str(base) + ".png"
        xml_path = str(base) + ".xml"
        json_path = str(base) + ".json"

        dev = None
        try:
            dev = u2_pool.get(slave_id)
        except Exception as exc:  # noqa: BLE001
            _log.debug("failure capture: u2 get %s failed: %s", slave_id, exc)

        saved_png = None
        saved_xml = None
        if dev is not None:
            try:
                dev.screenshot(png_path)
                saved_png = png_path
            except Exception as exc:  # noqa: BLE001
                _log.debug("failure capture: screenshot %s failed: %s", slave_id, exc)
            try:
                xml = dev.dump_hierarchy()
                with open(xml_path, "w", encoding="utf-8") as fh:
                    fh.write(xml)
                saved_xml = xml_path
            except Exception as exc:  # noqa: BLE001
                _log.debug("failure capture: dump %s failed: %s", slave_id, exc)

        record = {
            "group_id": group_id,
            "master_device_id": master_id,
            "slave_device_id": slave_id,
            "timestamp": ts,
            "detail": detail,
            "screenshot": saved_png,
            "xml": saved_xml,
        }
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=2)

        _maybe_cleanup()
        return {"screenshot": saved_png, "xml": saved_xml, "json": json_path}
    except OSError as exc:
        _log.warning("failure capture failed for %s: %s", slave_id, exc)
        return None


def _maybe_cleanup() -> None:
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < _CLEANUP_INTERVAL:
        return
    _last_cleanup = now
    try:
        cleanup(config.SYNC_FAILURE_RETENTION_DAYS)
    except OSError as exc:
        _log.debug("failure cleanup error: %s", exc)


def cleanup(retention_days: int) -> int:
    """Delete evidence files older than ``retention_days``. Returns count removed."""
    root = config.SYNC_FAILURE_DIR
    if not root.exists():
        return 0
    cutoff = time.time() - max(1, retention_days) * 86400
    removed = 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        for name in filenames:
            fp = os.path.join(dirpath, name)
            try:
                if os.path.getmtime(fp) < cutoff:
                    os.remove(fp)
                    removed += 1
            except OSError:
                pass
        # Prune now-empty date/group dirs.
        try:
            if dirpath != str(root) and not os.listdir(dirpath):
                os.rmdir(dirpath)
        except OSError:
            pass
    if removed:
        _log.info("sync failure cleanup removed %d old files", removed)
    return removed
