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
│   └── webrtc.py             #   WebRTC signaling (offer/ice/close)
├── services/                 # business logic
│   ├── database.py           #   SQLite connection, schema, seed accounts
│   ├── authentication.py     #   password hashing, session tokens, role decorators
│   ├── validators.py         #   ★reusable: username/email/password rules (single source)
│   ├── rate_limit.py         #   ★reusable: RateLimiter + rate_limit decorator (anti-brute-force)
│   ├── reservations.py       #   occupancy: claim/release/sweep/assert_owner
│   ├── adb_bootstrap.py      #   bundled-adb extraction + PATH injection (cross-platform)
│   ├── webrtc_session.py     #   WebRTC session orchestration
│   ├── quality_controller.py #   adaptive anti-mosaic control
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
├── resources/
│   ├── re_adb/               #   The execution path for ADB after extraction
│   └── runpath/              #   adb extracted on first boot
├── tests/                    # pytest white-box tests
└── data/                     # SQLite database (app.db)
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

Set via environment variables before launch:

| Variable           | Default | Description                                                                            |
|--------------------|---------|----------------------------------------------------------------------------------------|
| `FLASK_SECRET_KEY` | random  | Session cookie signing key; pin it in production or every restart invalidates sessions |
| `HOST`             | LAN IP  | Backend bind address (`app.py`)                                                        |
| `PORT`             | `5001`  | Backend port                                                                           |
| `OPEN_BROWSER`     | `1`     | Auto-open browser (`0` to disable)                                                     |
| `ENABLE_WEBRTC`    | `1`     | WebRTC signaling kill-switch (`0` to force off)                                        |
| `ENABLE_TERMINAL`  | `1`     | Embedded terminal toggle                                                               |

Session TTL, max reservation minutes, etc. live as code constants (`services/authentication.py`, `services/reservations.py`).

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
```

Coverage: full account chain (register/login/logout/check-auth), password policy & validators, login lockout, role visibility & admin constraints, reservation lifecycle (claim/extend/race/expiry/sweep), three-plane authorization, reservation HTTP routes, and the `init_db` tool.

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
