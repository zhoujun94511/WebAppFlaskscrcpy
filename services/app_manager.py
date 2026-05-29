"""Manage installed packages on the device via adb shell + sync.

We avoid heavyweight dependencies (no aapt, no androguard) by reading
what ``pm list packages -3 -f`` gives us — the per-package APK path on
the device — and then either pulling the APK bytes (for export / backup)
or running ``pm install/uninstall`` for lifecycle ops. Label + icon
extraction is intentionally NOT implemented here: aapt-style parsing
balloons the runtime dependency footprint and most users only need the
package name + APK path for the actions they care about (uninstall /
export / push to another device).

Returned package shape::

    {
      "package": "com.example.app",
      "apk_path": "/data/app/.../base.apk",
      "system": false,
      "version_name": "1.2.3",   # may be empty if dumpsys denies
      "version_code": 123,       # may be 0
      "first_install": 0,        # unix ms, may be 0
      "last_update": 0,          # unix ms, may be 0
      "size": 12345678,          # APK size in bytes, may be 0
    }
"""

# cspell:ignore adbutils dumpsys versionName versionCode firstInstallTime lastUpdateTime installer pkg

from __future__ import annotations

import logging
import re
import shlex
from typing import Any, Dict, List, Optional, Tuple

from adbutils import adb

_log = logging.getLogger(__name__)


def _device(device_id: str):
    return adb.device(device_id)


def _shell(device_id: str, cmd: str, timeout: float = 12.0) -> Tuple[bool, str]:
    try:
        return True, _device(device_id).shell(cmd, timeout=timeout) or ''
    except (RuntimeError, OSError, TimeoutError) as exc:
        _log.warning('[apps] shell %r failed on %s: %s', cmd, device_id, exc)
        return False, str(exc)


# ``pm list packages -f -3 [-s]`` outputs lines like:
#   package:/data/app/~~xxx==/com.foo-yyy==/base.apk=com.foo
# Naive non-greedy regex on '=' falls over because the path contains base64
# hashes with '==' padding. We split on the FINAL '=' instead (package names
# are valid Java identifiers — they can't contain '=').


