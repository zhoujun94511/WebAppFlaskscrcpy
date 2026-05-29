"""Background asyncio event loop running in a real OS thread.

Flask-SocketIO uses eventlet (greenlets); aiortc requires a real asyncio loop.
We run that loop in a dedicated thread and expose a thread-safe ``submit`` /
``run_sync`` API so eventlet-side handlers can schedule coroutines without
blocking the greenlet scheduler.

Usage:
    runner = get_runner()                 # idempotent
    fut = runner.submit(coro())           # fire-and-forget Future
    result = runner.run_sync(coro())      # blocks until result / raises
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import Future
from typing import Any, Coroutine, Optional

_log = logging.getLogger(__name__)


class AsyncRunner:
    def __init__(self, name: str = "webrtc-async") -> None:
        self._name = name
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._lock = threading.Lock()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or not self._loop.is_running():
            raise RuntimeError("AsyncRunner has not been started")
        return self._loop

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._ready.clear()
            self._thread = threading.Thread(
                target=self._run, name=self._name, daemon=True
            )
            self._thread.start()
            if not self._ready.wait(timeout=5):
                raise RuntimeError("AsyncRunner failed to start within 5s")
            _log.info("AsyncRunner '%s' started", self._name)

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            if not self._loop or not self._thread:
                return
            loop = self._loop

            async def _shutdown() -> None:
                # Cancel every outstanding task, drain them, then ask the loop
                # to stop — all inside the loop's own thread. No thread-safe
                # scheduling needed afterward.
                tasks = [
                    t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()
                ]
                for t in tasks:
                    t.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                loop.stop()

            try:
                fut = asyncio.run_coroutine_threadsafe(_shutdown(), loop)
                fut.result(timeout=timeout)
            except (RuntimeError, TimeoutError, asyncio.CancelledError) as exc:
                _log.warning("AsyncRunner shutdown error: %s", exc)
            self._thread.join(timeout=timeout)
            self._loop = None
            self._thread = None
            _log.info("AsyncRunner '%s' stopped", self._name)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except (RuntimeError, asyncio.CancelledError):
                pass
            loop.close()

    def submit(self, coro: Coroutine[Any, Any, Any]) -> "Future[Any]":
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def run_sync(self, coro: Coroutine[Any, Any, Any], timeout: float = 10.0) -> Any:
        return self.submit(coro).result(timeout=timeout)

    def call_soon(self, fn, *args) -> None:
        self.loop.call_soon_threadsafe(fn, *args)


_singleton: Optional[AsyncRunner] = None
_singleton_lock = threading.Lock()


def get_runner() -> AsyncRunner:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = AsyncRunner()
            _singleton.start()
        return _singleton


def shutdown_runner() -> None:
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            _singleton.stop()
            _singleton = None
