"""Stream ``adb logcat`` output to the browser over Server-Sent Events.

Each connected browser tab gets its OWN logcat subprocess — no buffer
sharing across tabs, no funny multiplexing. The lifecycle is bounded by
the HTTP response stream: when the browser closes the EventSource the
generator's ``finally`` block fires and we SIGTERM the subprocess. If
that fails we follow up with SIGKILL after a short grace period.

Wire format (one line per logcat line)::

    data: <one logcat line, no newline>\\n\\n

We do NOT pre-parse into JSON on the wire — the frontend tokenises lazily
when it needs to filter / colorise. This keeps per-line CPU cost minimal
and lets us stream chatty devices (2k+ lines/sec) without trouble.
"""

# cspell:ignore adbutils threadtime SIGTERM SIGKILL nonblocking dumpsys logcat

from __future__ import annotations

import logging
import shlex
import subprocess
import sys
import time
from typing import Generator, Optional

_log = logging.getLogger(__name__)

_VALID_LEVELS = {'V', 'D', 'I', 'W', 'E', 'F', 'S'}


def _build_filterspec(level: str, tag: Optional[str]) -> str:
    """Compose a logcat filter spec.

    - level='I' (default): suppress V/D, show I and above for every tag
    - level='V': show everything
    - tag given: include only that tag at ``level``, everything else silent
    """
    level = (level or 'I').upper()
    if level not in _VALID_LEVELS:
        level = 'I'
    if tag:
        # Sanitise: logcat tag spec disallows whitespace + colon.
        tag = tag.replace(':', '_').replace(' ', '_')[:64]
        return f'{tag}:{level} *:S'
    return f'*:{level}'


def _resolve_adb_path() -> str:
    """Find the adb binary our adb_bootstrap put on PATH."""
    # adb_bootstrap prepended its directory to PATH; shutil.which finds it.
    import shutil
    found = shutil.which('adb')
    return found or 'adb'


def stream(device_id: str, level: str = 'I', tag: Optional[str] = None,
           pid: Optional[int] = None) -> Generator[bytes, None, None]:
    """Yield SSE-framed bytes for one logcat session.

    The generator is intended to be wrapped by Flask's
    ``Response(stream_with_context(...), mimetype='text/event-stream')``.
    """
    adb = _resolve_adb_path()
    filterspec = _build_filterspec(level, tag)
    cmd = [adb, '-s', device_id, 'shell', 'logcat', '-v', 'threadtime', filterspec]
    if pid:
        cmd[5:5] = ['--pid', str(int(pid))]
    _log.info('[logcat] starting %s: %s', device_id, ' '.join(shlex.quote(x) for x in cmd))

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
        text=True,
        encoding='utf-8',
        errors='replace',
        # On Windows we want a separate process group so SIGTERM-equivalents
        # don't propagate to the parent dev server when we kill the child.
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0,
    )

    # Initial comment line keeps the connection open and tells the client
    # which device they're listening to. SSE comments start with ":".
    yield f': logcat {device_id} {filterspec}\n\n'.encode('utf-8')

    last_keepalive = time.monotonic()
    keepalive_interval = 15.0  # seconds

    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip('\r\n')
            if not line:
                # SSE keepalive comment every ~15s of silence.
                if time.monotonic() - last_keepalive > keepalive_interval:
                    yield b': keepalive\n\n'
                    last_keepalive = time.monotonic()
                continue
            # One event per line. Escape newlines that could fool the SSE parser
            # (shouldn't happen with logcat -v threadtime but be defensive).
            yield f'data: {line}\n\n'.encode('utf-8')
            last_keepalive = time.monotonic()
    except (GeneratorExit, BrokenPipeError, ConnectionError) as exc:
        _log.info('[logcat] client disconnect for %s: %s', device_id, type(exc).__name__)
    finally:
        _terminate(proc, device_id)


def _terminate(proc: subprocess.Popen, device_id: str) -> None:
    """Kill the logcat subprocess + drain its pipes so we don't leak FDs."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError as exc:
        _log.warning('[logcat] terminate %s failed: %s', device_id, exc)
    # Give it a short window to exit cleanly, then escalate.
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.05)
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError as exc:
            _log.warning('[logcat] kill %s failed: %s', device_id, exc)
    # Reap pipes so the OS releases them promptly.
    try:
        proc.stdout and proc.stdout.close()
        proc.stderr and proc.stderr.close()
    except OSError:
        pass
    _log.info('[logcat] subprocess for %s reaped (rc=%s)', device_id, proc.returncode)


def clear(device_id: str) -> bool:
    """Issue ``logcat -c`` so subsequent streams start with an empty buffer."""
    adb = _resolve_adb_path()
    try:
        subprocess.run(
            [adb, '-s', device_id, 'shell', 'logcat', '-c'],
            check=False, timeout=10,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.warning('[logcat] clear %s failed: %s', device_id, exc)
        return False
