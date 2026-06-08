"""Sync-group lifecycle reactions to device release / teardown.

Hooked (defensively) from :func:`services.reservations._teardown`, which is
the single chokepoint every release path funnels through (owner cancel,
admin force-release, expiry sweep). Keeping the hook there means we react to
all of them without wiring into each call site.

Rules:

* master gone  → the whole group is dissolved; its slaves are released so
  they don't stay stuck reserved under the multi-device opt-in.
* slave gone   → it's removed from the group (group auto-disables if it was
  the last slave); nothing else to release (the slave is already being
  torn down).
"""

from __future__ import annotations

import logging

from services.sync import sync_groups

_log = logging.getLogger("sync")


def on_device_gone(device_id: str) -> None:
    """React to a device being released/expired/offline. Never raises."""
    try:
        deleted = sync_groups.remove_device(device_id)
    except (KeyError, ValueError, RuntimeError, OSError) as exc:
        _log.warning("sync remove_device(%s) failed: %s", device_id, exc)
        return

    if not deleted:
        return

    # A master disappeared → release the now-orphaned slaves. Import inside
    # the function to avoid a circular import (reservations → sync → reservations).
    try:
        from services import reservations
    except ImportError:
        return
    for group in deleted:
        _log.info("Sync group %s dissolved (master %s gone)", group.id, device_id)
        for slave_id in group.slave_device_ids:
            try:
                reservations.release(slave_id, reason="sync_master_gone")
            except (KeyError, ValueError, RuntimeError, OSError, AttributeError) as exc:
                _log.warning("release slave %s failed: %s", slave_id, exc)
