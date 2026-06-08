"""Experimental master→slave multi-device synchronisation (lab feature).

This package is a *pluggable, decoupled* add-on: when the ``ENABLE_SYNC``
environment variable is off it is never imported into the hot paths, and
even when on, the single injection point in
:func:`services.scrcpy_input.dispatch` is wrapped so any failure here
degrades to "no sync" rather than disturbing the master device's own
control path.

Design constraints (see the plan file):

* Group state lives **in memory** (:mod:`services.sync.sync_groups`) — no
  new DB tables, no schema migration. A process restart drops all groups,
  which is acceptable for an experimental feature.
* Execution/diagnostic output goes to a **log file**
  (``logs/sync.log``) via :mod:`services.sync.sync_dispatcher`, never to
  the database.
"""

from config import config


def is_enabled() -> bool:
    """True when the sync lab feature is switched on.

    Single source of truth is :data:`config.config.ENABLE_SYNC` — both the
    control-plane registration (app.py) and the data-plane hooks (input
    fan-out, reservation lifecycle) consult this, so there's exactly one
    flag to flip in ``config/config.py``.
    """
    return config.ENABLE_SYNC
