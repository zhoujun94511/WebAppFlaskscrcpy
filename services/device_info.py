"""Snapshot of a connected device's static + dynamic info for the UI panel.

All probes go through the existing adbutils singleton — no extra binaries
or network calls. Each probe is independently fault-tolerant: a single
``getprop`` or ``dumpsys`` failure just yields ``None`` for that field
rather than tanking the whole panel.

Returned shape (subset of fields populated depending on what the device
exposes — Samsung locks down some ``dumpsys`` calls, etc.)::

    {
      "device_id": "...",
      "manufacturer": "Google",
      "model": "Pixel 7",
      "android_version": "14",
      "android_codename": "UpsideDownCake",
      "sdk": 34,
      "abi": "arm64-v8a",
      "serial": "...",
      "battery_level": 87,           # percent, 0-100
      "battery_status": "Charging",
      "wifi_ssid": "Home",
      "ip_address": "192.168.1.101",
      "storage_used_bytes": 37_400_000_000,
      "storage_total_bytes": 120_000_000_000,
      "memory_used_bytes": ...,
      "memory_total_bytes": ...,
    }
"""

# cspell:ignore adbutils getprop dumpsys meminfo MemTotal MemAvailable arm64

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from adbutils import adb

_log = logging.getLogger(__name__)


def _shell(device_id: str, cmd: str, timeout: float = 5.0) -> str:
    """Run a shell command via adbutils. Returns '' on failure."""
    try:
        return adb.device(device_id).shell(cmd, timeout=timeout) or ''
    except (RuntimeError, OSError, TimeoutError) as exc:
        _log.debug('shell %r failed on %s: %s', cmd, device_id, exc)
        return ''


def _getprop(device_id: str, key: str) -> str:
    return _shell(device_id, f'getprop {key}').strip()


def _try_int(s: str) -> Optional[int]:
    try:
        return int(s.strip())
    except (ValueError, AttributeError):
        return None


