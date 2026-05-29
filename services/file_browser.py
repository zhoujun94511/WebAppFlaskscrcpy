"""Browse and manipulate the Android device filesystem via ADB.

Listing / stat go through ``adbutils.AdbDevice.sync``; structural
operations (mkdir / rm / mv) go through the shell because there is no
sync equivalent. We sanitise every path before shell concatenation so a
malicious or fat-fingered name can't break out of the intended command.

Returned dir-entry shape (matches what the frontend table renders)::

    {
      "name": "Download",
      "path": "/sdcard/Download",
      "is_dir": true,
      "is_link": false,
      "size": 4096,           # bytes (0 for dirs that don't report)
      "mtime": 1716700000,    # unix seconds, may be 0 if unknown
      "mode": 16877,          # st_mode-style permission bits
    }
"""

# cspell:ignore adbutils mkdir nlink androidx getprop

from __future__ import annotations

import io
import logging
import posixpath
import shlex
import stat as stat_lib
from typing import Any, Dict, Iterable, List, Optional, Tuple

from adbutils import adb

_log = logging.getLogger(__name__)


def _device(device_id: str):
    return adb.device(device_id)


def _normalize(path: str) -> str:
    """Collapse '…'/'.' and ensure the path is absolute + POSIX style."""
    if not path:
        return '/'
    # Force absolute. POSIX joins handle Windows-style separators gracefully
    # because adb shells are always POSIX.
    p = path.replace('\\', '/')
    if not p.startswith('/'):
        p = '/' + p
    norm = posixpath.normpath(p)
    return norm or '/'


def _shell(device_id: str, cmd: str, timeout: float = 8.0) -> Tuple[bool, str]:
    """Run an arbitrary shell command. Returns (ok, output_or_error)."""
    try:
        out = _device(device_id).shell(cmd, timeout=timeout)
        return True, out or ''
    except (RuntimeError, OSError, TimeoutError) as exc:
        _log.warning('[files] shell %r failed on %s: %s', cmd, device_id, exc)
        return False, str(exc)


def _list_root_via_shell(device_id: str) -> Optional[List[Dict[str, Any]]]:
    """Build the root listing using ``ls -1 /`` + per-entry sync.stat.

    Android's adb sync server (the protocol behind ``sync.list``) refuses
    to list the bare root: it returns EINVAL because ``/`` is the implicit
    base of all sync paths, not a regular directory entry. So for root we
    enumerate the names via shell and then stat each one individually —
    that gets us the real type / size / mtime per entry for the small
    handful of well-known Android top-level dirs (sdcard, system, data,
    vendor, etc.).
    """
    ok, out = _shell(device_id, 'ls -1 /')
    if not ok or not out:
        return None
    entries: List[Dict[str, Any]] = []
    sync = _device(device_id).sync
    for raw in out.splitlines():
        name = raw.strip()
        if not name or name.startswith('ls:'):
            # Skip blank lines and partial errors like "ls: ./xx: Permission denied".
            continue
        full = '/' + name
        try:
            info = sync.stat(full)
            mode = int(info.mode or 0) if info else 0
            entries.append({
                'name': name,
                'path': full,
                'is_dir': stat_lib.S_ISDIR(mode) if mode else True,
                'is_link': stat_lib.S_ISLNK(mode) if mode else False,
                'size': int(info.size or 0) if info else 0,
                'mtime': int(info.mtime.timestamp()) if info and info.mtime else 0,
                'mode': mode,
            })
        except (RuntimeError, OSError):
            # Many root entries (proc, sys, …) aren't statable via sync.
            # Assume directory so the user can at least click in.
            entries.append({
                'name': name, 'path': full,
                'is_dir': True, 'is_link': False,
                'size': 0, 'mtime': 0, 'mode': 0,
            })
    entries.sort(key=lambda e: (not e['is_dir'], e['name'].lower()))
    return entries


def list_directory(device_id: str, path: str) -> Dict[str, Any]:
    """List entries under ``path``. Returns {ok, path, parent, entries}.

    Hidden ("dotfiles") entries are included — the UI can filter them. We
    also stat the dir itself so the UI can disable navigation actions when
    the path doesn't exist or isn't readable.
    """
    target = _normalize(path)
    entries: List[Dict[str, Any]] = []

    # Root needs the shell-based fallback — see ``_list_root_via_shell``.
    if target == '/':
        rooted = _list_root_via_shell(device_id)
        if rooted is not None:
            return {'ok': True, 'path': '/', 'parent': '/', 'entries': rooted}
        # If even the shell ls fails (rare — adbd would be sick), fall
        # through to sync.list so the user sees a sensible error code.

    try:
        sync = _device(device_id).sync
        for f in sync.list(target):
            full = posixpath.join(target, f.path) if not f.path.startswith('/') else f.path
            entries.append({
                'name': posixpath.basename(f.path) or f.path,
                'path': full,
                'is_dir': stat_lib.S_ISDIR(f.mode),
                'is_link': stat_lib.S_ISLNK(f.mode),
                'size': int(f.size or 0),
                'mtime': int(f.mtime.timestamp()) if f.mtime else 0,
                'mode': int(f.mode or 0),
            })
    except (RuntimeError, OSError) as exc:
        # ``adbutils.sync.list`` raises on permission denied / non-existent.
        return {
            'ok': False,
            'path': target,
            'parent': posixpath.dirname(target) if target != '/' else '/',
            'error': str(exc),
            'entries': [],
        }

    entries.sort(key=lambda e: (not e['is_dir'], e['name'].lower()))
    return {
        'ok': True,
        'path': target,
        'parent': posixpath.dirname(target) if target != '/' else '/',
        'entries': entries,
    }


