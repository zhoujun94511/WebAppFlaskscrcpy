"""Decode messages on the ``adb`` (reliable) DataChannel and dispatch.

Supported event types:

    clipboard.set   → push text to device; reply ``clipboard.ack``
    clipboard.get   → read device clipboard; reply ``clipboard.value``
    ping            → reply ``pong``
    file.push       ┐
    file.chunk      │ delegated to ``services.file_transfer``
    file.end        │
    file.cancel     ┘

The route layer hands us ``client`` (live scrcpy.Client) and a
``reply(dict)`` callable that ships JSON back over the same channel.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict

from . import file_transfer

_log = logging.getLogger(__name__)

ReplyFn = Callable[[Dict[str, Any]], None]


def dispatch(client: Any, msg: Any, reply: ReplyFn) -> None:
    """Top-level entry point for one inbound DataChannel message."""
    if not isinstance(msg, (str, bytes)):
        return
    try:
        evt = json.loads(msg if isinstance(msg, str) else msg.decode('utf-8', 'ignore'))
    except (ValueError, TypeError):
        return
    t = evt.get('t')

    try:
        if t == 'clipboard.set':
            _set_clipboard(client, evt, reply)
        elif t == 'clipboard.get':
            _get_clipboard(client, reply)
        elif t == 'ping':
            reply({'t': 'pong', 'ts': time.time()})
        elif t == 'file.push':
            file_transfer.handle_push(evt, reply)
        elif t == 'file.chunk':
            file_transfer.handle_chunk(evt, reply)
        elif t == 'file.end':
            file_transfer.handle_end(evt, client, reply)
        elif t == 'file.cancel':
            file_transfer.handle_cancel(evt, reply)
    except (OSError, RuntimeError, ValueError, AttributeError, KeyError) as exc:
        _log.warning('adb dispatch error (%s): %s', t, exc)
        reply({'t': 'error', 'for': t, 'error': str(exc)})


def _set_clipboard(client: Any, evt: Dict[str, Any], reply: ReplyFn) -> None:
    seq = int(time.time_ns() & 0xFFFFFFFFFFFFFFFF)
    ok = client.control.set_clipboard(
        evt.get('text', ''),
        paste=bool(evt.get('paste', False)),
        sequence=seq,
    )
    reply({'t': 'clipboard.ack', 'ok': bool(ok), 'sequence': seq})


def _get_clipboard(client: Any, reply: ReplyFn) -> None:
    text = client.control.get_clipboard()
    reply({'t': 'clipboard.value', 'text': text})
