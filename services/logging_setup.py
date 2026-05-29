"""Centralised logging configuration.

Imported once at process start (``app.py``, ``start_dev.py``) to apply
a uniform format across every module. The level can be overridden at
runtime via the ``LOG_LEVEL`` environment variable; recognised values
are the standard Python names (``DEBUG``, ``INFO``, ``WARNING``,
``ERROR``, ``CRITICAL``) — case-insensitive.

Format:

    2026-05-27 13:42:07 [INFO ] services.uploads: staging upload api.apk

* ``%(asctime)s`` — full year-month-day hour:minute:second.
* ``[INFO ]`` — level tag, padded to 5 chars for column alignment, and
  ANSI-colourised on a TTY (DEBUG cyan, INFO green, WARNING yellow,
  ERROR red, CRITICAL bold red). Plain text on non-TTY sinks so log
  files stay greppable.
* ``%(name)s`` — dotted module path (``services.uploads``,
  ``scrcpy.webrtc.peer_manager`` …) when loggers are obtained via
  ``logging.getLogger(__name__)``.
* ``%(message)s`` — the log message itself.

Calling :func:`configure` more than once is safe: subsequent calls only
adjust the level, they do not duplicate handlers.
"""

from __future__ import annotations

import datetime
import logging
import os
import sys
from pathlib import Path
from typing import Optional, Union

DEFAULT_LEVEL = "INFO"
DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Where to drop per-run log files. Path is relative to the repo root so the
# files live alongside everything else and ``git status`` notices them
# (add ``logs/`` to ``.gitignore`` if you don't want that). One file per
# backend process start, named ``applog-YYYYMMDD-HHMMSS.log``. No rotation
# inside a single run — the file just grows until the process exits, at
# which point the next start gets a fresh file.
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_FILENAME_FMT = "applog-{ts}.log"

# ANSI SGR colour codes. Empty strings on non-TTY sinks so log files
# don't end up littered with ESC[…m sequences.
_RESET = "\x1b[0m"
_LEVEL_COLOURS = {
    "DEBUG": "\x1b[36m",       # cyan
    "INFO": "\x1b[32m",        # green
    "WARNING": "\x1b[33m",     # yellow
    "ERROR": "\x1b[31m",       # red
    "CRITICAL": "\x1b[1;31m",  # bold red
}

# Pad every level name to 5 chars so the columns after it line up.
# WARNING gets shortened to WARN, CRITICAL to CRIT, so each fits in 5.
_LEVEL_SHORT = {
    "DEBUG": "DEBUG",
    "INFO": "INFO ",
    "WARNING": "WARN ",
    "ERROR": "ERROR",
    "CRITICAL": "CRIT ",
}

# Third-party loggers that are noisy at their default level but rarely
# carry actionable information for THIS project. We bump them to WARNING
# so we still see real failures but stop spamming the console with
# expected platform quirks.
#
#   aioice.ice → on Windows it tries to bind UDP sockets on every local
#     interface during ICE gathering. APIPA (169.254.x.x) addresses on
#     virtual NICs (Hyper-V / WSL / VirtualBox / Docker) reject the
#     bind with WSAEADDRNOTAVAIL (WinError 10049). aioice logs each
#     failure at INFO; on a typical dev box that's 4-8 lines per new
#     RTCPeerConnection. The "could not bind" message is informational
#     — gathering continues on the real Wi-Fi/Ethernet interface and
#     WebRTC works fine. Real WARNING+ messages from aioice (STUN
#     failures, ICE timeouts) still propagate.
_QUIET_LOGGERS = {
    "aioice.ice": logging.WARNING,
}

_configured = False


