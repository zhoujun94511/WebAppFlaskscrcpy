# WebAppFlaskScrcpy — Browser-Based Android Mirroring & Control Platform

<div align="center">

<img src="frontend/public/logo.svg" width="96" alt="WebAppFlaskScrcpy logo" />

**Low-latency Android screen mirroring, control and ops — entirely in the browser, over WebRTC**

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.1-green.svg)](https://flask.palletsprojects.com)
[![Vue](https://img.shields.io/badge/Vue-3.x-42b883.svg)](https://vuejs.org)
[![WebRTC](https://img.shields.io/badge/WebRTC-aiortc-orange.svg)](https://github.com/aiortc/aiortc)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[中文](README.md) • [English](README_en.md)

[Features](#features) • [Quick Start](#quick-start) • [Security](#security-design) • [API](#api-reference) • [Deployment](#deployment)

</div>

## Overview

WebAppFlaskScrcpy is a **client-free** Android mirroring and control platform. Open a web page, and you can mirror a phone's screen in real time, control it remotely (touch / keys / text), transfer files, tail logs, manage apps, and use an embedded ADB terminal — no local installation required.

Unlike vanilla scrcpy, all three **data planes** — video, input, files — run over **WebRTC** (RTP + DataChannel). Socket.IO is reserved for signaling, the terminal, and REST-style HTTP routes. The result is near-native latency in the browser plus natural multi-user, multi-device access over a LAN.

On top of mirroring, the platform ships a full **account system** and a **device reservation (occupancy)** mechanism, turning shared real devices into schedulable, auditable resources: you can see who holds which device, and admins can force-release devices and manage users.

The platform also ships a **Lab** module (sidebar entry "Lab") that offers **master→slave multi-device sync**: drive one master and have any number of slaves follow along — either by **proportional coordinate mirroring**, or **semantically** via uiautomator2 (the master's tap is resolved to a control by `resourceId` / `text` / `xpath`, slaves locate the same control and click; failure transparently falls back to coordinates). Useful for multi-device regression, batch demos, compatibility cross-checks, etc.

## Screenshots

<div align="center">

**Main interface**

<img src="pic/Main%20Interface%20Display-EN.png" width="900" alt="Main interface" />

**Multi-device grid**

<img src="pic/Multi-Device%20Display-EN.png" width="900" alt="Multi-device side-by-side view" />

**Single-device stage**

<img src="pic/Single-Device%20Display-EN.png" width="900" alt="Single-device stage view" />

</div>

## Motivation

### Background

Operating real devices remotely is a high-frequency need for mobile testing, ops and demos. Traditional approaches hurt:

- **Heavy clients**: scrcpy / mirroring tools must be installed and adb-configured on every operator machine.
- **Contention chaos**: a single device gets fought over with no exclusivity or reservation.
- **No access boundaries**: anyone can connect and control; no accounts, no audit.
- **Cross-platform drift**: startup, adb and process management differ across Windows / macOS / Linux.

### Goals

1. **Zero client** — the browser is the console; any device on the LAN can connect.
2. **Low latency** — video/input/files over WebRTC, close to a local connection.
3. **Governable** — accounts + roles + reservations make devices a managed, auditable shared resource.
4. **Cross-platform** — one codebase boots on Windows / macOS / Linux, bundling adb for each.

## Pain Points Solved

### 1. High barrier to remote control
**Problem**: every operator box needs scrcpy + adb + drivers.
**Solution**: the server centralizes adb and scrcpy-server; operators only need a browser. On first boot the server extracts the OS-matching platform-tools from `resources/re_adb/` and prepends it to PATH (`services/adb_bootstrap.py`).

### 2. Multiple people fighting over one device
**Problem**: scarce real devices, concurrent connections interfere, no exclusivity.
**Solution**: a **reservation/occupancy** mechanism — a user holds a device for a chosen duration during which only they may control it; the owner can release, it auto-expires, and admins can force-release (`services/reservations.py`).

### 3. No permissions or audit
**Problem**: no account model — anyone connects, nothing is controllable or traceable.
**Solution**: a built-in three-tier role system (super admin / admin / user), server-side sessions, a central login gate, and an admin user-management panel.

### 4. Large attack surface
**Problem**: login endpoints are brute-forceable; unvalidated input invites abuse/injection.
**Solution**: parameterized SQL, salted password hashing, revocable server-side sessions, **login lockout**, **input validation**, and **anti-enumeration** login (see [Security](#security-design)).

### 5. Cross-platform differences
**Problem**: process/port management, venv layout and the adb binary all differ across OSes.
**Solution**: the `start_dev.py` launcher branches per OS (port cleanup, process groups, venv paths); bundled adb restores exec bits / strips the macOS quarantine attribute automatically.

## How It Works

### Core architecture

```
Browser (Vue3 SPA)
   │  HTTP / Socket.IO (long-polling)         WebRTC (RTP + DataChannel)
   ▼                                              ▼
Flask + Flask-SocketIO  ──signaling──►  aiortc (asyncio thread)  ──►  scrcpy-server (device)
   │                                              ▲
   │  REST: device/account/reservation/files...   │ adb (adbutils)
   ▼                                              │
SQLite (accounts/sessions/reservations)    Android device (USB/TCP)
```

- **Threading, not eventlet**: aiortc runs in a dedicated asyncio thread and needs real OS sockets; eventlet's monkey-patching hijacks the global socket and deadlocks asyncio's selector. So Socket.IO uses `async_mode="threading"`, and signaling is forced onto HTTP long-polling (Werkzeug + threading can't cleanly complete a WebSocket upgrade). The heavy data planes run on WebRTC's own RTP/DataChannel.
- **All three data planes over WebRTC**: video (H.264 RTP), input (unreliable DataChannel), files (reliable DataChannel).
- **Device access**: `scrcpy/` is a trimmed scrcpy client port (`scrcpyclients` / `scrcpycore` / `scrcpycontrol` / `scrcpyconst` / `scrcpynetwork`) that pushes and launches the device-side scrcpy-server via adbutils.
- **Embedded terminal**: speaks the adb `shell:v2` protocol over a raw socket; the PTY lives on the device, so the host needs no `pty`/`winpty` — fully cross-platform (`terminal/adb_shell.py`).

### Reservation model

- `device_id` is the reservation table's PRIMARY KEY → the UNIQUE constraint settles concurrent claims (the loser gets an IntegrityError, never a half-applied second reservation).
- All three release paths funnel through `release()`: owner cancel / admin force / expiry sweeper.
- Every release runs `_teardown`: stop the scrcpy client → close all WebRTC peers for that device → broadcast `device_released`, so a released device is genuinely free.
- **Three-plane gating**: HTTP (`_require_owner` in `api/devices.py`), WebRTC (`webrtc:offer`) and terminal (`terminal:open`) all call `reservations.assert_owner` — guarding only HTTP would be bypassable via the other two planes.

### 🧪 Lab · Multi-device sync — implementation notes

**Architecture stance**: fully pluggable, purely additive. The backend blueprint is only registered when `config.ENABLE_SYNC=True`; the data plane (input fan-out) is wired into `services/scrcpy_input.dispatch()` with a **defensive try/except**, so any failure there can never disturb the master's own control path.

**Data-plane injection point**: `services/scrcpy_input.dispatch()` — the single funnel through which every WebRTC `input` DataChannel event passes. We hook fan-out at its tail (`if config.ENABLE_SYNC: sync_dispatcher.fanout(...)`). The two HTTP-based controls (quick keyevents, directional swipes) get the same defensive fan-out hook at their corresponding `api/devices.py` route tails.

**Occupancy bypass**: group creation calls `reservations.claim(..., allow_multi=True)` — the **only** additive bypass of the "one-device-per-user" anti-hogging rule (the parameter defaults to `False`, so every existing call site behaves exactly as before). Dissolving a group releases the slaves the same way.

**Two sync modes**:
- **Coordinate**: fan-out maps `master_resolution → slave_resolution` proportionally and injects via `scrcpy.control.touch/swipe/text/keycode`. Fast path runs inline on the aiortc loop thread (single socket write); swipe is offloaded to a worker.
- **Semantic** (built on uiautomator2 v3): on touch-up, `semantic_event_builder.build_tap()` calls `dump_hierarchy()` on the master to reverse-look-up the control under the point; each slave is then matched in order **resourceId → resourceId+text → text → description → xpath** and clicked; any miss falls back to coordinate. The whole semantic path is offloaded to a worker thread (master `dump_hierarchy` is too slow for the aiortc loop). u2 v3 launches its bundled `u2.jar` via `app_process` as a transient process — **NOT a persistently installed app**, so device-side footprint is much smaller than v2's atx-agent.

**Concurrency safety (multi-user scenarios)**:
- u2's jsonrpc connection isn't thread-safe → `u2_pool.device_lock(serial)` provides a **per-device RLock**, serialising u2 calls on the same device while different devices stay fully parallel.
- Each worker thread re-validates **before** touching every slave: group still exists & enabled + slave still a member + **slave still reserved by the group owner**. This closes the race "device was force-released / expired but an in-flight worker still pokes the now-foreign device."
- Semantic taps are **single-flight + 250 ms throttled** per master, preventing rapid taps from piling up master UI dumps. Slave execution uses a `ThreadPoolExecutor` (default 8 concurrent).

**Tap vs. drag detection**: in semantic mode, touch-DOWN records the start point; touch-UP compares displacement. ≥ `SYNC_SWIPE_MIN_PX` (24 px) → treated as a **drag** mirrored as a coordinate swipe to every slave; below threshold → it's a **tap** routed through semantic click. (Fixes an early bug where on-screen drags collapsed into a single tap.)

**Lifecycle hooks**: `reservations._teardown()` (the single funnel for all release paths) defensively calls `sync.lifecycle.on_device_gone()` at the end — a released/offline **master** dissolves its group and releases all slaves; a released/offline **slave** is removed from its group (the rest keep running).

**Frontend integration caveats**:
- `ensureLabStream` deliberately **skips devices already streaming** — calling `startDevice` again on a healthy master triggers scrcpy's `reset_video` re-negotiation, which stalls its video (remote track goes muted). Real bug, hard-learned.
- Slaves block manual touch/scroll/key/text input at the `useScrcpySession.emitTo` chokepoint while a group is active (so they stay clean mirrors), but system events (power/rotate/clipboard) still flow.

**Logs & evidence**:
- Execution stream goes to `logs/sync.log` via `RotatingFileHandler` (2 MB × 3 backups ≈ 8 MB ceiling; rotation IS the cleanup, no scheduled task needed).
- A total failure (semantic AND coordinate fallback both fail) invokes `failure_collector.capture()`, dropping **screenshot + UI hierarchy XML + JSON detail** under `data/sync_failures/<group>/<date>/`. These are discrete files (no rotation), so an age-based cleanup throttled to once per hour and triggered off capture itself does the housekeeping (retention = `SYNC_FAILURE_RETENTION_DAYS` days).
- **No DB tables added**: real-time per-slave results live in the dispatcher's in-memory snapshot (one entry per slave, naturally bounded), the UI polls every 1.5 s to colour each chip.

## Project Structure

```
WebAppFlaskscrcpy/
├── app.py                    # Flask + Socket.IO entry; blueprints, login gate, SPA hosting
├── start_dev.py              # Cross-platform launcher (backend 5001 + Vite 5173)
├── requirements.txt          # Python deps (UTF-8)
├── api/                      # HTTP / Socket.IO routes (thin adapters)
│   ├── authentication.py     #   login/register/session/user-mgmt + login lockout
│   ├── reservations.py       #   reservation REST
│   ├── devices.py            #   device list/start-stop/input/files/apps/logcat
│   ├── streams.py            #   stream config/adaptive/snapshot
│   ├── webrtc.py             #   WebRTC signaling (offer/ice/close)
│   └── sync.py               #   🧪 Lab: sync-group CRUD + u2 introspection / semantic preview
├── services/                 # business logic
│   ├── database.py           #   SQLite connection, schema, seed accounts
│   ├── authentication.py     #   password hashing, session tokens, role decorators
│   ├── validators.py         #   ★reusable: username/email/password rules (single source)
│   ├── rate_limit.py         #   ★reusable: RateLimiter + rate_limit decorator (anti-brute-force)
│   ├── reservations.py       #   occupancy: claim/release/sweep/assert_owner
│   ├── adb_bootstrap.py      #   bundled-adb extraction + PATH injection (cross-platform)
│   ├── webrtc_session.py     #   WebRTC session orchestration
│   ├── quality_controller.py #   adaptive anti-mosaic control
│   ├── sync/                 # 🧪 Lab: multi-device master→slave sync (ENABLE_SYNC kill-switch)
│   │   ├── sync_groups.py    #   in-memory group manager (no DB)
│   │   ├── sync_dispatcher.py#   fan-out + coordinate mapping + throttling + worker pool
│   │   ├── semantic_event_builder.py # u2 control reverse-lookup + selector generation
│   │   ├── u2_pool.py        #   u2 device connection pool + per-serial RLock
│   │   ├── u2_executor.py    #   u2 semantic click / text (with fallback)
│   │   ├── failure_collector.py # failure evidence (PNG/XML/JSON) + age-based cleanup
│   │   └── lifecycle.py      #   release/offline hooks → dissolve / shrink groups
│   └── ...                   #   device info / files / uploads / logs / input ...
├── scrcpy/                   # trimmed scrcpy client port
│   ├── scrcpyclients.py / scrcpycore.py / scrcpycontrol.py
│   ├── scrcpyconst.py / scrcpynetwork.py
│   └── webrtc/               #   aiortc pipeline (peer_manager / video_track / ...)
├── terminal/                 # embedded ADB terminal (shell:v2 over socket)
├── frontend/                 # Vue 3 + Vite (build output in dist/, served by Flask)
│   └── src/
│       ├── components/       #   LoginView / AdminPanel / ThemeToggle / DeviceCard ...
│       ├── composables/      #   useAuth / useReservations / useValidators / useAppContext ...
│       └── locales/          #   en / zh-CN / zh-TW
├── scripts/
│   └── init_db.py            # DB tool: init / clear / reset / status / backup
├── config/
│   └── config.py             # single source of truth for all tunables (flags / port / DB / sync params)
├── resources/
│   ├── re_adb/               #   adb archives
│   ├── runpath/              #   adb extracted on first boot
│   ├── scrcpy/               #   scrcpy-server.jar
│   └── uiautomator/          #   uiautomator helper APKs (Lab / semantic sync; optional)
├── tests/                    # pytest white-box tests (incl. test_sync_chain.py — full sync chain)
├── logs/                     # application logs + sync.log (sync stream, rotating)
└── data/
    ├── app.db                # SQLite database (accounts/sessions/reservations)
    └── sync_failures/        # sync failure evidence (PNG/XML/JSON, age-based cleanup)
```

## Features

### Mirroring & control
- Low-latency WebRTC mirroring (H.264); adaptive single-stage and multi-device grid views
- Touch, swipe, keys, text input, rotation, screen on/off, quick keys

### Device ops
- File browser (CRUD, up/download), app management (list/uninstall/export APK)
- Live logcat (SSE stream), device info panel
- Embedded ADB terminal (xterm.js, real PTY)

### Accounts & reservations
- Three roles: super admin / admin / user; users self-register
- Reservations: claim for a duration, self-release, auto-expiry, admin force-release
- Admin panel: create / edit email / reset password / change role / enable-disable / delete users; reservation overview

### Experience
- Light / dark theme (shared toggle component on login and main UI)
- Trilingual: Simplified / Traditional Chinese, English
- Stream tuning and adaptive anti-mosaic

### Lab · Multi-device master→slave sync
> Sidebar "Lab" entry; pluggable kill-switch — when off, completely decoupled from the main path.
- **Master→slave sync**: pick 1 master + N slaves; operating the master mirrors to every slave
- **Two modes**:
  - **Coordinate** (V1): proportional coordinate mirroring; auto-handles cross-resolution; ms-level latency
  - **Semantic** (V2, via uiautomator2): reverse-look-up the master's tap to a control (resourceId/text/xpath), slaves locate that same control and click; falls back to coordinates on miss
- **Tap vs. drag detection**: a press → semantic control click; a drag (displacement ≥ 24 px) → coordinate-swipe mirror
- **Quick keys / directional swipes / text input** all fan out to slaves too (semantic mode types text via u2 `send_keys`, falling back to scrcpy text injection)
- **Reservation exception**: creating a group batch-reserves the master + all slaves (the single bypass of the "one-device-per-user" anti-hogging rule); stopping the group releases the slaves while keeping the master
- **Lifecycle hooks**: master released/offline → group auto-dissolves and slaves are released; slave released/offline → automatically removed from the group
- **Multi-user concurrency safety**: per-serial RLock on u2 calls; workers re-validate "group exists + still a member + still owned by group master" before touching a slave, closing the device-ownership-changed race
- **Stability**: semantic tap single-flight + 250 ms throttle (prevents dump storms); slave execution thread pool (default 8 concurrent); failure evidence (screenshot + UI XML + JSON detail) dropped under `data/sync_failures/`, cleaned by age
- **Frontend dashboard**: two-column layout in the Lab page (centered panel before a group; panel + live previews after), with per-slave execution results live-coloured (green = semantic ok / yellow = fallback / red = failed / grey = offline)
- **File logs, not DB**: execution stream written to `logs/sync.log` via `RotatingFileHandler` (2 MB × 3 rotation); no schema added to the main DB

## Tech Stack

**Backend**: Python 3.10+ (developed on 3.13), Flask 3.1 + Flask-SocketIO 5.5 (`async_mode="threading"`), aiortc 1.14 + av + opencv-python, adbutils, Werkzeug (password hashing), SQLite (accounts / sessions / reservations — single file, no external deps).

**Frontend**: Vue 3 + Vite 6 SPA, socket.io-client, @xterm/xterm; component-based with composables, self-managed i18n / theme.

## Quick Start

### Requirements
- Python 3.10+
- Node.js 18+ (to build/dev the frontend)
- adb: optional — if absent, the matching platform-tools is auto-extracted from `resources/re_adb/`

### Install

```bash
# 1) Backend deps (use a venv)
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
pip install -r requirements.txt

# 2) Frontend deps & build
cd frontend
npm install
npm run build        # produces dist/, served by Flask
cd ..

# 3) Initialize the database (required on first run)
python scripts/init_db.py init
```

### Run

```bash
# Option A: one-click launcher (backend 5001 + Vite 5173, opens browser)
python start_dev.py
#   python start_dev.py stop                  # only free the ports
#   python start_dev.py start --local-only     # bind 127.0.0.1 only (no LAN exposure)
#   python start_dev.py start --no-browser     # don't auto-open the browser

# Option B: backend only (serves the prebuilt frontend dist)
python app.py
```

Open `http://localhost:5173` (dev) or `http://localhost:5001` (backend only) and sign in with a seed account.

### Backend / frontend commands

**Backend** (repo root, with the venv activated):

```bash
python app.py                  # start the backend (serves the prebuilt frontend dist), default :5001
python start_dev.py            # one-click: backend (:5001) + frontend Vite (:5173) together
python scripts/init_db.py init # DB tool: init / status / clear / reset / backup
python -m pytest tests/ -q     # run the backend white-box tests
```

**Frontend** (in `frontend/`):

```bash
npm install         # install deps
npm run dev         # Vite dev server (:5173, HMR, proxies /api and /socket.io to :5001)
npm run build       # production build → dist/ (served by Flask)
npm run preview     # preview the production build locally
npm run test        # vitest unit tests (npm run test:watch for watch mode)
npm run check:i18n  # verify the three locale bundles have matching keys
npm run format      # Prettier (npm run format:check to only check)
```

> For development, prefer `python start_dev.py`: it launches the backend and Vite together with frontend HMR (backend code changes need a restart). Use `python app.py` to just serve the prebuilt `dist/` for deploy/demo.

### Seed accounts

| Role        | Username     | Password        | Notes                                                      |
|-------------|--------------|-----------------|------------------------------------------------------------|
| Super admin | `superadmin` | `superadmin123` | Break-glass; cannot be deleted/disabled                    |
| Admin       | `admin`      | `admin123`      | Manages users, force-releases devices                      |
| User        | `user1`      | `User123456`    | Sample standard user; or self-register on the sign-up page |

> **Self-registration policy**: username **3–32 chars, starting with a letter or digit** (only letters / digits / `.` `_` `-`); password **8–128 chars containing both letters and digits**. Rules are shared front and back (`services/validators.py` ↔ `frontend/src/composables/useValidators.js`).

> ⚠️ **Change the seed passwords before real use** (Change Password after login, or admin reset).

## Configuration

**Single source of truth: `config/config.py`**. Every tunable lives in this one file; other modules only read from it. Each setting also honours an optional environment variable of the same name as a deploy-time override (when unset, the literal default in the file is used). No `.env` file is needed.

Key settings:

| Setting                          | Default                          | Description                                                                            |
|----------------------------------|----------------------------------|----------------------------------------------------------------------------------------|
| `ENABLE_SYNC`                    | `True`                           | 🧪 Lab: multi-device sync master switch (off → blueprint not registered, fan-out skipped, UI entry hidden) |
| `ENABLE_WEBRTC`                  | `True`                           | WebRTC signaling toggle                                                                |
| `ENABLE_TERMINAL`                | `True`                           | Embedded terminal toggle                                                               |
| `OPEN_BROWSER`                   | `True`                           | Auto-open browser on startup                                                           |
| `HOST` / `PORT`                  | LAN IP / `5001`                  | Backend bind address / port                                                            |
| `FLASK_SECRET_KEY`               | random on boot                   | Session cookie signing key; pin in production or every restart invalidates sessions    |
| `DB_PATH`                        | `data/app.db`                    | SQLite database path                                                                   |
| `LOG_LEVEL`                      | `INFO`                           | Log level                                                                              |
| `SESSION_TTL_HOURS`              | `24`                             | Session validity                                                                       |
| `RESERVATION_*`                  | `240 / 60 / 30 / 10`             | Max / default minutes, grace seconds, sweeper interval seconds                          |
| `SCRCPY_SERVER_PATH` / `_VERSION` | `resources/scrcpy/...` / `4.0`   | scrcpy-server jar path and version string                                              |
| `U2_WAIT_TIMEOUT`                | `1.5`                            | 🧪 u2 selector wait timeout (seconds)                                                  |
| `SYNC_TAP_THROTTLE_MS`           | `250`                            | 🧪 Semantic-tap throttle interval (anti dump-storm)                                    |
| `SYNC_MAX_CONCURRENCY`           | `8`                              | 🧪 Concurrent slave fan-out cap                                                        |
| `SYNC_LOG_PATH/MAX_BYTES/BACKUP_COUNT` | `logs/sync.log` / 2 MB / 3 | 🧪 Sync stream log (rotating)                                                          |
| `SYNC_FAILURE_CAPTURE`           | `True`                           | 🧪 Whether to capture screenshot + UI XML + JSON on total failure                      |
| `SYNC_FAILURE_DIR`               | `data/sync_failures`             | 🧪 Evidence directory                                                                  |
| `SYNC_FAILURE_RETENTION_DAYS`    | `14`                             | 🧪 Evidence retention days (cleanup triggered off capture, throttled hourly)           |
| `SYNC_SWIPE_MIN_PX` / `SYNC_SWIPE_DEFAULT_MS` | `24` / `200`        | 🧪 Min displacement to count as a swipe in semantic mode / default swipe duration       |

## Security Design

> Login/registration rules follow OWASP ASVS and Django validator practices.

| Area                          | Implementation                                                                                                                                                                                                     |
|-------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **SQL injection**             | All queries parameterized (`?`); the one dynamic `UPDATE` interpolates only code-fixed column fragments, values stay bound                                                                                         |
| **Password storage**          | Werkzeug `generate_password_hash` + a per-user random salt; never stored in plaintext                                                                                                                              |
| **Sessions**                  | Tokens (`secrets.token_urlsafe`) stored in `user_sessions`; the cookie carries only an opaque token; revocable by admin / on password change; wiped on boot to force re-login                                      |
| **Login gate**                | A `before_request` hook in `app.py` guards all `/api/`, exempting only login/register/check-auth                                                                                                                   |
| **Brute force**               | `services/rate_limit.py` failure counting: after N consecutive failures per `ip+username` the pair is locked for a window (default in `api/authentication.py`, tunable); success resets the counter                |
| **Input validation**          | `services/validators.py` single source, mirrored by the frontend `useValidators`: username 3–32 chars starting alphanumeric (allow-list), email length-capped + lowercased, password 8–128 with letters and digits |
| **Anti-enumeration**          | Failed login always returns a generic "wrong username or password"; never reveals whether an account exists                                                                                                        |
| **XSS defense-in-depth**      | The username allow-list blocks spaces/control/HTML chars; Vue auto-escapes output                                                                                                                                  |
| **Three-plane authorization** | Device control is checked for reservation ownership on HTTP, WebRTC and terminal alike                                                                                                                             |

## Usage

1. **Sign in / register**: open the app to reach the login page; new users click "Register" (subject to the password policy).
2. **Reserve a device**: in the stage's left "Reservation" card, pick a duration and claim; control requires holding the reservation.
3. **Mirror & control**: click "Start" on a device card to begin mirroring; touch, input, drag-and-drop files, clipboard, terminal, etc.
4. **Release**: release any time; reservations also auto-expire.
5. **Admin**: the "Admin" button (top-right) opens the panel — user management (create / edit email / reset password / change role / enable-disable / delete) and a reservation overview (force-release).
6. **🧪 Multi-device sync (Lab)**: left-rail "Lab" entry (requires `ENABLE_SYNC=True`) — pick 1 master + N slaves → choose mode (semantic / coordinate) → "Start sync". On group creation, all members are batch-reserved and started; the right column then shows live previews with per-slave result colours (green = semantic ok / yellow = fallback / red = failed). Stopping the group releases the slaves and keeps the master.

## API Reference

### Auth & users (`/api/auth/*`)
| Method | Path                          | Access | Description                                        |
|--------|-------------------------------|--------|----------------------------------------------------|
| POST   | `/api/auth/register`          | public | Register (role forced to user)                     |
| POST   | `/api/auth/login`             | public | Login (with failure lockout)                       |
| POST   | `/api/auth/logout`            | auth   | Logout                                             |
| GET    | `/api/auth/check-auth`        | public | Auth status                                        |
| GET    | `/api/auth/profile`           | auth   | Current user                                       |
| POST   | `/api/auth/change-password`   | auth   | Change password (revokes all sessions)             |
| GET    | `/api/auth/users`             | admin  | List users (super sees all, admin sees only users) |
| POST   | `/api/auth/users`             | admin  | Create user                                        |
| PUT    | `/api/auth/users/<id>`        | admin  | Edit email / role / password / active              |
| POST   | `/api/auth/users/<id>/active` | admin  | Enable / disable                                   |
| DELETE | `/api/auth/users/<id>`        | admin  | Delete                                             |

### Reservations (`/api/reservations*`)
| Method | Path                            | Description                         |
|--------|---------------------------------|-------------------------------------|
| GET    | `/api/reservations`             | List + max/default minutes          |
| POST   | `/api/reservations`             | Claim a device (device_id, minutes) |
| DELETE | `/api/reservations/<device_id>` | Release (owner or admin force)      |

### Device / stream (`/api/*`, require login + reservation ownership)
`GET /api/devices`, `POST /api/start`, `POST /api/stop`, `POST /api/upload`,
`/api/device/<id>/{info,keyevent,swipe,rotate,logcat,files,apps,...}`,
`/api/{scrcpy-server,stream-config,reconfigure,adaptive,snapshot,active-streams}`.

> Note: when `ENABLE_SYNC=True` and the device is the master of a sync group, `POST /api/device/<id>/keyevent` and `POST /api/device/<id>/swipe` defensively fan out to all slaves after the master itself succeeds (any fan-out error never affects the master's own response).

### 🧪 Lab · Multi-device sync (`/api/sync-groups*`, `/api/devices/<id>/u2/*`, only when `ENABLE_SYNC=True`)

| Method | Path                                       | Access            | Description                                                                                                |
|--------|--------------------------------------------|-------------------|------------------------------------------------------------------------------------------------------------|
| GET    | `/api/sync-groups`                         | auth              | List sync groups (admins see all, users see their own) + live status snapshot                              |
| POST   | `/api/sync-groups`                         | auth              | Create a group: `{master_device_id, slave_device_ids[], mode, sync_touch/keyevent/text}`, batch-reserves master + slaves |
| DELETE | `/api/sync-groups/<group_id>`              | owner / admin     | Dissolve the group, releases the slaves (master stays reserved)                                            |
| POST   | `/api/sync-groups/<group_id>/enable`       | owner / admin     | Enable sync                                                                                                |
| POST   | `/api/sync-groups/<group_id>/disable`      | owner / admin     | Pause sync                                                                                                 |
| POST   | `/api/sync-groups/precheck`                | auth              | Builder pre-flight: `{device_ids[]}` → per-device `online / reserved_by_me / reserved_by_other / in_group` |
| GET    | `/api/devices/<id>/u2/ping`                | reservation owner | uiautomator2 connectivity + window size + current app (first call pushes `u2.jar` to the device)           |
| GET    | `/api/devices/<id>/u2/hierarchy`           | reservation owner | Current UI hierarchy XML (debug)                                                                           |
| POST   | `/api/devices/<id>/u2/inspect`             | reservation owner | `{x,y}` → resolve the control at that point to a selector (resourceId / text / xpath / bounds)             |
| POST   | `/api/devices/<id>/u2/semantic`            | reservation owner | `{x,y}` → preview the semantic event that tap would produce (mode + selector + coordinate fallback)        |

### Socket.IO events
- WebRTC signaling: `webrtc:offer` / `webrtc:ice` / `webrtc:close` (→ `webrtc:answer` / `webrtc:ice` / `webrtc:closed`)
- Terminal: `terminal:open` / `terminal:input` / `terminal:resize` / `terminal:close` (→ `terminal:opened/output/closed/error`)
- Broadcasts: `devices_changed` / `reservation_changed` / `device_released` / `scrcpy_status`

## Database Tool

```bash
python scripts/init_db.py init      # create tables + seed accounts (idempotent)
python scripts/init_db.py status    # show accounts / reservations / file size
python scripts/init_db.py clear     # wipe runtime data (sessions/reservations/registered users), keep seeds
python scripts/init_db.py reset     # drop & recreate, reseed accounts (destructive)
python scripts/init_db.py backup    # timestamped copy of data/app.db
```

## Testing

```bash
# Full white-box suite (pytest)
python -m pytest tests/ -q

# Lab · sync chain white-box test (also runnable directly)
python tests/test_sync_chain.py
```

Coverage: full account chain (register/login/logout/check-auth), password policy & validators, login lockout, role visibility & admin constraints, reservation lifecycle (claim/extend/race/expiry/sweep), three-plane authorization, reservation HTTP routes, and the `init_db` tool.

**`tests/test_sync_chain.py`** mocks device IO at the boundaries (scrcpy control, u2 executor / inspect, adb shell, input_shell, failure capture, reservations) and asserts the full sync chain in 12 cases: coordinate touch ratio mapping / text+key+swipe fan-out, semantic tap success / fallback / total-failure evidence, semantic text success+fallback, keyevent fan-out, directional swipe fan-out, throttle single-flight + interval, revalidate guard (member / non-reserved / owned-by-other), lifecycle master dissolution + slave shrink.

## Deployment

- **Development**: `python start_dev.py` (Werkzeug dev server — dev only).
- **Production**: the bundled server is for development. Use a real WSGI server and separate the aiortc subprocess (see `docs/`). Always set a fixed `FLASK_SECRET_KEY`, change the seed passwords, and limit exposure with `--local-only` or a reverse proxy.

## Cross-Platform Notes

- **Windows / macOS / Linux supported**: `resources/re_adb/` bundles platform-tools for all three; first boot auto-extracts (POSIX restores exec bits, macOS strips the quarantine attribute).
- `start_dev.py` branches per OS: port cleanup (Windows PowerShell/`netstat` vs POSIX `lsof`), process groups (`CREATE_NEW_PROCESS_GROUP` vs `setsid`), venv paths (`Scripts` vs `bin`).
- The embedded terminal uses adb `shell:v2` and needs no host PTY — identical across OSes.

## Troubleshooting

- **Frontend 503 / dist missing**: run `cd frontend && npm run build` first.
- **No devices detected**: confirm `adb devices` lists it and USB debugging is on; bundled adb is auto-extracted on first boot.
- **Re-login required after restart**: with no `FLASK_SECRET_KEY` set (random key changes each restart) plus the boot-time session wipe, this is expected; pin the key to mitigate (sessions are still cleared on boot).
- **Locked out (429)**: repeated failures triggered brute-force lockout — wait out the cooldown shown, or ask an admin.
- **Logs**: see the `logs/` directory.

---

To apply the rate-limit decorator to more endpoints, tune lockout thresholds / reservation durations, or extend role permissions, the rules are already factored into reusable modules (`services/validators.py`, `services/rate_limit.py`) — change them in one place, and it takes effect everywhere.
