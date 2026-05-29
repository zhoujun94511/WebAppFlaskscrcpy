"""Chunked file upload over the WebRTC ``adb`` DataChannel.

State machine
-------------
``file.push``   → register transfer, allocate temp file        → ``file.ready``
``file.chunk``  → append base64-decoded bytes                  → ``file.progress``
``file.end``    → close temp, install (.apk) or push to sdcard → ``file.done``
``file.cancel`` → discard temp                                 → ``file.cancelled``

The registry is process-global because the producer (DataChannel
message handler) and the finaliser (``file.end``) may run on different
threads.
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

_log = logging.getLogger(__name__)

_STAGING_DIR = Path(tempfile.gettempdir()) / 'webapp-flaskscrcpy-uploads'

# A callable injected by the route layer: ``reply(dict)`` ships a JSON
# message back over the same DataChannel.
ReplyFn = Callable[[Dict[str, Any]], None]


class FileTransfer:
    """One in-flight transfer. Not thread-safe by itself; wrap with the registry lock."""

    def __init__(self, transfer_id: str, name: str, size: int, remote: str) -> None:
        self.id = transfer_id
        self.name = os.path.basename(name)
        self.size = int(size)
        self.remote = remote or '/sdcard/Download/'
        self.received = 0
        self.seq = 0
        suffix = Path(self.name).suffix
        _STAGING_DIR.mkdir(parents=True, exist_ok=True)
        self._tmp = tempfile.NamedTemporaryFile(
            delete=False, dir=_STAGING_DIR, suffix=suffix, mode='wb'
        )

    @property
    def local_path(self) -> str:
        return self._tmp.name

    def write_chunk(self, seq: int, data: bytes) -> None:
        if seq != self.seq:
            raise ValueError(f'out-of-order chunk seq={seq} expected={self.seq}')
        self._tmp.write(data)
        self._tmp.flush()
        self.received += len(data)
        self.seq += 1

    def close(self) -> None:
        try:
            self._tmp.close()
        except OSError:
            pass

    def unlink(self) -> None:
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass


_transfers: Dict[str, FileTransfer] = {}
_transfers_lock = threading.Lock()


def handle_push(evt: Dict[str, Any], reply: ReplyFn) -> None:
    transfer_id = evt.get('id')
    name = evt.get('name')
    if not transfer_id or not name:
        reply({'t': 'file.error', 'id': transfer_id, 'error': 'missing id/name'})
        return
    with _transfers_lock:
        if transfer_id in _transfers:
            reply({'t': 'file.error', 'id': transfer_id, 'error': 'duplicate id'})
            return
        _transfers[transfer_id] = FileTransfer(
            transfer_id=transfer_id,
            name=name,
            size=int(evt.get('size', 0)),
            remote=evt.get('remote', '/sdcard/Download/'),
        )
    reply({'t': 'file.ready', 'id': transfer_id})


def handle_chunk(evt: Dict[str, Any], reply: ReplyFn) -> None:
    transfer_id = evt.get('id')
    seq = int(evt.get('seq', -1))
    data_b64 = evt.get('data', '')
    with _transfers_lock:
        transfer = _transfers.get(transfer_id) if transfer_id else None
    if transfer is None:
        reply({'t': 'file.error', 'id': transfer_id, 'error': 'unknown transfer'})
        return
    try:
        data = base64.b64decode(data_b64, validate=False)
        transfer.write_chunk(seq, data)
    except (ValueError, OSError) as exc:
        reply({'t': 'file.error', 'id': transfer_id, 'error': str(exc)})
        return
    reply({
        't': 'file.progress',
        'id': transfer_id,
        'received': transfer.received,
        'total': transfer.size,
    })


def handle_end(evt: Dict[str, Any], client: Any, reply: ReplyFn) -> None:
    transfer_id = evt.get('id')
    with _transfers_lock:
        transfer = _transfers.pop(transfer_id, None) if transfer_id else None
    if transfer is None:
        reply({'t': 'file.error', 'id': transfer_id, 'error': 'unknown transfer'})
        return
    transfer.close()
    # ``evt`` (the originating ``file.end`` payload) may carry ``force=1``
    # — the frontend sets it after the user confirmed an
    # INSTALL_FAILED_UPDATE_INCOMPATIBLE recovery dialog. The first
    # attempt always runs with force=False so we can surface the
    # structured signature-mismatch error to the UI.
    force_replace = bool(evt.get('force'))
    try:
        suffix = Path(transfer.name).suffix.lower()
        if suffix == '.apk':
            # Route through ``app_manager.install_from_local`` rather
            # than ``client.device.install`` (adbutils) so we share the
            # same signature-mismatch detection used by /api/upload and
            # /apps/install. Extract the device serial from the live
            # scrcpy client; this is the only place we need it inside
            # the DataChannel transfer pipeline.
            from . import app_manager  # local import to avoid cycle
            device_id = getattr(getattr(client, 'device', None), 'serial', None) or ''
            ok, msg = app_manager.install_from_local(
                device_id,
                transfer.local_path,
                grant_permissions=False,
                force_replace=force_replace,
            )
            if ok:
                reply({
                    't': 'file.done',
                    'id': transfer_id,
                    'action': 'install',
                    'filename': transfer.name,
                })
                return
            offending_pkg = app_manager.detect_signature_mismatch(msg)
            if offending_pkg and not force_replace:
                # Structured signature-mismatch reply — frontend pops
                # the confirmation dialog and re-sends ``file.end`` with
                # ``force: true`` (transfer must be re-uploaded since
                # we already unlinked the staged file in ``finally``).
                reply({
                    't': 'file.error',
                    'id': transfer_id,
                    'error': msg,
                    'error_code': 'signature_mismatch',
                    'existing_package': offending_pkg,
                })
                return
            reply({'t': 'file.error', 'id': transfer_id, 'error': msg})
        else:
            remote = transfer.remote
            if remote.endswith('/'):
                remote = remote + transfer.name
            client.device.push(transfer.local_path, remote)
            reply({
                't': 'file.done',
                'id': transfer_id,
                'action': 'push',
                'filename': transfer.name,
                'remote_path': remote,
            })
    except (OSError, RuntimeError, ValueError) as exc:
        reply({'t': 'file.error', 'id': transfer_id, 'error': str(exc)})
    finally:
        transfer.unlink()


def handle_cancel(evt: Dict[str, Any], reply: ReplyFn) -> None:
    transfer_id = evt.get('id')
    with _transfers_lock:
        transfer = _transfers.pop(transfer_id, None) if transfer_id else None
    if transfer is not None:
        transfer.close()
        transfer.unlink()
    reply({'t': 'file.cancelled', 'id': transfer_id})


def active_transfer(transfer_id: str) -> Optional[FileTransfer]:
    """Test/diagnostic helper."""
    return _transfers.get(transfer_id)