def _parse_battery(dump: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for line in dump.splitlines():
        line = line.strip()
        if line.startswith('level:'):
            out['battery_level'] = _try_int(line.split(':', 1)[1])
        elif line.startswith('status:'):
            code = _try_int(line.split(':', 1)[1])
            out['battery_status'] = {
                1: 'Unknown', 2: 'Charging', 3: 'Discharging',
                4: 'Not charging', 5: 'Full',
            }.get(code, str(code))
        elif line.startswith('temperature:'):
            t = _try_int(line.split(':', 1)[1])
            if t is not None:
                out['battery_temp_c'] = t / 10.0
    return out


_DF_RE = re.compile(r'^\S+\s+(\d+)\s+(\d+)\s+(\d+)\s+\d+%', re.MULTILINE)


def _parse_df(df_output: str) -> Dict[str, Any]:
    """``df -k /data`` → used + total bytes."""
    m = _DF_RE.search(df_output)
    if not m:
        return {}
    total_kb, used_kb, _avail_kb = (int(g) for g in m.groups())
    return {
        'storage_used_bytes': used_kb * 1024,
        'storage_total_bytes': total_kb * 1024,
    }


def _parse_meminfo(mem: str) -> Dict[str, Any]:
    total_kb = avail_kb = None
    for line in mem.splitlines():
        if line.startswith('MemTotal:'):
            total_kb = _try_int(line.split(':', 1)[1].strip().split()[0])
        elif line.startswith('MemAvailable:'):
            avail_kb = _try_int(line.split(':', 1)[1].strip().split()[0])
    if total_kb is None:
        return {}
    out = {'memory_total_bytes': total_kb * 1024}
    if avail_kb is not None:
        out['memory_used_bytes'] = (total_kb - avail_kb) * 1024
    return out


_RESOLUTION_RE = re.compile(r'Physical size:\s*(\d+)x(\d+)')
_OVERRIDE_RESOLUTION_RE = re.compile(r'Override size:\s*(\d+)x(\d+)')
_DENSITY_RE = re.compile(r'Physical density:\s*(\d+)')
_OVERRIDE_DENSITY_RE = re.compile(r'Override density:\s*(\d+)')


def _parse_resolution(wm_size: str) -> Dict[str, Any]:
    """``wm size`` returns physical + optional override (user-resized) dims.
    Prefer override (what the device is actually rendering at) when present."""
    out: Dict[str, Any] = {}
    m = _OVERRIDE_RESOLUTION_RE.search(wm_size) or _RESOLUTION_RE.search(wm_size)
    if m:
        out['resolution_width'] = int(m.group(1))
        out['resolution_height'] = int(m.group(2))
    return out


def _parse_density(wm_density: str) -> Optional[int]:
    m = _OVERRIDE_DENSITY_RE.search(wm_density) or _DENSITY_RE.search(wm_density)
    return int(m.group(1)) if m else None


def _parse_uptime(uptime_proc: str) -> Optional[int]:
    """``/proc/uptime`` is two floats: total seconds + idle seconds. We want
    the first one, rounded to int — frontend formats as days/hours/minutes."""
    parts = uptime_proc.strip().split()
    if not parts:
        return None
    try:
        return int(float(parts[0]))
    except (ValueError, TypeError):
        return None


_IPV4_RE = re.compile(r'inet (\d+\.\d+\.\d+\.\d+)')


def _parse_ip(ip_output: str) -> Optional[str]:
    for line in ip_output.splitlines():
        m = _IPV4_RE.search(line)
        if m:
            ip = m.group(1)
            if not ip.startswith('127.'):
                return ip
    return None


_SSID_RE = re.compile(r'mWifiInfo SSID:\s*"?([^"\n,]+)"?')


def _parse_wifi_ssid(dump: str) -> Optional[str]:
    m = _SSID_RE.search(dump)
    if m:
        ssid = m.group(1).strip()
        return ssid if ssid and ssid != '<unknown ssid>' else None
    return None


# Marker we insert between probe outputs to split them on the client side.
# Picked something extremely unlikely to appear in any legitimate getprop
# / dumpsys output (no shell metacharacters, no Android tags, no real
# property name pattern). If a future Android release ever did emit this
# string, the split would silently mis-attribute one section to the next —
# vanishingly unlikely but worth a sanity check at parse time.
_PROBE_BREAK = '###=====SCRCPY_INFO_BREAK=====###'

# Probe script — runs ALL data gathering in a single adb shell round-trip
# instead of the original 22-25 serial calls. On the wire, each adb shell
# invocation has ~30-100 ms of fixed cost (USB / TCP latency + transport
# framing) so 22 of them serialised pushed the panel load to ~660 ms.
# Bundled, the round-trip cost is paid ONCE; the device's own shell does
# the work back-to-back at native speed, dropping the panel load to
# ~80-150 ms.
#
# Order matters here only for the consumer below — sections are split on
# ``_PROBE_BREAK`` and assigned by index. Output that fails (locked-down
# dumpsys on some OEMs, missing ``ip`` binary, etc.) just yields an empty
# section; the corresponding parser tolerates that and the field is
# omitted from the response. Each command is followed by ``2>/dev/null``
# (where applicable) so stderr noise doesn't contaminate the section.
_PROBE_SCRIPT = (
    'getprop 2>/dev/null;'
    f' echo "{_PROBE_BREAK}";'
    ' dumpsys battery 2>/dev/null;'
    f' echo "{_PROBE_BREAK}";'
    ' df -k /data 2>/dev/null;'
    f' echo "{_PROBE_BREAK}";'
    ' cat /proc/meminfo 2>/dev/null;'
    f' echo "{_PROBE_BREAK}";'
    ' { ip addr show wlan0 2>/dev/null || ip addr 2>/dev/null; };'
    f' echo "{_PROBE_BREAK}";'
    ' dumpsys wifi 2>/dev/null;'
    f' echo "{_PROBE_BREAK}";'
    ' wm size 2>/dev/null;'
    f' echo "{_PROBE_BREAK}";'
    ' wm density 2>/dev/null;'
    f' echo "{_PROBE_BREAK}";'
    ' cat /proc/uptime 2>/dev/null'
)

# Mapping of getprop key → field name we put on the response dict. Kept
# here so the parser stays declarative — adding a new prop is one line.
_GETPROP_FIELDS = {
    'ro.product.manufacturer': 'manufacturer',
    'ro.product.brand': '_brand_raw',  # processed below (drops if == manufacturer)
    'ro.product.model': 'model',
    'ro.build.version.release': 'android_version',
    'ro.build.version.codename': '_codename_raw',  # drops if 'REL'
    'ro.build.version.sdk': '_sdk_raw',  # converted to int
    'ro.product.cpu.abi': 'abi',
    'ro.serialno': 'serial',
    'ro.soc.manufacturer': 'soc_manufacturer',
    'ro.soc.model': '_soc_model_primary',
    'ro.boot.hardware.platform': '_soc_model_qualcomm_fallback',
    'ro.boot.hardware': '_soc_model_mtk_fallback',
    'ro.build.id': 'build_id',
}

_GETPROP_LINE_RE = re.compile(r'^\[([^]]+)]:\s*\[([^]]*)]\s*$')


def _parse_all_props(raw: str) -> Dict[str, str]:
    """Parse ``getprop`` (no args) output into a dict of key → value.

    Output format is ``[key]: [value]`` per line. We only keep keys
    listed in :data:`_GETPROP_FIELDS` — props dump has ~1000 entries,
    but we care about ~13. Skipping the rest at parse time keeps the
    dict small and the downstream code obvious.
    """
    wanted = set(_GETPROP_FIELDS.keys())
    out: Dict[str, str] = {}
    for line in raw.splitlines():
        m = _GETPROP_LINE_RE.match(line)
        if not m:
            continue
        key = m.group(1)
        if key in wanted:
            out[key] = m.group(2).strip()
    return out


def collect(device_id: str) -> Dict[str, Any]:
    """Gather a best-effort device info snapshot.

    Always returns a dict with ``device_id``; missing fields just don't
    appear (frontend renders dashes for them). All probes are bundled
    into one adb shell round-trip (see :data:`_PROBE_SCRIPT`) — earlier
    versions ran 22+ serial shell commands which dominated panel load
    time.
    """
    info: Dict[str, Any] = {'device_id': device_id}

    raw = _shell(device_id, _PROBE_SCRIPT, timeout=10.0)
    sections = raw.split(_PROBE_BREAK)
    # Expected 9 sections; if the shell returned fewer (very old ROM that
    # doesn't support multi-statement strings? probe binary missing?),
    # pad with empty strings so index-based access below is safe.
    while len(sections) < 9:
        sections.append('')
    props_raw, battery_raw, df_raw, mem_raw, ip_raw, wifi_raw, wmsize_raw, wmdensity_raw, uptime_raw = sections[:9]

    # ── Properties ──────────────────────────────────────────────
    props = _parse_all_props(props_raw)

    manufacturer = props.get('ro.product.manufacturer', '')
    if manufacturer:
        info['manufacturer'] = manufacturer
    # Brand can differ from manufacturer for sub-brands (e.g. POCO under
    # Xiaomi, Redmi under Xiaomi, Pixel under Google). Only include when
    # it's actually distinct — otherwise it duplicates the manufacturer
    # row in the UI.
    brand = props.get('ro.product.brand', '')
    if brand and brand.lower() != manufacturer.lower():
        info['brand'] = brand
    model = props.get('ro.product.model', '')
    if model:
        info['model'] = model
    android_release = props.get('ro.build.version.release', '')
    if android_release:
        info['android_version'] = android_release
    codename = props.get('ro.build.version.codename', '')
    if codename and codename != 'REL':
        info['android_codename'] = codename
    sdk = _try_int(props.get('ro.build.version.sdk', ''))
    if sdk is not None:
        info['sdk'] = sdk
    abi = props.get('ro.product.cpu.abi', '')
    if abi:
        info['abi'] = abi
    serial = props.get('ro.serialno', '')
    if serial:
        info['serial'] = serial

    # SoC info — chipset vendor + model. Android 11+ exposes both as
    # standard properties; pre-11 devices need vendor-specific fallbacks
    # (Qualcomm: ro.boot.hardware.platform, MTK: ro.boot.hardware).
    soc_mfr = props.get('ro.soc.manufacturer', '')
    if soc_mfr:
        info['soc_manufacturer'] = soc_mfr
    soc_model = (
        props.get('ro.soc.model', '')
        or props.get('ro.boot.hardware.platform', '')
        or props.get('ro.boot.hardware', '')
    )
    if soc_model:
        info['soc_model'] = soc_model

    build_id = props.get('ro.build.id', '')
    if build_id:
        info['build_id'] = build_id

    # ── Battery / Storage / Memory ──────────────────────────────
    info.update(_parse_battery(battery_raw))
    info.update(_parse_df(df_raw))
    info.update(_parse_meminfo(mem_raw))

    # ── Network ─────────────────────────────────────────────────
    ip = _parse_ip(ip_raw)
    if ip:
        info['ip_address'] = ip
    ssid = _parse_wifi_ssid(wifi_raw)
    if ssid:
        info['wifi_ssid'] = ssid

    # ── Display ─────────────────────────────────────────────────
    info.update(_parse_resolution(wmsize_raw))
    density = _parse_density(wmdensity_raw)
    if density is not None:
        info['density_dpi'] = density

    # ── Uptime ──────────────────────────────────────────────────
    uptime = _parse_uptime(uptime_raw)
    if uptime is not None:
        info['uptime_seconds'] = uptime

    # ── Connection type ─────────────────────────────────────────
    # adbutils gives us the serial, and TCP serials look like
    # ``192.168.x.x:5555``. USB serials are bare alphanumerics.
    info['connection_type'] = 'wifi' if ':' in device_id else 'usb'

    return info
