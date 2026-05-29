"""File upload service: stage to a temp dir, then install (APK) or push.

Used by ``POST /api/upload``. The route layer hands us a Werkzeug
``FileStorage`` (or anything with ``.save(fp)`` + ``.filename``) and a
device id; we own the temp-file lifecycle and dispatch.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import scrcpy.scrcpyclients as scrcpy_clients

_log = logging.getLogger(__name__)

# Local staging directory for uploaded files. Each request creates a
# uniquely-named tempfile here that is unlinked once the installation/push
# completes (success or failure).
STAGING_DIR = Path(r'D:\tmp')


@dataclass
class UploadResult:
    action: str           # "install" | "push"
    filename: str
    remote_path: str | None = None
    # On a failed APK install, populated with the PM output. Lets the
    # API layer extract structured error info (e.g. signature mismatch
    # → which existing package is in the way) without re-running.
    install_ok: bool = True
    install_message: str = ''


class InstallSignatureMismatch(RuntimeError):
    """Raised by ``stage_and_dispatch`` when ``pm install`` fails with
    INSTALL_FAILED_UPDATE_INCOMPATIBLE — the device has the same package
    installed under a different signing key. Only non-root fix is to
    uninstall first, which loses app data, so we surface this as a
    distinct exception type and let the caller (the route layer) decide
    whether to prompt the user or auto-recover (when ``force=True`` was
    passed on the original request).

    ``existing_package`` is the package name extracted from the PM
    output, so the UI can name it in the confirmation dialog.
    """

    def __init__(self, existing_package: str, pm_message: str) -> None:
        super().__init__(pm_message)
        self.existing_package = existing_package
        self.pm_message = pm_message


def stage_and_dispatch(device_id: str, file_obj: Any, *, force_replace: bool = False) -> UploadResult:
    """Save ``file_obj`` to disk and dispatch by extension.

    Raises ``ValueError`` for missing args, ``RuntimeError`` if the
    device's scrcpy client isn't ready, ``InstallSignatureMismatch`` for
    the well-known PM signature-mismatch path (so the route layer can
    surface a structured error to the UI), and any other underlying adb
    error bubbles up unchanged.

    ``force_replace`` — opt-in destructive recovery: on signature
    mismatch, uninstall the existing package first then retry. Disabled
    by default; the route layer flips it on after the user confirms.
    """
    if not device_id:
        raise ValueError('missing device_id')
    filename = getattr(file_obj, 'filename', None)
    if file_obj is None or not filename:
        raise ValueError('missing file')

    client = scrcpy_clients.get_client(device_id)
    if not client or not client.alive:
        raise RuntimeError('device not ready')

    safe_name = os.path.basename(filename)
    suffix = Path(safe_name).suffix.lower()
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        delete=False,
        dir=STAGING_DIR,
        suffix=Path(safe_name).suffix,
        mode='wb',
    ) as tmp:
        file_obj.save(tmp)
        local_path = tmp.name

    try:
        if suffix == '.apk':
            # Use our own ``install_from_local`` rather than
            # ``client.device.install`` (adbutils). adbutils.install
            # pulls in apkutils to parse the manifest and has been a
            # source of cryptic 500s (apkutils version drift). Our
            # version also returns the raw PM message which lets us
            # detect signature-mismatch deterministically.
            from . import app_manager  # local import — avoid cycle at module load
            ok, msg = app_manager.install_from_local(
                device_id, local_path,
                grant_permissions=False,
                force_replace=force_replace,
            )
            if ok:
                return UploadResult(action='install', filename=safe_name,
                                    install_ok=True, install_message=msg)
            offending_pkg = app_manager.detect_signature_mismatch(msg)
            if offending_pkg and not force_replace:
                raise InstallSignatureMismatch(offending_pkg, msg)
            # Generic install failure (insufficient storage, parse
            # error, etc.) — surface as OSError so the route returns 500.
            raise OSError(msg)

        remote_path = f'/sdcard/Download/{safe_name}'
        client.device.push(local_path, remote_path)
        return UploadResult(action='push', filename=safe_name, remote_path=remote_path)
    finally:
        try:
            os.remove(local_path)
        except OSError as exc:
            _log.debug('failed to clean staged upload %s: %s', local_path, exc)
