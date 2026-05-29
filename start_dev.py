r"""One-click dev launcher for WebAppFlaskscrcpy.

Usage:
    python start_dev.py                      # bind 0.0.0.0, print LAN URLs (default)
    python start_dev.py start                # same as above
    python start_dev.py stop                 # only free local service ports
    python start_dev.py start --local-only   # bind 127.0.0.1 only (no LAN exposure)
    python start_dev.py start --no-browser   # skip auto-opening tab
    python start_dev.py start --verbose-logs # show full child output

Design mirrors d:\projectx\Scenario_Engine\start_dev.py — same port-cleanup,
signal-handling and log-relay scaffolding — trimmed to this project's two
services (Flask-SocketIO backend on 5001, Vite dev server on 5173).
"""

from __future__ import annotations

# cspell:ignore scrcpy webappflask PYTHONUNBUFFERED

import argparse
import atexit
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT / "frontend"

# Windows lays the venv out as ``.venv/Scripts/python.exe``; POSIX uses
# ``.venv/bin/python``. Detect once and branch every OS-specific path below.
IS_WINDOWS = os.name == "nt"
VENV_PYTHON = (
    ROOT / ".venv" / "Scripts" / "python.exe"
    if IS_WINDOWS
    else ROOT / ".venv" / "bin" / "python"
)

BACKEND_PORT = 5001
CLIENT_PORT = 5173
BACKEND_URL = f"http://127.0.0.1:{BACKEND_PORT}"
CLIENT_URL = f"http://127.0.0.1:{CLIENT_PORT}"

_started_processes: list[subprocess.Popen[Any]] = []
_log_threads: list[threading.Thread] = []
_log_lock = threading.Lock()
# Per-service ring buffer of the most recent RAW output lines (unfiltered).
# When a child dies unexpectedly the condensed relay usually hides the cause
# (a traceback, a bind error, a "Press CTRL+C to quit" then silence…), so we
# replay this tail to make the failure diagnosable instead of just printing
# an opaque exit code.
_recent_output: dict[str, "deque[str]"] = {}
_RECENT_OUTPUT_MAXLEN = 60

try:
    # UTF-8 stdout so we don't crash log relay on Windows code pages.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except (AttributeError, TypeError, ValueError):
    pass


def _print(msg: str) -> None:
    with _log_lock:
        print(f"[start-dev] {msg}", flush=True)


def _resolve_local_ip() -> str | None:
    try:
        sys.path.insert(0, str(ROOT))
        from scrcpy.scrcpynetwork import get_local_ip  # type: ignore

        return get_local_ip()
    except (ImportError, ModuleNotFoundError, OSError):
        pass

    sock = None
    try:
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        if sock is not None:
            sock.close()


# Bounded subprocess timeouts: port cleanup is best-effort, never block startup.
_POWERSHELL_TIMEOUT_S = 6.0
_NETSTAT_TIMEOUT_S = 8.0
_TASKLIST_TIMEOUT_S = 4.0
_TASKKILL_TIMEOUT_S = 6.0


def _powershell_lines(script: str) -> list[str]:
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=_POWERSHELL_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        _print(f"powershell call timed out / failed ({exc}); falling back to netstat")
        return []
    if completed.returncode != 0:
        return []
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _netstat_owners(port: int) -> list[int]:
    owners: list[int] = []
    try:
        completed = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_NETSTAT_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError):
        return owners

    token = f":{port}"
    for line in completed.stdout.splitlines():
        if token not in line:
            continue
        parts = [p for p in line.split() if p]
        if len(parts) < 5:
            continue
        if not any(p.endswith(token) for p in parts):
            continue
        if not any(p in {"LISTENING", "监听"} for p in parts):
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid > 0 and pid not in owners:
            owners.append(pid)
    return owners


def _pid_is_running(pid: int) -> bool:
    if not IS_WINDOWS:
        # POSIX: signal 0 is the canonical "does this PID exist?" probe.
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Exists but owned by another user — still "running".
            return True
        except OSError:
            return True
    try:
        probe = subprocess.run(
            ["tasklist.exe", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_TASKLIST_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError):
        return True
    output = f"{probe.stdout}\n{probe.stderr}"
    return re.search(rf"\b{pid}\b", output) is not None


def _lsof_owners(port: int) -> list[int]:
    """POSIX: PIDs listening on ``port`` via ``lsof`` (macOS + most Linux)."""
    owners: list[int] = []
    if shutil.which("lsof") is None:
        return owners
    try:
        completed = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_NETSTAT_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError):
        return owners
    for raw in completed.stdout.split():
        try:
            pid = int(raw.strip())
        except ValueError:
            continue
        if pid > 0 and pid not in owners:
            owners.append(pid)
    return owners