def _parse_pkg_list(out: str) -> List[Tuple[str, str]]:
    """Return list of (apk_path, package_name)."""
    result: List[Tuple[str, str]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith('package:'):
            continue
        body = line[len('package:'):]
        # Last '=' separates path from package name.
        idx = body.rfind('=')
        if idx <= 0:
            continue
        apk_path = body[:idx].strip()
        pkg = body[idx + 1:].strip()
        if apk_path and pkg:
            result.append((apk_path, pkg))
    return result


def _stat_size(device_id: str, apk_path: str) -> int:
    """Stat the APK file via sync — cheap, doesn't need a shell call."""
    try:
        info = _device(device_id).sync.stat(apk_path)
        return int(info.size or 0) if info else 0
    except (RuntimeError, OSError):
        return 0


def list_packages(device_id: str, scope: str = 'all') -> Dict[str, Any]:
    """List installed packages. ``scope`` ∈ {'all','third_party','system'}."""
    flag_third = '-3' if scope == 'third_party' else ''
    flag_sys = '-s' if scope == 'system' else ''
    flag = flag_third or flag_sys
    cmd = f'pm list packages -f {flag}'.strip()
    ok, out = _shell(device_id, cmd)
    if not ok:
        return {'ok': False, 'error': out, 'packages': []}

    # Cross-reference with a second pass to flag system vs third-party.
    sys_set = set()
    if scope == 'all':
        ok2, out2 = _shell(device_id, 'pm list packages -f -s')
        if ok2:
            sys_set = {pkg for _, pkg in _parse_pkg_list(out2)}

    pkgs: List[Dict[str, Any]] = []
    for apk_path, pkg in _parse_pkg_list(out):
        pkgs.append({
            'package': pkg,
            'apk_path': apk_path,
            'system': (scope == 'system') or (pkg in sys_set),
            'size': _stat_size(device_id, apk_path),
        })
    pkgs.sort(key=lambda p: (p['system'], p['package'].lower()))
    return {'ok': True, 'packages': pkgs, 'count': len(pkgs)}


_VERSION_NAME_RE = re.compile(r'^\s*versionName=(.+)$', re.MULTILINE)
_VERSION_CODE_RE = re.compile(r'^\s*versionCode=(\d+)', re.MULTILINE)
_FIRST_INSTALL_RE = re.compile(r'^\s*firstInstallTime=(.+)$', re.MULTILINE)
_LAST_UPDATE_RE = re.compile(r'^\s*lastUpdateTime=(.+)$', re.MULTILINE)


def package_info(device_id: str, package: str) -> Optional[Dict[str, Any]]:
    """Per-package detail via ``dumpsys package``. May be partial."""
    if not package or '/' in package or ' ' in package:
        return None
    ok, out = _shell(device_id, f'dumpsys package {shlex.quote(package)}')
    if not ok or not out:
        return None
    info: Dict[str, Any] = {'package': package}
    if m := _VERSION_NAME_RE.search(out):
        info['version_name'] = m.group(1).strip()
    if m := _VERSION_CODE_RE.search(out):
        try: info['version_code'] = int(m.group(1))
        except ValueError: pass
    if m := _FIRST_INSTALL_RE.search(out):
        info['first_install'] = m.group(1).strip()
    if m := _LAST_UPDATE_RE.search(out):
        info['last_update'] = m.group(1).strip()
    return info


def uninstall(device_id: str, package: str, keep_data: bool = False) -> Tuple[bool, str]:
    if not package:
        return False, 'missing package'
    flag = '-k ' if keep_data else ''
    ok, out = _shell(device_id, f'pm uninstall {flag}{shlex.quote(package)}')
    success = ok and 'Success' in out
    return success, (out.strip() or ('uninstalled' if success else 'uninstall failed'))


# Regex for the PM "signature mismatch" error. The literal output is:
#
#   Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: Existing package
#   com.foo.bar signatures do not match newer version; ignoring!]
#
# Android refuses to update an APK if the signing key differs from the
# already-installed copy. The ONLY non-root workaround is uninstalled
# first (which loses the app's data). We extract the package name so the
# UI can ask the user for explicit consent before doing that.
_SIGNATURE_MISMATCH_RE = re.compile(
    r'INSTALL_FAILED_UPDATE_INCOMPATIBLE.*?Existing package\s+([A-Za-z_][A-Za-z0-9_.]*)\s+signatures',
    re.IGNORECASE | re.DOTALL,
)


def detect_signature_mismatch(pm_output: str) -> Optional[str]:
    """Return the offending package name if ``pm_output`` contains the
    signature-mismatch failure; ``None`` otherwise.

    Lifted out of ``install_from_local`` so the API layer can also call
    this on the raw message after a failed install, without re-running
    the installation just to reparse.
    """
    if not pm_output:
        return None
    m = _SIGNATURE_MISMATCH_RE.search(pm_output)
    return m.group(1) if m else None


def install_from_local(device_id: str, local_apk_path: str,
                       grant_permissions: bool = False,
                       reinstall: bool = True,
                       force_replace: bool = False) -> Tuple[bool, str]:
    """Install an APK that's already staged in the dev server's filesystem.

    We deliberately do NOT call ``adbutils.AdbDevice.install()`` — it
    pulls in ``apkutils`` to parse the APK manifest just to discover the
    package name and optional launcher activity for auto-launch. We pass
    ``nolaunch=True`` so the manifest peek is *useless* to us, yet it's
    the path that keeps breaking:

        * apkutils missing → TypeError: list + None inside the pm wrapper
        * apkutils installed (v2.0.5) → AttributeError: 'APK' has no
          attribute 'get_main_activities' — adbutils targets an older API

    Both surface as 500s on /apps/install. Cut the dependency entirely
    by doing the two real steps ourselves with adbutils' low-level
    ``sync.push`` + ``shell``:

        1. Push the local file to ``/data/local/tmp/{filename}.apk``
        2. Run ``pm install <flags> {remote_path}``
        3. Best-effort delete the staging file

    ``pm install`` exits 0 on success and writes ``Success`` to stdout,
    or exits non-zero with a ``Failure [REASON]`` line — we parse for
    the human-readable PM error and surface it as the message.
    """
    # ``-i com.android.shell`` declares the installer-package to PM as
    # the system shell. On stock Android this is just metadata, but on
    # OEM ROMs that gate adb-side installs behind a confirmation prompt
    # (MIUI / HyperOS / ColorOS / Magic UI / Funtouch — all of Xiaomi /
    # OPPO / vivo / Honor add this layer), the gate often inspects the
    # caller's installer hint and SKIPS the prompt when it's
    # ``com.android.shell``. Without this hint, the user has to walk
    # over to the physical phone and tap "Allow" — and the dialog
    # itself is rendered with FLAG_SECURE so it appears as a BLACK
    # OVERLAY on the scrcpy stream, making the web UI look frozen
    # until they tap it on the hardware. Harmless on devices that
    # don't gate (the hint is just stored in PackageManager and
    # surfaces in ``dumpsys package``); useful where it matters.
    flags = ['-i', 'com.android.shell', '-r', '-t']
    if not reinstall:
        flags.remove('-r')
    if grant_permissions:
        flags.append('-g')

    # Build a remote path that's unique-ish so concurrent installations from
    # different sessions don't stomp on each other.
    import os as _os
    import time as _time
    base = _os.path.basename(local_apk_path) or 'app.apk'
    remote = f"/data/local/tmp/{int(_time.time() * 1000)}-{base}"
    if not remote.lower().endswith('.apk'):
        remote += '.apk'

    dev = _device(device_id)
    try:
        # 1. Push. adbutils' sync.push handles chunking + progress; on
        # failure it raises adbutils.AdbError (RuntimeError subclass).
        dev.sync.push(local_apk_path, remote)
    except (RuntimeError, OSError) as exc:
        return False, f'push failed: {exc}'

    def _do_install() -> Tuple[bool, str]:
        # 2. Install. Quote the remote path defensively — it has a
        # timestamp prefix so it's ASCII, but shlex.quote keeps this
        # future-proof if someone changes the naming.
        cmd = f"pm install {' '.join(flags)} {shlex.quote(remote)}"
        # Use distinct local names so the linter doesn't flag the
        # outer ``(ok, msg)`` tuple as shadowed across scopes.
        shell_ok, shell_out = _shell(device_id, cmd, timeout=120.0)
        if not shell_ok:
            return False, f'pm install error: {shell_out}'
        if 'Success' in shell_out:
            return True, 'installed'
        # Pull the parenthesised reason if present, e.g.
        # ``Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE]``.
        last_line = shell_out.strip().splitlines()[-1] if shell_out else 'install failed'
        return False, last_line

    try:
        ok, msg = _do_install()
        if ok:
            return True, msg

        # Signature-mismatch recovery path. Android's PM service refuses
        # to update an APK if its signing key differs from the version
        # already on the device — no flag combination on ``pm install``
        # changes that. The only non-root remedy is to uninstall the
        # existing package first, which loses its data. Surface this
        # back to the caller WITHOUT forcing the destructive step unless
        # they explicitly opt in (``force_replace=True``); the API layer
        # turns ``force_replace`` on after the user confirms.
        offending_pkg = detect_signature_mismatch(msg)
        if offending_pkg and force_replace:
            _log.info(
                '[apps] signature mismatch on %s for %s; uninstalling and retrying',
                device_id, offending_pkg,
            )
            uninstall_ok, uninstall_msg = uninstall(device_id, offending_pkg, keep_data=False)
            if not uninstall_ok:
                return False, (
                    f'signature mismatch; uninstall of {offending_pkg} failed: {uninstall_msg}'
                )
            # Retry the installation with the same flags AND the staged file
            # still in place (the ``finally`` cleanup hasn't run yet).
            return _do_install()
        # Not signature-mismatch, OR caller hasn't opted into the
        # destructive path — return the raw PM message unchanged. The
        # API layer parses it for the signature-mismatch shape and
        # prompts the user accordingly.
        return False, msg
    except (RuntimeError, OSError, TimeoutError) as exc:
        return False, f'install error: {exc}'
    finally:
        # 3. Best-effort cleanup. ``_shell`` already swallows runtime /
        # OS / timeout errors and returns ``(False, msg)`` rather than
        # raising — so no extra try/except needed here. Failure to
        # delete the staging file is non-fatal (it just lingers under
        # /data/local/tmp and gets cleared on device reboot).
        _shell(device_id, f"rm -f {shlex.quote(remote)}", timeout=5.0)


def pull_apk(device_id: str, package: str) -> Optional[Tuple[bytes, str]]:
    """Return (bytes, base_filename) for the package's APK, or None."""
    if not package or '/' in package or ' ' in package:
        return None
    ok, out = _shell(device_id, f'pm path {shlex.quote(package)}')
    if not ok or 'package:' not in out:
        return None
    # ``pm path`` may return multiple lines (base + split APKs). We pull the
    # primary "base.apk" path on the first line; split installations need their
    # own handling (out of scope for the basic export).
    first = out.strip().splitlines()[0]
    apk_path = first.split('package:', 1)[1].strip()
    try:
        data = _device(device_id).sync.read_bytes(apk_path)
    except (RuntimeError, OSError) as exc:
        _log.warning('[apps] pull_apk %s failed: %s', package, exc)
        return None
    filename = f'{package}.apk'
    return data, filename
