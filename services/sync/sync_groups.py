"""In-memory sync-group registry.

A *sync group* binds one **master** device to N **slave** devices owned by
the same user. While a group is enabled, input the user performs on the
master is mirrored to every slave (see :mod:`services.sync.sync_dispatcher`).

State is deliberately kept in memory only (a module-level singleton guarded
by a lock) — no SQLite tables. This keeps the experimental feature fully
decoupled from the main schema; the trade-off is that groups don't survive
a process restart, which is fine for a lab feature.

Invariants enforced here:

* one group per master device;
* a device belongs to at most one group at a time (as master OR slave);
* the master is never also a slave of its own group.

Ownership / reservation checks live in :mod:`api.sync` — this module is
pure bookkeeping so it stays trivially unit-testable.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional


class SyncGroupError(Exception):
    """Raised on an invalid group operation (conflict, missing group, …)."""


@dataclass
class SyncGroup:
    id: str
    owner_user_id: int
    master_device_id: str
    slave_device_ids: list[str] = field(default_factory=list)
    enabled: bool = True
    # "coordinate" = V1 ratio-coordinate mirroring; "semantic" = semantic-first
    # (u2 control click with coordinate fallback). Default keeps V1 behaviour.
    mode: str = "coordinate"
    sync_touch: bool = True
    sync_keyevent: bool = True
    sync_text: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    def member_ids(self) -> list[str]:
        """Master + all slaves (every device the group touches)."""
        return [self.master_device_id, *self.slave_device_ids]


_groups: dict[str, SyncGroup] = {}
_lock = threading.RLock()


def _find_group_for_device(device_id: str) -> Optional[SyncGroup]:
    """Return the group this device participates in (master or slave), or None."""
    for group in _groups.values():
        if device_id == group.master_device_id or device_id in group.slave_device_ids:
            return group
    return None


def create_group(
    owner_user_id: int,
    master_device_id: str,
    slave_device_ids: list[str],
    *,
    mode: str = "coordinate",
    sync_touch: bool = True,
    sync_keyevent: bool = True,
    sync_text: bool = True,
) -> SyncGroup:
    """Register a new sync group. Raises SyncGroupError on a conflict."""
    master_device_id = (master_device_id or "").strip()
    if not master_device_id:
        raise SyncGroupError("缺少主机设备")
    # De-dup, drop the master if it slipped into the slave list, drop blanks.
    slaves: list[str] = []
    for sid in slave_device_ids or []:
        sid = (sid or "").strip()
        if sid and sid != master_device_id and sid not in slaves:
            slaves.append(sid)
    if not slaves:
        raise SyncGroupError("至少需要一台从机")

    with _lock:
        for dev in (master_device_id, *slaves):
            existing = _find_group_for_device(dev)
            if existing is not None:
                raise SyncGroupError(f"设备 {dev} 已在另一个同步组中")
        group = SyncGroup(
            id=uuid.uuid4().hex[:12],
            owner_user_id=owner_user_id,
            master_device_id=master_device_id,
            slave_device_ids=slaves,
            mode="semantic" if mode == "semantic" else "coordinate",
            sync_touch=sync_touch,
            sync_keyevent=sync_keyevent,
            sync_text=sync_text,
        )
        _groups[group.id] = group
        return group


def delete_group(group_id: str) -> Optional[SyncGroup]:
    """Remove a group and return it (or None if it was already gone)."""
    with _lock:
        return _groups.pop(group_id, None)


def get(group_id: str) -> Optional[SyncGroup]:
    with _lock:
        return _groups.get(group_id)


def get_by_master(master_device_id: str) -> Optional[SyncGroup]:
    """The ENABLED group whose master is this device — the dispatch hot path.

    Returns None for disabled groups so the fan-out is skipped cheaply.
    """
    with _lock:
        for group in _groups.values():
            if group.master_device_id == master_device_id and group.enabled:
                return group
    return None


def is_slave(device_id: str) -> bool:
    with _lock:
        for group in _groups.values():
            if device_id in group.slave_device_ids:
                return True
    return False


def list_groups(owner_user_id: Optional[int] = None) -> list[SyncGroup]:
    with _lock:
        groups = list(_groups.values())
    if owner_user_id is None:
        return groups
    return [g for g in groups if g.owner_user_id == owner_user_id]


def set_enabled(group_id: str, enabled: bool) -> Optional[SyncGroup]:
    with _lock:
        group = _groups.get(group_id)
        if group is None:
            return None
        group.enabled = enabled
        return group


def groups_for_device(device_id: str) -> list[SyncGroup]:
    """Every group this device participates in (master or slave).

    Used by the lifecycle hooks: a released/offline device must be removed
    from any group it's part of.
    """
    with _lock:
        return [
            g
            for g in _groups.values()
            if device_id == g.master_device_id or device_id in g.slave_device_ids
        ]


def remove_device(device_id: str) -> list[SyncGroup]:
    """Detach a device from all groups; delete groups whose master is gone.

    Returns the groups that were *deleted* (master removed) so callers can
    release their remaining members. A slave removal just shrinks the group
    (and disables it if no slaves remain).
    """
    deleted: list[SyncGroup] = []
    with _lock:
        for group in list(_groups.values()):
            if group.master_device_id == device_id:
                _groups.pop(group.id, None)
                deleted.append(group)
            elif device_id in group.slave_device_ids:
                group.slave_device_ids.remove(device_id)
                if not group.slave_device_ids:
                    group.enabled = False
    return deleted