class _AlignedFormatter(logging.Formatter):
    """Format with padded level names and optional ANSI colouring.

    Colour is applied only when ``use_color=True`` (i.e. the underlying
    stream is a TTY). Level names are shortened to 5 chars where needed
    (WARNING → WARN, CRITICAL → CRIT) so the column after them lines up
    across all log lines.
    """

    def __init__(self, *, use_color: bool, datefmt: str = DEFAULT_DATEFMT):
        super().__init__(datefmt=datefmt)
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        # Stamp time / message / name through the standard machinery,
        # but build the prefix ourselves so we can colourise the level.
        record.message = record.getMessage()
        asctime = self.formatTime(record, self.datefmt)
        level_short = _LEVEL_SHORT.get(record.levelname, record.levelname.ljust(5)[:5])
        if self._use_color:
            colour = _LEVEL_COLOURS.get(record.levelname, "")
            level_tag = f"[{colour}{level_short}{_RESET}]"
        else:
            level_tag = f"[{level_short}]"
        line = f"{asctime} {level_tag} {record.name}: {record.message}"
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            line = f"{line}\n{record.exc_text}"
        if record.stack_info:
            line = f"{line}\n{self.formatStack(record.stack_info)}"
        return line


def _resolve_level(level: Optional[Union[str, int]]) -> int:
    """Accept a string, int, or ``None`` (use env / default)."""
    if level is None:
        level = os.environ.get("LOG_LEVEL", DEFAULT_LEVEL)
    if isinstance(level, int):
        return level
    parsed = logging.getLevelName(str(level).strip().upper())
    if isinstance(parsed, int):
        return parsed
    return logging.INFO


def _stream_supports_color(stream) -> bool:
    """Best-effort TTY detection. Honours NO_COLOR / FORCE_COLOR env vars."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    try:
        return bool(getattr(stream, "isatty", lambda: False)())
    except (OSError, AttributeError, ValueError):
        # OSError: detached / closed stream. AttributeError: exotic
        # stream object. ValueError: I/O operation on closed file.
        return False


def configure(level: Optional[Union[str, int]] = None) -> int:
    """Apply the project-wide logging format.

    Returns the effective numeric level so callers can log a one-line
    confirmation if they want.
    """
    global _configured
    effective = _resolve_level(level)

    root = logging.getLogger()
    root.setLevel(effective)

    if not _configured:
        handler = logging.StreamHandler(stream=sys.stderr)
        use_color = _stream_supports_color(handler.stream)
        # Enable ANSI on Windows consoles where possible (Win10+).
        if use_color and os.name == "nt":
            try:
                import ctypes

                kernel32 = ctypes.windll.kernel32
                # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
                kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
                kernel32.SetConsoleMode(kernel32.GetStdHandle(-12), 7)
            except (OSError, AttributeError):
                # No windll (non-CPython) or missing kernel32 entry — just
                # skip enabling VT mode, the colours simply won't render.
                pass
        handler.setFormatter(_AlignedFormatter(use_color=use_color))
        root.addHandler(handler)

        # Mirror everything to a per-run file under ``logs/`` so debugging
        # post-mortem doesn't require having had the terminal open. The
        # filename includes a wall-clock timestamp so consecutive runs
        # don't overwrite each other and ``logs/`` becomes a chronological
        # archive. ``mkdir(exist_ok=True)`` makes the directory lazily —
        # we don't ship an empty ``logs/`` in the repo.
        try:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            log_path = _LOG_DIR / _LOG_FILENAME_FMT.format(ts=ts)
            file_handler = logging.FileHandler(
                str(log_path), mode="a", encoding="utf-8"
            )
            file_handler.setFormatter(_AlignedFormatter(use_color=False))
            root.addHandler(file_handler)
            # Surface the file path on stderr so the user can grep / tail
            # it without hunting through the directory.
            print(f"[logging] writing to {log_path}", file=sys.stderr)
        except OSError as exc:
            # Disk full / read-only mount / permission denied: log to
            # stderr only and keep going. Worst case the user just
            # doesn't get the file mirror.
            print(f"[logging] file handler disabled: {exc}", file=sys.stderr)

        _configured = True
    else:
        # Idempotent reconfigure: refresh every existing handler's format.
        for h in root.handlers:
            stream = getattr(h, "stream", None)
            use_color = _stream_supports_color(stream) if stream is not None else False
            h.setFormatter(_AlignedFormatter(use_color=use_color))

    # Quiet down third-party loggers whose default chatter isn't useful
    # in our context. Done on every ``configure`` call so it survives a
    # later root-level reset.
    for name, lvl in _QUIET_LOGGERS.items():
        logging.getLogger(name).setLevel(lvl)

    return effective