def stat_path(device_id: str, path: str) -> Optional[Dict[str, Any]]:
    """Stat a single path. Returns None if the path doesn't exist."""
    target = _normalize(path)
    try:
        info = _device(device_id).sync.stat(target)
    except (RuntimeError, OSError) as exc:
        _log.debug('[files] stat %s on %s failed: %s', target, device_id, exc)
        return None
    if not info or not info.mode:
        return None
    return {
        'name': posixpath.basename(target) or target,
        'path': target,
        'is_dir': stat_lib.S_ISDIR(info.mode),
        'is_link': stat_lib.S_ISLNK(info.mode),
        'size': int(info.size or 0),
        'mtime': int(info.mtime.timestamp()) if info.mtime else 0,
        'mode': int(info.mode or 0),
    }


def mkdir(device_id: str, path: str) -> Tuple[bool, str]:
    target = _normalize(path)
    if target == '/':
        return False, 'cannot create root'
    ok, out = _shell(device_id, f'mkdir -p {shlex.quote(target)} && echo OK')
    if not ok or 'OK' not in out:
        return False, out.strip() or 'mkdir failed'
    return True, target


def rename(device_id: str, src: str, dst: str) -> Tuple[bool, str]:
    src_n = _normalize(src)
    dst_n = _normalize(dst)
    if src_n == '/' or dst_n == '/':
        return False, 'cannot rename root'
    ok, out = _shell(device_id, f'mv {shlex.quote(src_n)} {shlex.quote(dst_n)} && echo OK')
    if not ok or 'OK' not in out:
        return False, out.strip() or 'rename failed'
    return True, dst_n


def delete(device_id: str, path: str, recursive: bool = False) -> Tuple[bool, str]:
    target = _normalize(path)
    # Hard refuse a few well-known dangerous roots.
    if target in ('/', '/system', '/vendor', '/sdcard', '/data', '/storage'):
        return False, f'refusing to delete protected path {target}'
    flag = '-rf' if recursive else '-f'
    ok, out = _shell(device_id, f'rm {flag} {shlex.quote(target)} && echo OK')
    if not ok or 'OK' not in out:
        return False, out.strip() or 'delete failed'
    return True, target


def pull_to_buffer(device_id: str, path: str, max_bytes: int = 256 * 1024 * 1024) -> Optional[bytes]:
    """Pull a remote file into memory. Returns None on failure / too large.

    256 MiB cap protects the server from accidentally streaming a movie
    into RAM. Larger downloads should stream via :func:`pull_iter`.
    """
    target = _normalize(path)
    info = stat_path(device_id, target)
    if info is None:
        return None
    if info['is_dir']:
        return None
    if info['size'] > max_bytes:
        _log.warning('[files] refusing to buffer %s (%d bytes > %d)', target, info['size'], max_bytes)
        return None
    try:
        return _device(device_id).sync.read_bytes(target)
    except (RuntimeError, OSError) as exc:
        _log.warning('[files] pull %s failed: %s', target, exc)
        return None


def pull_iter(device_id: str, path: str, _chunk: int = 64 * 1024) -> Iterable[bytes]:
    """Stream a remote file in chunks — for large downloads via the HTTP layer."""
    target = _normalize(path)
    info = stat_path(device_id, target)
    if info is None or info['is_dir']:
        return
    try:
        for buf in _device(device_id).sync.iter_content(target):
            # adbutils yields raw bytes already; pass through.
            yield buf
    except (RuntimeError, OSError) as exc:
        _log.warning('[files] pull_iter %s failed: %s', target, exc)


def push_bytes(device_id: str, remote_path: str, data: bytes) -> Tuple[bool, str]:
    """Push the given bytes to ``remote_path`` (file path, not directory)."""
    target = _normalize(remote_path)
    if target == '/' or target.endswith('/'):
        return False, 'remote path must be a file, not directory'
    try:
        _device(device_id).sync.push(io.BytesIO(data), target)
        return True, target
    except (RuntimeError, OSError) as exc:
        _log.warning('[files] push %s failed: %s', target, exc)
        return False, str(exc)