def get_port_owners(port: int) -> list[int]:
    if not IS_WINDOWS:
        return [p for p in _lsof_owners(port) if _pid_is_running(p)]

    owners: list[int] = []
    for raw in _powershell_lines(
        f"$ErrorActionPreference='SilentlyContinue'; "
        f"Get-NetTCPConnection -LocalPort {port} -State Listen "
        f"| Select-Object -ExpandProperty OwningProcess"
    ):
        try:
            pid = int(raw)
        except ValueError:
            continue
        if pid > 0 and pid not in owners and _pid_is_running(pid):
            owners.append(pid)
    if owners:
        return owners
    return _netstat_owners(port)


def _posix_kill_pid(pid: int, port: int) -> None:
    """SIGTERM, then SIGKILL if it doesn't go away promptly."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError as exc:
        _print(f"Could not stop PID {pid} on port {port}: {exc}")
        return
    for _ in range(10):
        if not _pid_is_running(pid):
            _print(f"Freed port {port}: stopped PID {pid}")
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
        _print(f"Freed port {port}: force-killed PID {pid}")
    except (ProcessLookupError, OSError):
        pass


def stop_port_owner(port: int) -> None:
    for pid in get_port_owners(port):
        if not _pid_is_running(pid):
            continue
        if not IS_WINDOWS:
            _posix_kill_pid(pid, port)
            continue
        try:
            proc = subprocess.run(
                ["taskkill.exe", "/PID", str(pid), "/F", "/T"],
                capture_output=True,
                text=True,
                check=False,
                timeout=_TASKKILL_TIMEOUT_S,
            )
            if proc.returncode == 0:
                _print(f"Freed port {port}: stopped PID {pid}")
            else:
                _print(
                    f"taskkill failed for PID {pid} on port {port}: "
                    f"{proc.stderr.strip() or proc.stdout.strip()}"
                )
        except subprocess.TimeoutExpired:
            _print(f"taskkill timed out for PID {pid} on port {port}; skipping")
        except OSError as exc:
            _print(f"Could not stop PID {pid} on port {port}: {exc}")


def reset_port(port: int) -> None:
    for pid in get_port_owners(port):
        stop_port_owner(port)
        _ = pid
    for _retry in range(8):
        if not get_port_owners(port):
            return
        stop_port_owner(port)
        time.sleep(0.8)
    remaining = [p for p in get_port_owners(port) if _pid_is_running(p)]
    if remaining:
        raise RuntimeError(
            f"Port {port} is still held by PID(s): {', '.join(map(str, remaining))}"
        )


def wait_for_http(
    url: str,
    timeout_seconds: int = 120,
    *,
    label: str,
    processes: Iterable[tuple[str, subprocess.Popen[Any]]] | None = None,
) -> None:
    deadline = time.time() + timeout_seconds
    last_error: str | None = None
    while time.time() < deadline:
        if processes is not None:
            for name, proc in processes:
                rc = proc.poll()
                if rc is not None:
                    raise RuntimeError(f"{name} exited early with code {rc}")
        try:
            request = Request(url, method="GET")
            with urlopen(request, timeout=5) as response:
                if 200 <= getattr(response, "status", 200) < 500:
                    return
        except HTTPError as exc:
            # A 4xx response still proves the server is listening and serving.
            # Since adding the auth gate, GET /api/devices returns 401 until a
            # session exists — that's "ready" for our purposes (urlopen raises
            # HTTPError on 4xx/5xx instead of entering the block above).
            if exc.code < 500:
                return
            last_error = str(exc)
            time.sleep(1.5)
        except (URLError, TimeoutError, OSError) as exc:
            last_error = str(exc)
            time.sleep(1.5)
    raise RuntimeError(
        f"{label} did not become ready within {timeout_seconds}s: {last_error or 'unknown error'}"
    )


def _service_tag(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "service"


def _should_relay_log(name: str, line: str, verbose_logs: bool) -> bool:
    if verbose_logs:
        return True
    stripped = line.strip()
    if not stripped:
        return False
    lowered = stripped.lower()

    if "error" in lowered or "warn" in lowered or "fatal" in lowered or "traceback" in lowered:
        return True

    if name == "backend":
        return any(m in stripped for m in (
            "Service started",
            "Running on",
            "WebRTC signaling registered",
            "ADB terminal registered",
            "Restarting with",
        ))

    if name == "client":
        return any(m in stripped for m in (
            "VITE",
            "Local:",
            "Network:",
            "ready in",
        ))

    return False


def _relay_log_line(name: str, line: str, verbose_logs: bool) -> None:
    if not _should_relay_log(name, line, verbose_logs):
        return
    with _log_lock:
        safe = line.replace("➜", "->")
        print(f"[{_service_tag(name)}] {safe}", flush=True)


def start_process(
    name: str,
    args: list[str],
    cwd: Path,
    env: dict[str, str],
    *,
    verbose_logs: bool,
) -> subprocess.Popen[Any]:
    _print(f"Starting {name}...")
    # Put each child in its own process group so we can signal the whole
    # tree on shutdown without hitting the launcher itself. Windows uses
    # CREATE_NEW_PROCESS_GROUP; POSIX uses a new session (setsid).
    group_kwargs: dict[str, Any] = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if IS_WINDOWS
        else {"start_new_session": True}
    )
    proc = subprocess.Popen(
        args,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **group_kwargs,
    )
    _started_processes.append(proc)
    _recent_output[name] = deque(maxlen=_RECENT_OUTPUT_MAXLEN)

    def _pump() -> None:
        if proc.stdout is None:
            return
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            _recent_output[name].append(line)  # keep every line for diagnostics
            _relay_log_line(name, line, verbose_logs)

    t = threading.Thread(target=_pump, name=f"{_service_tag(name)}-log", daemon=True)
    t.start()
    _log_threads.append(t)
    return proc


def _explain_exit_code(rc: int) -> str:
    """Human hint for the more cryptic Windows/POSIX exit codes."""
    if rc == 0:
        return "clean exit"
    if rc in (4294967295, -1):  # 0xFFFFFFFF — typical of TerminateProcess / console-close
        return (
            "0xFFFFFFFF: process was terminated rather than crashing — usually a "
            "stale instance on the port, antivirus, or the console closing"
        )
    if rc == 1:
        return "generic error / taskkill /F"
    if rc < 0:
        return f"killed by signal {-rc}"
    return "see output below"


def _dump_recent_output(name: str) -> None:
    """Replay the last buffered lines from a child that just died."""
    lines = list(_recent_output.get(name, ()))
    tag = _service_tag(name)
    with _log_lock:
        if not lines:
            print(f"[{tag}] (no captured output before exit)", flush=True)
            return
        print(f"[{tag}] ---- last {len(lines)} line(s) before it exited ----", flush=True)
        for ln in lines:
            print(f"[{tag}] {ln}", flush=True)
        print(f"[{tag}] ---- end of captured output ----", flush=True)


_last_signal_at = 0.0
_DOUBLE_TAP_WINDOW_S = 2.0


def install_signal_handlers() -> None:
    """Single Ctrl+C = ignore (likely a reloader broadcast); double-tap = exit."""

    def _children_healthy() -> bool:
        if not _started_processes:
            return False
        return all(p.poll() is None for p in _started_processes)

    def _handler(signum: int, _frame: object) -> None:
        global _last_signal_at
        now = time.monotonic()
        is_double_tap = (now - _last_signal_at) <= _DOUBLE_TAP_WINDOW_S
        _last_signal_at = now
        if signum == signal.SIGINT and _children_healthy() and not is_double_tap:
            _print(
                f"Received signal {signum} (possibly reloader). "
                f"Press Ctrl+C again within {_DOUBLE_TAP_WINDOW_S:.0f}s to shut down."
            )
            return
        _print(f"Received signal {signum}, shutting down...")
        kill_started_processes()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def _posix_kill_tree(proc: subprocess.Popen[Any]) -> None:
    """Signal the child's whole session group (started via start_new_session)."""
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        pgid = None
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGTERM)
        else:
            proc.terminate()
    except (ProcessLookupError, OSError):
        return
    try:
        proc.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, OSError):
        pass


def kill_started_processes() -> None:
    for proc in reversed(_started_processes):
        if proc.poll() is not None:
            continue
        if not IS_WINDOWS:
            _posix_kill_tree(proc)
            continue
        try:
            subprocess.run(
                ["taskkill.exe", "/PID", str(proc.pid), "/F", "/T"],
                capture_output=True,
                text=True,
                check=False,
                timeout=_TASKKILL_TIMEOUT_S,
            )
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass


def build_env(overrides: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env.update(overrides)
    return env


def _resolve_node_exe() -> str | None:
    # shutil.which is cross-platform: resolves node.exe on Windows and the
    # ``node`` binary on PATH for macOS/Linux (respects PATHEXT on Windows).
    return shutil.which("node")


def stop_mode() -> int:
    _print("Cleaning local service ports...")
    reset_port(BACKEND_PORT)
    reset_port(CLIENT_PORT)
    _print("Cleanup completed.")
    return 0


def start_mode(args: argparse.Namespace) -> int:
    if not VENV_PYTHON.exists():
        raise FileNotFoundError(f"Missing virtualenv Python: {VENV_PYTHON}")

    vite_js = FRONTEND_DIR / "node_modules" / "vite" / "bin" / "vite.js"
    if not vite_js.exists():
        raise FileNotFoundError(
            f"Missing Vite ({vite_js}). Run `npm install` inside {FRONTEND_DIR} first."
        )

    reset_port(BACKEND_PORT)
    reset_port(CLIENT_PORT)

    atexit.register(kill_started_processes)
    install_signal_handlers()

    services: dict[str, subprocess.Popen[Any]] = {}

    # Default is LAN-exposed (0.0.0.0) so phones / tablets on the same
    # network can hit the dev server directly — this app is a device
    # mirror, LAN access is the common case. Pass ``--local-only`` to
    # restrict to loopback when you specifically don't want the dev
    # server reachable from other machines.
    backend_host = "127.0.0.1" if args.local_only else "0.0.0.0"
    client_host = "127.0.0.1" if args.local_only else "0.0.0.0"

    backend_env = build_env({
        "HOST": backend_host,
        "PORT": str(BACKEND_PORT),
        "PYTHONUNBUFFERED": "1",
        # Suppress backend's own auto-open browser tab — we open the Vite
        # frontend (5173) instead, which proxies to the backend.
        "OPEN_BROWSER": "0",
        # Forward WebRTC / terminal feature flags, honouring caller's env.
        "ENABLE_WEBRTC": os.environ.get("ENABLE_WEBRTC", "1"),
        "ENABLE_TERMINAL": os.environ.get("ENABLE_TERMINAL", "1"),
    })

    services["backend"] = start_process(
        "backend",
        [str(VENV_PYTHON), str(ROOT / "app.py")],
        ROOT,
        backend_env,
        verbose_logs=args.verbose_logs,
    )

    _print("Waiting for backend to listen...")
    wait_for_http(
        f"{BACKEND_URL}/api/devices",
        label="backend (/api/devices)",
        processes=(("backend", services["backend"]),),
    )

    node_exe = _resolve_node_exe() or "node"
    client_env = build_env({"PYTHONUNBUFFERED": "1"})
    services["client"] = start_process(
        "client",
        [
            node_exe,
            str(vite_js),
            "--host", client_host,
            "--port", str(CLIENT_PORT),
            "--strictPort",
        ],
        FRONTEND_DIR,
        client_env,
        verbose_logs=args.verbose_logs,
    )

    _print("Waiting for client dev server...")
    wait_for_http(
        CLIENT_URL,
        label="client dev server",
        processes=(
            ("backend", services["backend"]),
            ("client", services["client"]),
        ),
    )

    _print("All services are up.")
    _print(f"Frontend:  {CLIENT_URL}")
    _print(f"Backend:   {BACKEND_URL}")
    if not args.local_only:
        local_ip = _resolve_local_ip()
        if local_ip:
            _print(f"Frontend(LAN):  http://{local_ip}:{CLIENT_PORT}")
            _print(f"Backend(LAN):   http://{local_ip}:{BACKEND_PORT}")
        else:
            _print("LAN binding active, but local IP could not be resolved.")

    if not args.no_browser:
        try:
            webbrowser.open_new_tab(CLIENT_URL)
        except OSError:
            pass

    try:
        while True:
            stale: list[str] = []
            for name, proc in services.items():
                rc = proc.poll()
                if rc is None:
                    continue
                if name == "client":
                    # Vite wrapper sometimes exits while dev server stays alive.
                    try:
                        wait_for_http(CLIENT_URL, timeout_seconds=5, label="client dev server")
                        _print("Client launcher exited but dev server is reachable; continuing.")
                        stale.append(name)
                        continue
                    except RuntimeError:
                        pass
                _dump_recent_output(name)
                raise RuntimeError(
                    f"{name} exited unexpectedly with code {rc} "
                    f"({_explain_exit_code(rc)}). See the [{_service_tag(name)}] "
                    f"output above for the cause."
                )
            for s in stale:
                services.pop(s, None)
            time.sleep(2)
    finally:
        kill_started_processes()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start or stop the WebAppFlaskscrcpy local dev stack."
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("start", "stop"),
        default="start",
        help="Use 'stop' to free dev ports without starting anything.",
    )
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not auto-open the client in a browser.")
    parser.add_argument("--verbose-logs", action="store_true",
                        help="Show full logs from child processes instead of the condensed default.")
    parser.add_argument("--local-only", action="store_true",
                        help="Bind backend/client to 127.0.0.1 instead of the LAN default.")
    # Backwards-compat: the previous flag was ``--lan`` (opt-in). LAN is
    # now the default so the flag is a no-op, but keep it accepted so old
    # shortcuts / scripts don't break.
    parser.add_argument("--lan", action="store_true",
                        help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.command == "stop":
        return stop_mode()
    return start_mode(args)


if __name__ == "__main__":
    raise SystemExit(main())
