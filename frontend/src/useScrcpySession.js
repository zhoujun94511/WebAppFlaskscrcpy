import {
  computed,
  onBeforeUnmount,
  onMounted,
  ref,
  shallowReactive,
  watch,
} from "vue";
import { io } from "socket.io-client";
import { useWebRTC } from "./composables/useWebRTC";
import { startStreamQuality } from "./composables/useStreamQuality";

// One-shot per JS module load. Survives Vue re-mounts within the same
// page (HMR resets the module → new BOOT_ID, which is exactly the
// signal we want to see), but stays constant across component remounts
// inside one full page load. Used to correlate frontend boot ↔ backend
// log lines so we can tell apart:
//   * F5 refresh (new module load, new BOOT_ID)
//   * HMR module replacement (also a new module load → new BOOT_ID)
//   * Component re-mount (same module instance → same BOOT_ID)
//   * Multiple browser tabs (each tab has its own JS realm → different BOOT_IDs)
export const BOOT_ID = (() => {
  try {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  } catch { /* ignore */ }
  return `boot-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
})();
// BOOT_ID is still used to tag start requests, but we no longer emit
// boot-timing console logs in normal operation.

// ── Boot-timing instrumentation ────────────────────────────────
// Tagged log helper that prints "T+<ms> [stage] msg" so the user can
// see exactly when each milestone fires after F5. Anchored at
// ``performance.timeOrigin`` (~ page navigation start), so the first
// number is "time since you hit refresh".
//
// Disable with ``localStorage.setItem("scrcpy:bootTrace", "0")`` once
// you've tuned things to the point you don't want the spam anymore.
// Boot-timing console instrumentation removed after validation.

// Persisted across page reloads — devices the user had streaming last
// session get auto-restarted as soon as the socket reconnects. Removed
// from the set when the user explicitly stops a device.
const STREAMING_STORAGE_KEY = "webapp-flaskscrcpy-streaming";

function loadPersistedStreams() {
  try {
    const raw = window.localStorage?.getItem(STREAMING_STORAGE_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function savePersistedStreams(ids) {
  try {
    window.localStorage?.setItem(
      STREAMING_STORAGE_KEY,
      JSON.stringify([...new Set(ids)]),
    );
  } catch {
    /* quota/privacy errors — best-effort */
  }
}

// Shape an outbound input event for the WebRTC ``input`` DataChannel
// (consumed by services/scrcpy_input.py::dispatch).
function toDataChannelEvent(type, payload) {
  if (type === "touch")
    return { t: "touch", x: payload.x, y: payload.y, action: payload.action };
  if (type === "scroll")
    return {
      t: "scroll",
      x: payload.x,
      y: payload.y,
      h: payload.h,
      v: payload.v,
    };
  if (type === "key")
    return { t: "key", code: payload.keycode, action: payload.action };
  if (type === "swipe") {
    const [sx, sy] = payload.start || [];
    const [ex, ey] = payload.end || [];
    return {
      t: "swipe",
      startX: sx,
      startY: sy,
      endX: ex,
      endY: ey,
      duration: payload.duration,
    };
  }
  if (type === "text") return { t: "text", text: payload.text };
  if (type === "set_power_mode") return { t: "power", mode: payload.mode ?? 2 };
  if (type === "expand_notification") return { t: "expandNotification" };
  if (type === "expand_settings") return { t: "expandSettings" };
  if (type === "collapse_panels") return { t: "collapse" };
  if (type === "rotate_device") return { t: "rotate" };
  if (type === "request_keyframe")
    return { t: "request_keyframe", reason: String(payload?.reason || "") };
  if (type === "clipboard_get") return { t: "clipboard.get" };
  if (type === "clipboard_set")
    return { t: "clipboard.set", text: payload.text, paste: payload.paste };
  return null;
}

export function useScrcpySession() {
  const socket = ref(null);
  const socketConnected = ref(false);
  const rtc = useWebRTC({ socket });
  const webrtcEnabled = ref(true);

  // ────────────────────────────────────────────────────────────────────
  // Global tab-scoped state
  // ────────────────────────────────────────────────────────────────────
  const devices = ref([]);
  // Friendly "Brand Model" lookup keyed by serial. Populated from
  // /api/devices response + every `devices_changed` socket broadcast.
  const deviceNames = ref({});
  // Static screen geometry keyed by serial: ``{ serial: { width, height } }``.
  // Pre-populated from ``/api/devices`` so the UI knows each device's true
  // aspect ratio BEFORE any scrcpy stream exists. Cleared per-device on
  // disconnect. The live ``<video>``'s resolutionWidth/Height (per-session
  // ref) takes priority once streaming starts — that one reflects rotation
  // and any user-applied wm-size override; this one is the static fallback.
  const deviceGeometry = ref({});
  const selectedDeviceId = ref(""); // highlighted in nav (drawer-driven)
  const subscribedDeviceId = ref(""); // "most recently started" — drives the
  //   summary chip in the settings page
  const activeDeviceIds = rtc.activeDeviceIds; // ← reactive list of streamed devices

  function nameOf(deviceId) {
    return deviceNames.value[deviceId] || deviceId;
  }

  // Upload/clipboard status is *currently global* — only one upload at a
  // time, only one clipboard ack chip visible. Could become per-device
  // later; the per-device session below already tracks the destination.
  //
  // The status TEXT is stored as an i18n key + params pair so the composable
  // stays locale-agnostic; consumers (StatusStrip) resolve via t() at render
  // time. ``literal`` is used for unrecoverable raw error messages (typically
  // ``error.message``) where we have no i18n key — those bypass translation.
  const uploadBusy = ref(false);
  const uploadMessage = ref({ key: "upload.ready", params: {} });
  const uploadMessageClass = ref("subtle");
  const clipboardAckStatus = ref({ key: "clip.idle", params: {} });
  const clipboardAckClass = ref("subtle");
  const serverHealthText = ref({ key: "server.noDeviceSelected", params: {} });
  const serverHealthClass = ref("subtle");

  const audioMuted = ref(true); // audio is currently not piped at
  const audioState = ref("disabled"); //   the track level — see notes below

  const bindingSearchText = ref("");
  const bindingActiveGroup = ref("all");
  const showBindingEditor = ref(false);

  // ────────────────────────────────────────────────────────────────────
  // Per-device sessions
  // ────────────────────────────────────────────────────────────────────
  // shallowReactive so adding / removing a key triggers reactivity, but
  // each value (the session object) keeps a stable identity — internal
  // refs stay live across re-renders.
  const sessions = shallowReactive(new Map());

  function makeSession(deviceId) {
    // Removed fields (kept here as a tombstone so future code doesn't
    // resurrect them by accident):
    //   rotation / flipHorizontal / flipVertical — old client-side "fake
    //     rotation" state that pretended to rotate the video but only
    //     remapped input coords, leading to taps landing in the wrong
    //     place. Rotation is now real: POST /device/<id>/rotate writes
    //     Settings.System.user_rotation, the device physically rotates,
    //     scrcpy re-negotiates SPS/PPS, and resolutionWidth/Height auto-
    //     update from <video>.loadedmetadata. No client transform.
    //   fpsSamples / showFpsCounter — never wired to any UI.
    //   displayMode — kept; the keypad still exposes "set-display-mode"
    //     for fit / pixel / window although no current visible UI uses
    //     it. Cheap to keep, gnarly to thread out everywhere.
    return {
      deviceId,
      frameState: ref("idle"), // idle | loading | ready
      paused: ref(false),
      resolutionWidth: ref(0),
      resolutionHeight: ref(0),
      displayMode: ref("fit"),
      textValue: ref(""),
      clipboardContent: ref(""),
    };
  }

  function getSession(deviceId) {
    // Write-path accessor: creates a session lazily so callers that
    // are about to *populate* state (startDevice, sendKey, etc.) don't
    // need to special-case missing entries. Templates / read-only
    // call sites MUST use ``peekSession`` instead — calling
    // getSession from a template can resurrect a session entry for a
    // device id that ``handleDevicesChanged`` already removed (the
    // template re-evaluates and sees no entry, so getSession recreates
    // one for a device that no longer exists). The resurrected entry
    // never gets cleaned up because the device never reappears.
    if (!deviceId) return null;
    let s = sessions.get(deviceId);
    if (!s) {
      s = makeSession(deviceId);
      sessions.set(deviceId, s);
    }
    return s;
  }

  function peekSession(deviceId) {
    // Read-only counterpart to ``getSession``. Returns null when no
    // session exists rather than creating one. Use everywhere a
    // missing session is a valid state (template bindings, status
    // helpers) — never use ``getSession`` in those places.
    if (!deviceId) return null;
    return sessions.get(deviceId) || null;
  }

  function hasSession(deviceId) {
    return sessions.has(deviceId);
  }

  function removeSession(deviceId) {
    sessions.delete(deviceId);
  }

  // Convenience getters for components that don't want to drill into refs.
  function videoStreamOf(deviceId) {
    return rtc.getPeer(deviceId)?.mediaStream.value || null;
  }

  function isStreaming(deviceId) {
    const s = sessions.get(deviceId);
    return Boolean(s && s.frameState.value === "ready");
  }

  // ── Adaptive anti-mosaic monitors ──────────────────────────────────
  // One monitor per device. Polls inbound-rtp stats once per second
  // looking for the three classic mosaic signals (frame stall / drop
  // burst / browser-reported freeze) and asks the backend to force a
  // fresh IDR via scrcpy's TYPE_RESET_VIDEO opcode (services/
  // quality_controller.py L1).
  //
  // History — two pass at default-on, both ended back off:
  //   1. L1 reused ``scrcpy.clients.reconfigure``, tearing down every
  //      PeerConnection on every freeze signal → feedback-loop hell.
  //   2. Switched L1 to scrcpy's TYPE_RESET_VIDEO (opcode 17) so the
  //      reset wouldn't tear down the PC; that fixed the loop but
  //      real-device testing showed "Video header looks bogus" log
  //      warnings at roughly the L1 cooldown cadence — the bundled
  //      jar's encoder reset isn't fully compatible with our wire
  //      parser, and each reset hard-drops the video buffer.
  // Until we can ship a jar with a verified RESET_VIDEO contract,
  // the monitor stays off; the baseline tuning in scrcpy/clients.py
  // (16 Mbps, 60 fps cap, 2 s IDR cadence) handles most cases.
  const ADAPTIVE_QUALITY_ENABLED = false;
  const qualityMonitors = new Map();
  function attachQualityMonitor(deviceId) {
    if (!ADAPTIVE_QUALITY_ENABLED) return;
    if (qualityMonitors.has(deviceId)) return;
    const monitor = startStreamQuality({
      getPc: () => rtc.getPeer(deviceId)?.pc || null,
      isStreaming: () => isStreaming(deviceId),
      // Return whether the reset opcode actually made it onto the
      // wire. ``emitTo`` returns false when the input DataChannel
      // hasn't opened yet, the PC isn't connected, etc. — those drops
      // shouldn't burn the monitor's 4 s local cooldown.
      onTrigger: (reason) =>
        emitTo(deviceId, "request_keyframe", { reason }),
    });
    qualityMonitors.set(deviceId, monitor);
  }
  function detachQualityMonitor(deviceId) {
    const m = qualityMonitors.get(deviceId);
    if (m) {
      m.stop();
      qualityMonitors.delete(deviceId);
    }
  }

  // ────────────────────────────────────────────────────────────────────
  // Status helpers
  // ────────────────────────────────────────────────────────────────────
  // Status setters accept an i18n key (plus optional params) instead of a
  // pre-translated string. Pass ``{ literal: '...' }`` as the third arg to
  // emit a raw untranslated message (used for raw error.message strings
  // where we don't have a key).
  function setServerHealth(state, key, params) {
    serverHealthClass.value = state;
    serverHealthText.value = _statusMsg(key, params);
  }

  function setClipboardAck(state, key, params) {
    clipboardAckClass.value = state;
    clipboardAckStatus.value = _statusMsg(key, params);
  }

  function setUploadMessage(state, key, params) {
    uploadMessageClass.value = state;
    uploadMessage.value = _statusMsg(key, params);
  }

  function _statusMsg(key, params) {
    if (params && typeof params === "object" && "literal" in params) {
      return { literal: String(params.literal) };
    }
    return { key, params: params || {} };
  }

  function resetSession(s) {
    if (!s) return;
    s.paused.value = false;
    s.frameState.value = "idle";
    s.resolutionWidth.value = 0;
    s.resolutionHeight.value = 0;
    // fpsSamples was removed from the session shape during the dead-code
    // cleanup pass — nothing consumed it. Don't reintroduce it without a
    // real consumer first.
    // Tear down the per-device quality monitor too: with no stream
    // there's nothing meaningful to poll, and we don't want the next
    // start to inherit a stale ``prev`` snapshot.
    if (s.deviceId) detachQualityMonitor(s.deviceId);
  }

  // ────────────────────────────────────────────────────────────────────
  // Input routing — every send takes a deviceId now
  // ────────────────────────────────────────────────────────────────────
  function emitTo(deviceId, type, payload = {}) {
    // Returns true iff the event was actually written to the input
    // DataChannel. Callers that need to retry on failure (e.g.
    // useStreamQuality's request_keyframe cooldown gating) rely on
    // this — without a real success signal they'd burn their local
    // cooldown on dropped messages.
    if (!deviceId) return false;
    const peer = rtc.getPeer(deviceId);
    if (!peer || peer.connectionState.value !== "connected") return false;
    const dcEvent = toDataChannelEvent(type, payload);
    if (!dcEvent) return false;
    return rtc.sendInput(deviceId, dcEvent) === true;
  }

  function sendKey(deviceId, input) {
    if (!deviceId) return;
    if (typeof input === "number") {
      emitTo(deviceId, "key", { keycode: input, action: 0 });
      emitTo(deviceId, "key", { keycode: input, action: 1 });
      return;
    }
    const keycode = Number(input?.keycode);
    if (!Number.isFinite(keycode)) return;
    if (input?.action === undefined || input?.action === null) {
      emitTo(deviceId, "key", { keycode, action: 0 });
      emitTo(deviceId, "key", { keycode, action: 1 });
      return;
    }
    emitTo(deviceId, "key", { keycode, action: Number(input.action) || 0 });
  }

  // Always-on key path: uses ``adb shell input keyevent`` via the backend
  // so the quick-action buttons work even when scrcpy isn't streaming
  // (e.g., user wants to wake the screen before pressing Play). Latency is
  // higher than the input DataChannel path but irrelevant for one-shot
  // navigation keys.
  async function sendQuickKey(deviceId, code) {
    if (!deviceId) return;
    try {
      await fetch(`/api/device/${encodeURIComponent(deviceId)}/keyevent`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
    } catch (err) {
      console.warn(`[scrcpy/${deviceId}] sendQuickKey ${code} failed`, err);
    }
  }

  // Directional swipe (up/down/left/right) via ``adb shell input swipe``.
  // Same always-on philosophy as sendQuickKey: no streaming required, so the
  // device-card footer's quick-swipe buttons work even before the user hits
  // Play. Coordinates are computed server-side from ``wm size`` so callers
  // don't need to know the device resolution.
  async function sendSwipe(deviceId, direction, duration) {
    if (!deviceId) return;
    try {
      await fetch(`/api/device/${encodeURIComponent(deviceId)}/swipe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ direction, duration }),
      });
    } catch (err) {
      console.warn(`[scrcpy/${deviceId}] sendSwipe ${direction} failed`, err);
    }
  }

  function sendBackKey(deviceId) {
    sendKey(deviceId, 4);
  }
  function sendHomeKey(deviceId) {
    sendKey(deviceId, 3);
  }

  // sendTouch accepts (deviceId, {x,y,action}) — DeviceCard emits one object.
  function sendTouch(deviceId, payload) {
    if (!payload) return;
    emitTo(deviceId, "touch", payload);
  }

  function sendScroll(deviceId, payload) {
    if (!payload) return;
    emitTo(deviceId, "scroll", payload);
  }

  function sendText(deviceId, text) {
    if (!deviceId || !text) return;
    emitTo(deviceId, "text", { text });
  }

  function screenOff(deviceId) {
    emitTo(deviceId, "set_power_mode", { mode: 0 });
  }
  function screenOn(deviceId) {
    emitTo(deviceId, "set_power_mode", { mode: 2 });
  }
  function expandNotification(deviceId) {
    emitTo(deviceId, "expand_notification");
  }
  function expandSettings(deviceId) {
    emitTo(deviceId, "expand_settings");
  }
  function collapsePanels(deviceId) {
    emitTo(deviceId, "collapse_panels");
  }
  // Rotation with a two-tier strategy:
  //
  //   1. Fast path — scrcpy's TYPE_ROTATE_DEVICE control message over
  //      the input DataChannel. ~10ms round-trip, no HTTP, works on
  //      stock Android + most vendor builds.
  //
  //   2. Fallback — ``settings put system user_rotation`` via HTTP, for
  //      OEMs (MIUI, ColorOS, Magic UI, ...) where scrcpy's path is
  //      silently swallowed by IWindowManager.freezeRotation.
  //
  // We detect fast-path failure by watching the session's resolution.
  // A 90° rotation always inverts the aspect ratio (long axis swaps
  // with short axis); the <video> element fires loadedmetadata on the
  // re-negotiated SPS/PPS, and ``handleMediaLoad`` updates
  // resolutionWidth/Height. If 600ms pass without an inversion we
  // assume the device ignored scrcpy and fall back to HTTP.
  // Per-session desired rotation, advanced locally. We can't trust
  // ``adb shell settings get system user_rotation`` for the counter —
  // MIUI / HyperOS return stale values right after a write, which used
  // to brick the cycle at rotation=2. Tracking the target client-side
  // keeps the cycle monotonic regardless of what the device reports.
  const desiredRotationByDevice = new Map();

  async function rotateDevice(deviceId) {
    if (!deviceId) return;
    const s = getSession(deviceId);
    const beforeW = s?.resolutionWidth.value || 0;
    const beforeH = s?.resolutionHeight.value || 0;
    const wasPortrait = beforeH > beforeW;

    // L1: scrcpy TYPE_ROTATE_DEVICE via input DataChannel. Known to be a
    // no-op on Xiaomi / OPPO / vivo ROMs (IWindowManager.freezeRotation
    // is locked down), but it's the fastest path on stock AOSP / Pixel
    // / OnePlus, so we still try. Detection window is short — 300ms —
    // because waiting longer just delays the inevitable on the bad ROMs.
    emitTo(deviceId, "rotate_device");
    if (beforeW > 0 && beforeH > 0) {
      for (let i = 0; i < 3; i++) {
        await new Promise((r) => setTimeout(r, 100));
        const w = s?.resolutionWidth.value || 0;
        const h = s?.resolutionHeight.value || 0;
        if (w > 0 && h > 0 && (h > w) !== wasPortrait) {
          // Aspect inverted → scrcpy path succeeded; the stream
          // re-negotiated SPS/PPS on its own, nothing else to do.
          return;
        }
      }
    }

    // L2: HTTP fallback. We send an explicit absolute target (cycled
    // locally) rather than asking the backend to read-modify-write, so
    // stale ``settings get`` reads can't deadlock the cycle.
    const cur = desiredRotationByDevice.get(deviceId) ?? 0;
    const target = (cur + 1) % 4;
    desiredRotationByDevice.set(deviceId, target);
    let applied = null;
    try {
      const resp = await fetch(
        `/api/device/${encodeURIComponent(deviceId)}/rotate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ target }),
        },
      );
      const data = await resp.json().catch(() => ({}));
      if (data && typeof data.rotation === "number") {
        applied = data.rotation;
        desiredRotationByDevice.set(deviceId, applied);
      }
    } catch (err) {
      console.warn(`[scrcpy/${deviceId}] rotate fallback failed`, err);
      return;
    }
    if (applied == null) return;

    // After a Settings-DB rotation the scrcpy server's RotationListener
    // never fires (the broadcast goes through WindowManagerService, but
    // we bypassed that on legacy ROMs), so the H.264 encoder keeps
    // emitting frames at the OLD geometry — Web shows the device upside
    // down forever. Restart the stream so the encoder picks up the new
    // display dimensions. On the modern ``cmd window`` path scrcpy DOES
    // see the broadcast, but restarting is harmless (≈300 ms blip) and
    // keeps the invariant simple.
    if (isStreaming(deviceId)) {
      // Don't await — let the user keep clicking through orientations
      // while the previous restart settles. restartStream is idempotent.
      restartStream(deviceId);
    }
  }

  // ────────────────────────────────────────────────────────────────────
  // Display-control actions (per-device, just twiddle local refs)
  // ────────────────────────────────────────────────────────────────────
  function setDisplayMode(deviceId, mode) {
    const s = getSession(deviceId);
    if (!s) return;
    s.displayMode.value = mode === "pixel" ? "pixel" : "fit";
  }

  function togglePause(deviceId) {
    const s = getSession(deviceId);
    if (!s) return;
    s.paused.value = !s.paused.value;
  }

  function resumePause(deviceId) {
    const s = getSession(deviceId);
    if (!s) return;
    s.paused.value = false;
  }

  // Reset display only nudges the displayMode now (fit/pixel toggle).
  // The rotation/flip family was removed when we switched to the real
  // device-side ``settings put system user_rotation`` path; the local
  // refs that those used to touch are also gone (see makeSession()).
  function resetDisplayView(deviceId) {
    const s = getSession(deviceId);
    if (!s) return;
    s.displayMode.value = "fit";
  }

  function handleMediaLoad(deviceId, payload) {
    const s = getSession(deviceId);
    if (!s || !payload) return;
    if (payload.width && payload.height) {
      s.resolutionWidth.value = payload.width;
      s.resolutionHeight.value = payload.height;
    }
    if (!s.paused.value) {
      s.frameState.value = "ready";
      // First decoded frame after start / restart: bring the quality
      // monitor online. Idempotent — restarting the stream (e.g. after
      // a forced keyframe) just reuses the existing monitor.
      attachQualityMonitor(deviceId);
    }
  }

  // ────────────────────────────────────────────────────────────────────
  // HTTP plumbing
  // ────────────────────────────────────────────────────────────────────
  async function postJson(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const err = new Error(
        payload.message || `Request failed: ${response.status}`,
      );
      // Expose the HTTP status so callers can localize known cases (e.g.
      // 403 = device not reserved) instead of showing the raw backend text.
      err.status = response.status;
      throw err;
    }
    return payload;
  }

  async function refreshServerHealth(deviceId = selectedDeviceId.value) {
    if (!deviceId) {
      setServerHealth("subtle", "server.noDeviceSelected");
      return;
    }
    try {
      const response = await fetch(
        `/api/scrcpy-server?device_id=${encodeURIComponent(deviceId)}`,
      );
      const payload = await response.json().catch(() => ({}));
      // 403 = device not reserved by us. This is a common, expected state
      // (not a failure) and the backend message is server-localized Chinese
      // — surface our own i18n key with a neutral tone instead of dumping
      // the raw backend string into the English UI.
      if (response.status === 403) {
        setServerHealth("subtle", "server.reserveFirst");
        return;
      }
      if (!response.ok)
        throw new Error(
          payload.message || `Request failed: ${response.status}`,
        );
      if (payload.matches) {
        setServerHealth("ok", "server.jarMatches");
        return;
      }
      // Hash mismatch IS a real concern — the device has a stale jar and
      // we'd push a fresh one. Keep the red.
      if (payload.remote && payload.remote.exists) {
        setServerHealth("danger", "server.jarHashMismatch");
        return;
      }
      // "jar not found" is just the normal first-connection state: the
      // server will push it automatically when the user hits Start. Don't
      // scare them with a red FAIL badge — show a neutral info chip
      // instead. Was previously ``'danger', 'server.jarNotFound'``.
      setServerHealth("subtle", "server.jarNotPushed");
    } catch (error) {
      setServerHealth("danger", "server.checkFailed", {
        literal: error instanceof Error ? error.message : "",
      });
    }
  }

  const streamConfig = ref({ bitrate: 8000000, max_fps: 0, max_width: 0 });
  const adaptiveBitrate = ref(false);

  async function setAdaptiveBitrate(enabled) {
    adaptiveBitrate.value = Boolean(enabled);
    const deviceId = subscribedDeviceId.value;
    if (!deviceId) return;
    try {
      await fetch("/api/adaptive", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          device_id: deviceId,
          enabled: Boolean(enabled),
        }),
      });
    } catch (err) {
      console.warn("[scrcpy] adaptive toggle failed", err);
    }
  }

  function refreshStreamConfig(cfg) {
    if (cfg && typeof cfg === "object") {
      streamConfig.value = { ...streamConfig.value, ...cfg };
    }
  }

  async function loadStreamConfig(deviceId) {
    if (!deviceId) return;
    try {
      const r = await fetch(
        `/api/stream-config?device_id=${encodeURIComponent(deviceId)}`,
      );
      const payload = await r.json().catch(() => ({}));
      if (r.ok && payload.config) refreshStreamConfig(payload.config);
    } catch {
      /* harmless: keep defaults */
    }
  }

  async function refreshDevices() {
    try {
      const response = await fetch("/api/devices");
      const payload = await response.json().catch(() => ({}));
      if (!response.ok)
        throw new Error(
          payload.message || `Request failed: ${response.status}`,
        );

      const next = Array.isArray(payload.devices) ? payload.devices : [];
      devices.value = next;
      // Merge friendly-name lookup, keeping any cached names for devices
      // that just dropped (the user may want to see them briefly in error
      // messages until refresh removes them).
      if (payload.names && typeof payload.names === "object") {
        deviceNames.value = { ...deviceNames.value, ...payload.names };
      }
      // Merge static geometry; entries for missing devices get dropped
      // below alongside the session cleanup so stale aspects can't
      // leak across re-plug cycles.
      if (payload.geometry && typeof payload.geometry === "object") {
        const merged = { ...deviceGeometry.value };
        for (const [serial, dims] of Object.entries(payload.geometry)) {
          if (
            dims &&
            typeof dims === "object" &&
            Number.isFinite(dims.width) &&
            Number.isFinite(dims.height) &&
            dims.width > 0 &&
            dims.height > 0
          ) {
            merged[serial] = { width: dims.width, height: dims.height };
          }
        }
        deviceGeometry.value = merged;
      }

      // Forget sessions for devices that disappeared. Also tear down their
      // peer connections if streaming.
      //
      // Fire-and-forget on removePeer: it does an async socket round-trip
      // (notifyServer is false here, so it's just local PC.close, but
      // still returns a Promise the next handler depends on). Awaiting
      // serialised the loop and blocked the whole refresh for a few
      // seconds whenever one device's stop was slow.
      // ``handleDevicesChanged`` already uses the void-prefixed form
      // for the same scenario — keep both call sites consistent.
      for (const id of Array.from(sessions.keys())) {
        if (!next.includes(id)) {
          if (rtc.hasPeer(id)) {
            void rtc.removePeer(id, { notifyServer: false });
          }
          detachQualityMonitor(id);
          desiredRotationByDevice.delete(id);
          removeSession(id);
        }
      }
      // Drop static geometry for serials we no longer see so a future
      // device on the same serial doesn't briefly render with stale dims.
      const geo = deviceGeometry.value;
      const dropped = Object.keys(geo).filter((id) => !next.includes(id));
      if (dropped.length) {
        const filtered = { ...geo };
        for (const id of dropped) delete filtered[id];
        deviceGeometry.value = filtered;
      }

      if (!next.length) {
        selectedDeviceId.value = "";
        subscribedDeviceId.value = "";
        setServerHealth("subtle", "server.noDeviceDetected");
        return;
      }
      if (!selectedDeviceId.value || !next.includes(selectedDeviceId.value)) {
        selectedDeviceId.value = next[0];
      }
      if (
        subscribedDeviceId.value &&
        !next.includes(subscribedDeviceId.value)
      ) {
        subscribedDeviceId.value = "";
      }
      await refreshServerHealth(selectedDeviceId.value);
    } catch (error) {
      devices.value = [];
      selectedDeviceId.value = "";
      subscribedDeviceId.value = "";
      setServerHealth("danger", "server.loadDevicesFailed", {
        literal: error instanceof Error ? error.message : "",
      });
    }
  }

  async function ensureDeviceStarted(deviceId) {
    return postJson("/api/start", { device_id: deviceId, boot_id: BOOT_ID });
  }

  // ────────────────────────────────────────────────────────────────────
  // Device stream lifecycle (NON-EXCLUSIVE: multiple devices can stream)
  // ────────────────────────────────────────────────────────────────────
  async function startDevice(deviceId) {
    if (!deviceId) return;
    const s = getSession(deviceId);
    try {
      // If a peer exists but the stream never reached 'ready' (decoder
      // stalled on bootstrap, scrcpy startup glitch, etc.) and the user
      // is clicking Play again, treat that as a retry: tear down the
      // dangling PC so the new addPeer renegotiates from scratch.
      if (rtc.hasPeer(deviceId) && s.frameState.value !== "ready") {
        console.warn(
          `[scrcpy/${deviceId}] retry: peer stuck in '${s.frameState.value}', rebuilding`,
        );
        try {
          await rtc.removePeer(deviceId);
        } catch {
          /* ignore */
        }
      }
      await ensureDeviceStarted(deviceId);
      subscribedDeviceId.value = deviceId;
      s.frameState.value = "loading";
      void refreshServerHealth(deviceId);
      void loadStreamConfig(deviceId);
      if (webrtcEnabled.value && !rtc.hasPeer(deviceId)) {
        try {
          await rtc.addPeer(deviceId);
        } catch (err) {
          console.warn(`[scrcpy/${deviceId}] WebRTC negotiation failed`, err);
        }
      }
      // Remember this device so a page reload auto-restores its stream.
      const next = [...new Set([...loadPersistedStreams(), deviceId])];
      savePersistedStreams(next);
    } catch (error) {
      setServerHealth("danger", "server.startDeviceFailed", {
        literal: error instanceof Error ? error.message : "",
      });
      resetSession(s);
    }
  }

  async function stopDevice(deviceId) {
    if (!deviceId) return;
    if (rtc.hasPeer(deviceId)) {
      try {
        await rtc.removePeer(deviceId);
      } catch {
        /* ignore */
      }
    }
    try {
      await postJson("/api/stop", { device_id: deviceId });
    } catch {
      /* device may be gone */
    }

    if (subscribedDeviceId.value === deviceId) {
      // Promote any still-streaming device, or clear.
      const remaining = activeDeviceIds.value.filter((id) => id !== deviceId);
      subscribedDeviceId.value = remaining[0] || "";
    }
    resetSession(sessions.get(deviceId));
    // Drop from the persisted set — user said stop, don't auto-resume.
    savePersistedStreams(
      loadPersistedStreams().filter((id) => id !== deviceId),
    );
  }

  // Called once per socket connect after the device list arrives. For each
  // device that was streaming before the last page close AND is still
  // physically connected, kick off a fresh stream. Errors are swallowed —
  // best-effort restore, never blocks the rest of the boot.
  async function autoRestoreStreams() {
    const persisted = loadPersistedStreams();
    if (!persisted.length) return;
    const connected = new Set(devices.value);
    for (const id of persisted) {
      if (!connected.has(id)) continue;
      if (rtc.hasPeer(id)) continue;
      try {
        await startDevice(id);
      } catch (err) {
        console.warn(`[scrcpy] auto-restore ${id} failed`, err);
      }
    }
  }

  async function restartStream(deviceId) {
    const target = deviceId || subscribedDeviceId.value;
    if (!target) return;
    await stopDevice(target);
    await new Promise((r) => setTimeout(r, 120));
    await startDevice(target);
  }

  async function pushServerJar(deviceId = selectedDeviceId.value) {
    if (!deviceId) return;
    try {
      setServerHealth("subtle", "server.pushing");
      const payload = await postJson("/api/scrcpy-server/push", {
        device_id: deviceId,
      });
      if (payload.message)
        setServerHealth("ok", "server.pushedMsg", { literal: payload.message });
      else setServerHealth("ok", "server.pushed");
      await refreshServerHealth(deviceId);
    } catch (error) {
      if (error && error.status === 403) {
        setServerHealth("subtle", "server.reserveFirst");
        return;
      }
      setServerHealth("danger", "server.pushFailed", {
        literal: error instanceof Error ? error.message : "",
      });
    }
  }

  // ────────────────────────────────────────────────────────────────────
  // File upload via adb DataChannel (per-device routed)
  // ────────────────────────────────────────────────────────────────────
  function adbReady(deviceId) {
    if (!webrtcEnabled.value) return false;
    const peer = rtc.getPeer(deviceId);
    if (!peer || peer.connectionState.value !== "connected") return false;
    const ch = peer.adbChannel.value;
    return Boolean(ch && ch.readyState === "open");
  }

  async function handleUploadFile(deviceId, file, { force = false } = {}) {
    if (!deviceId || !file) return;
    uploadBusy.value = true;
    // ``force`` retries surface a distinct status message so the user
    // can see *why* the install was retried — but we never block the
    // UI thread with window.confirm. Dragging an APK in is already
    // explicit intent to install; for non-dev users who care about
    // data preservation, the manual ``adb uninstall`` route is
    // unchanged.
    setUploadMessage(
      "subtle",
      force ? "upload.installingReplace" : "upload.uploading",
      { name: file.name },
    );
    try {
      // DataChannel path — only when adb DataChannel is open AND we're
      // not on a force-retry (force_replace requires re-uploading,
      // which is simpler over HTTP for the retry path).
      if (adbReady(deviceId) && !force) {
        try {
          const payload = await uploadViaDataChannel(deviceId, file);
          if (payload.action === "install")
            setUploadMessage("ok", "upload.installed", { filename: payload.filename });
          else
            setUploadMessage("ok", "upload.pushed", {
              filename: payload.filename,
              path: payload.remote_path,
            });
          return;
        } catch (dcErr) {
          if (dcErr && dcErr.code === "signature_mismatch") {
            // Auto-recover silently with force=1. The earlier behaviour
            // popped a window.confirm which the user explicitly
            // objected to as a blocking UI.
            return await handleUploadFile(deviceId, file, { force: true });
          }
          throw dcErr;
        }
      }
      const formData = new FormData();
      formData.append("device_id", deviceId);
      formData.append("file", file, file.name);
      if (force) formData.append("force", "1");
      const response = await fetch("/api/upload", {
        method: "POST",
        body: formData,
      });
      const payload = await response.json().catch(() => ({}));
      // 409 with signature_mismatch → auto-retry once with force=1.
      // No user prompt; the busy chip already reflects the retry phase.
      if (
        !response.ok &&
        payload &&
        payload.error_code === "signature_mismatch" &&
        !force
      ) {
        return await handleUploadFile(deviceId, file, { force: true });
      }
      if (!response.ok)
        throw new Error(payload.message || `Upload failed: ${response.status}`);
      if (payload.action === "install")
        setUploadMessage("ok", "upload.installed", { filename: payload.filename });
      else
        setUploadMessage("ok", "upload.pushed", {
          filename: payload.filename,
          path: payload.remote_path,
        });
    } catch (error) {
      setUploadMessage("danger", "upload.failed", {
        literal: error instanceof Error ? error.message : "",
      });
    } finally {
      uploadBusy.value = false;
    }
  }

  // Default timeout for a file upload that gets stuck without any
  // file.ready / progress / done reply. Without this, a wedged device
  // (sync server dead, sdcard full, adb tunnel quietly dropped) would
  // leave the promise pending forever AND keep the rtc.on("adb")
  // subscriber registered — every subsequent upload would accumulate
  // a dead listener.
  const UPLOAD_INACTIVITY_TIMEOUT_MS = 60_000;

  async function uploadViaDataChannel(deviceId, file) {
    const transferId = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    const CHUNK = 32 * 1024;
    const total = file.size;
    return new Promise((resolve, reject) => {
      let done = false;
      let seq = 0;
      let off = () => {};
      let timeoutHandle = null;

      // Cleanup runs exactly once on any termination path (resolve,
      // reject, timeout). Without this the rtc.on("adb") subscriber
      // could leak — each leaked listener stays registered for the
      // lifetime of useScrcpySession, fielding ALL adb messages.
      const cleanup = () => {
        if (done) return;
        done = true;
        try { off(); } catch { /* listener already detached */ }
        if (timeoutHandle != null) clearTimeout(timeoutHandle);
      };
      const settle = (fn, value) => {
        cleanup();
        fn(value);
      };
      const fail = (err) => settle(reject, err);
      const succeed = (payload) => settle(resolve, payload);

      const armTimeout = () => {
        if (timeoutHandle != null) clearTimeout(timeoutHandle);
        timeoutHandle = setTimeout(() => {
          fail(new Error(`upload timeout: no progress for ${UPLOAD_INACTIVITY_TIMEOUT_MS}ms`));
        }, UPLOAD_INACTIVITY_TIMEOUT_MS);
      };

      off = rtc.on("adb", async (msg) => {
        if (done) return;
        if (!msg || msg.deviceId !== deviceId) return;
        const p = msg.payload;
        if (!p || p.id !== transferId) return;
        // Any payload for our transfer counts as progress for the
        // inactivity timeout.
        armTimeout();
        if (p.t === "file.error") {
          // Promote the structured signature-mismatch shape to a
          // typed error so handleUploadFile can offer the destructive
          // recovery dialog. Plain Error.message is unchanged for the
          // generic failure path.
          const err = new Error(p.error || "upload failed");
          if (p.error_code === "signature_mismatch") {
            err.code = "signature_mismatch";
            err.existing_package = p.existing_package || "";
          }
          fail(err);
        } else if (p.t === "file.ready") {
          try {
            await streamChunks();
            if (!done) rtc.sendAdb(deviceId, { t: "file.end", id: transferId });
          } catch (err) {
            fail(err);
          }
        } else if (p.t === "file.progress") {
          if (total > 0) {
            const pct = Math.min(100, Math.round((p.received / total) * 100));
            setUploadMessage("subtle", "upload.uploadingPct", {
              name: file.name,
              pct,
            });
          }
        } else if (p.t === "file.done") {
          succeed(p);
        }
      });

      armTimeout();

      // Send the initial push request AFTER the listener is wired —
      // pre-fix order meant a synchronous reply could land before we
      // subscribed (rare, but possible if sendAdb processed
      // synchronously in some adapter).
      try {
        rtc.sendAdb(deviceId, {
          t: "file.push",
          id: transferId,
          name: file.name,
          size: total,
          remote: "/sdcard/Download/",
        });
      } catch (err) {
        // sendAdb throw before we ever received a reply → without the
        // cleanup() call the listener would leak forever. This is the
        // exact failure mode the audit flagged.
        fail(err);
      }

      async function streamChunks() {
        for (let offset = 0; offset < total; offset += CHUNK) {
          if (done) return;
          const slice = file.slice(offset, Math.min(offset + CHUNK, total));
          const buf = await slice.arrayBuffer();
          const b64 = arrayBufferToBase64(buf);
          const ch = rtc.getPeer(deviceId)?.adbChannel.value;
          while (ch && ch.bufferedAmount > 1_000_000) {
            await new Promise((r) => setTimeout(r, 30));
          }
          rtc.sendAdb(deviceId, {
            t: "file.chunk",
            id: transferId,
            seq,
            data: b64,
          });
          seq += 1;
        }
      }
    });
  }

  function arrayBufferToBase64(buf) {
    const bytes = new Uint8Array(buf);
    let binary = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  }

  // ────────────────────────────────────────────────────────────────────
  // Clipboard
  // ────────────────────────────────────────────────────────────────────
  async function requestClipboard(deviceId) {
    if (!deviceId) return;
    setClipboardAck("subtle", "clip.requesting");
    if (adbReady(deviceId)) {
      rtc.sendAdb(deviceId, { t: "clipboard.get" });
      return;
    }
    emitTo(deviceId, "clipboard_get");
  }

  async function pasteBrowserClipboard(deviceId, text = "") {
    if (!deviceId) return;
    let value = String(text || "");
    if (!value) {
      if (!navigator.clipboard?.readText) {
        setClipboardAck("danger", "clip.browserApiUnavailable");
        return;
      }
      try {
        value = await navigator.clipboard.readText();
      } catch {
        setClipboardAck("danger", "clip.browserPermissionDenied");
        return;
      }
    }
    if (!value) {
      setClipboardAck("subtle", "clip.browserEmpty");
      return;
    }
    setClipboardAck("subtle", "clip.writing");
    if (adbReady(deviceId)) {
      rtc.sendAdb(deviceId, { t: "clipboard.set", text: value, paste: true });
      return;
    }
    emitTo(deviceId, "clipboard_set", { text: value, paste: true });
  }

  // ────────────────────────────────────────────────────────────────────
  // rtc.on() returns an ``off()`` disposer. Track every subscription
  // we make at composable setup so we can release them on unmount —
  // without this an accidental double-mount (HMR, route reuse,
  // ad-hoc unit test) would leave dead listeners attached to the
  // ``rtc`` singleton's Map<event, Set<fn>>, growing without bound.
  // ────────────────────────────────────────────────────────────────────
  const rtcListenerDisposers = [];

  // PC teardown notification — keep session state consistent with the
  // peer registry. Without this, when the backend pushes ``webrtc:closed``
  // useWebRTC tears down the PC silently, but ``sessions[deviceId]``
  // keeps ``frameState === "ready"`` and the quality monitor keeps
  // polling a dead PC. The next reconnect inherits a stale ``prev``
  // snapshot and emits a spurious freeze → reset_video → restart loop.
  rtcListenerDisposers.push(
    rtc.on("closed", ({ deviceId }) => {
      if (!deviceId) return;
      detachQualityMonitor(deviceId);
      const s = sessions.get(deviceId);
      if (s) resetSession(s);
    }),
  );

  // adb channel replies — demux by deviceId
  rtcListenerDisposers.push(rtc.on("adb", (msg) => {
    if (!msg || !msg.payload || typeof msg.payload !== "object") return;
    const { deviceId, payload } = msg;
    if (payload.t === "clipboard.value") {
      // Read-only — use peekSession so a late clipboard reply arriving
      // after the device was disconnected / removed doesn't resurrect
      // a session entry (the B3 hazard the peek/get split was meant
      // to prevent; clipboard handler was missed at the time).
      const s = peekSession(deviceId);
      if (s) {
        s.clipboardContent.value = payload.text || "";
        setClipboardAck("ok", "clip.updatedFrom", { id: deviceId });
      }
    } else if (payload.t === "clipboard.ack") {
      if (payload.ok)
        setClipboardAck("ok", "clip.ackOk", { seq: payload.sequence ?? "" });
      else
        setClipboardAck("danger", "clip.ackFailed", {
          seq: payload.sequence ?? "",
        });
    }
  }));

  // ────────────────────────────────────────────────────────────────────
  // Socket.IO
  // ────────────────────────────────────────────────────────────────────
  function handleDevicesChanged(data) {
    if (!data || !Array.isArray(data.devices)) return;
    const next = data.devices;
    // Drop sessions + peers + ALL per-device side-state for devices
    // that are gone — they're not coming back via the same handle.
    // Per-device Maps (qualityMonitors, desiredRotationByDevice) that
    // we forgot to clean up here would leak entries indefinitely
    // across hotplug cycles, eventually slowing every Map iteration.
    for (const id of Array.from(sessions.keys())) {
      if (!next.includes(id)) {
        if (rtc.hasPeer(id)) void rtc.removePeer(id, { notifyServer: false });
        detachQualityMonitor(id);
        desiredRotationByDevice.delete(id);
        deviceEpochById.delete(id);
        removeSession(id);
      }
    }
    // Devices may also have lived in our auxiliary maps without ever
    // having a session (e.g. rotation issued before first stream).
    // Sweep those too.
    for (const id of Array.from(desiredRotationByDevice.keys())) {
      if (!next.includes(id)) desiredRotationByDevice.delete(id);
    }
    for (const id of Array.from(deviceEpochById.keys())) {
      if (!next.includes(id)) deviceEpochById.delete(id);
    }
    devices.value = next;
    if (data.names && typeof data.names === "object") {
      deviceNames.value = { ...deviceNames.value, ...data.names };
    }
    if (subscribedDeviceId.value && !next.includes(subscribedDeviceId.value)) {
      subscribedDeviceId.value = next[0] || "";
    }
    if (selectedDeviceId.value && !next.includes(selectedDeviceId.value)) {
      selectedDeviceId.value = next[0] || "";
    }
  }

  // Per-device "current scrcpy lifecycle epoch". Updated on every
  // ``connected`` event; consulted on every ``disconnected`` event so
  // we can ignore broadcasts that belong to a previous Client instance
  // for the same device_id.
  //
  // The race we're fixing (see test_refresh_joint_pipeline.py and
  // test_scrcpy_lifecycle_epoch.py): on F5 the backend's get_client()
  // can find a dead-but-registered Client, call old.stop() (which fires
  // EVENT_DISCONNECT synchronously), then build a new one. The OLD
  // client's ``disconnected`` broadcast then gets sent on the NEW
  // socket — by the time the browser sees it, the new Client is up,
  // the new peer is streaming, and the user is watching the picture.
  // Without an epoch on the payload, the frontend can't tell apart
  // "old, ignore" from "current, tear down" and ends up killing the
  // working stream.
  const deviceEpochById = new Map();

  function handleScrcpyStatus(data) {
    if (!data?.device_id) return;
    const id = data.device_id;
    const incomingEpoch = data.epoch ?? null;
    if (incomingEpoch == null) return;
    const s = getSession(id);
    if (data.status === "connected") {
      // The 'connected' broadcast announces THE current epoch for this
      // device. Future disconnects must match it to be honoured.
      deviceEpochById.set(id, incomingEpoch);
      subscribedDeviceId.value = id;
      // Tear down any stale quality monitor before letting the next
      // first-frame attach a fresh one. Without this, a soft restart
      // (no preceding "disconnected" event — e.g. backend re-attaches
      // PCs after reconfigure) lets the OLD monitor's ``prev`` /
      // ``stallStartTs`` carry over onto the NEW PC, and the first
      // second after reconnect almost always trips the freeze
      // detector → spurious reset_video → restart loop.
      detachQualityMonitor(id);
      if (s) s.frameState.value = "loading";
      if (data.control_available)
        setServerHealth("ok", "server.controlAvailable");
      return;
    }
    if (data.status === "disconnected") {
      // Strict epoch gate: only a disconnect from the CURRENT client
      // generation may tear down the peer. We don't accept missing
      // epochs or mismatches here because this deployment only speaks
      // the epoch-aware protocol now.
      const current = deviceEpochById.get(id);
      if (current == null || current !== incomingEpoch) {
        return;
      }
      // Honoured disconnect — clear the stored epoch so a future
      // ``connected`` re-seeds it cleanly.
      deviceEpochById.delete(id);
      if (subscribedDeviceId.value === id) {
        const remaining = activeDeviceIds.value.filter((x) => x !== id);
        subscribedDeviceId.value = remaining[0] || "";
      }
      resetSession(s);
      // Tear down the dangling PC so the card stops showing a black box
      // (an attached MediaStream with no live frames). Without this the
      // chip flips to "disconnected" but the <video> element keeps a
      // stale srcObject and the user sees a black rectangle instead of
      // the empty-state placeholder.
      if (rtc.hasPeer(id)) {
        void rtc.removePeer(id, { notifyServer: false });
      }
    }
  }

  function setupSocket() {
    // Socket.IO is only the signaling channel. Force HTTP long-polling.
    // Werkzeug threading mode can't complete a WebSocket upgrade.
    const next = io({
      transports: ["polling"],
      upgrade: false,
      autoConnect: false,
      auth: { boot_id: BOOT_ID },
    });
    next.on("connect", async () => {
      socketConnected.value = true;
      await refreshDevices();
      // After we know which devices are connected, restart any that were
      // streaming before the last reload. Sequential to avoid spamming
      // the scrcpy lifecycle handler.
      void autoRestoreStreams();
    });
    next.on("disconnect", () => {
      socketConnected.value = false;
      subscribedDeviceId.value = "";
      for (const s of sessions.values()) resetSession(s);
    });
    next.on("scrcpy_status", handleScrcpyStatus);
    next.on("devices_changed", handleDevicesChanged);
    socket.value = next;
    next.connect();
  }

  watch(selectedDeviceId, (next) => {
    if (next) void refreshServerHealth(next);
    else setServerHealth("subtle", "server.noDeviceSelected");
  });

  onMounted(() => {
    setupSocket();
    void refreshDevices();
  });
  onBeforeUnmount(() => {
    if (socket.value) {
      socket.value.removeAllListeners();
      socket.value.disconnect();
    }
    // Release the ``rtc`` event-bus subscriptions we wired during
    // setup. Forgetting this would leak listeners on every HMR or
    // double-mount, and the dead handlers would keep firing for
    // every future close / adb event.
    for (const dispose of rtcListenerDisposers) {
      try { dispose(); } catch { /* listener already removed */ }
    }
    rtcListenerDisposers.length = 0;
    // Also stop any per-device quality monitors still running.
    for (const id of Array.from(qualityMonitors.keys())) {
      detachQualityMonitor(id);
    }
  });

  // ────────────────────────────────────────────────────────────────────
  // Derived: status text shown in the (now demoted) connection panel
  // ────────────────────────────────────────────────────────────────────
  // Semantic enums — display text lives in i18n on the consuming component
  // so the composable stays locale-agnostic.
  const connectionStatusKind = computed(() => {
    if (!socketConnected.value) return "disconnected";
    if (activeDeviceIds.value.length) return "subscribed";
    return "connected";
  });
  const activeStreamCount = computed(() => activeDeviceIds.value.length);
  const controlStatusKind = computed(() => {
    if (!selectedDeviceId.value) return "idle";
    if (subscribedDeviceId.value) return "streaming";
    return "selected";
  });

  // Resolution text for the settings-page summary chip. Reads the
  // "most recently started" device's resolution — the same device the
  // connection/control status text refers to.
  const resolutionText = computed(() => {
    const s = sessions.get(subscribedDeviceId.value);
    const w = s?.resolutionWidth.value || 0;
    const h = s?.resolutionHeight.value || 0;
    return w && h ? `${w} × ${h}` : "-";
  });

  // Drawer text composer — global because there is one drawer at a time.
  const textValue = ref("");

  return {
    // ── global state ──────────────────────────────────────────────
    socket,
    socketConnected,
    webrtcEnabled,
    devices,
    deviceNames,
    deviceGeometry,
    nameOf,
    selectedDeviceId,
    subscribedDeviceId,
    activeDeviceIds,
    textValue,

    // ── per-device session access ─────────────────────────────────
    sessions,
    getSession,
    peekSession,
    hasSession,
    removeSession,
    videoStreamOf,
    isStreaming,

    // ── stream / negotiation lifecycle ────────────────────────────
    refreshDevices,
    startDevice,
    stopDevice,
    restartStream,
    pushServerJar,

    // ── input (per-device) ────────────────────────────────────────
    sendKey,
    sendQuickKey,
    sendSwipe,
    sendBackKey,
    sendHomeKey,
    sendTouch,
    sendScroll,
    sendText,
    screenOff,
    screenOn,
    expandNotification,
    expandSettings,
    collapsePanels,
    rotateDevice,

    // ── display tweaks (per-device) ───────────────────────────────
    setDisplayMode,
    togglePause,
    resumePause,
    resetDisplayView,
    handleMediaLoad,

    // ── clipboard / upload ────────────────────────────────────────
    requestClipboard,
    pasteBrowserClipboard,
    handleUploadFile,

    // ── stream config / adaptive ──────────────────────────────────
    streamConfig,
    refreshStreamConfig,
    adaptiveBitrate,
    setAdaptiveBitrate,

    // ── status / chips (global) ───────────────────────────────────
    uploadBusy,
    uploadMessage,
    uploadMessageClass,
    clipboardAckStatus,
    clipboardAckClass,
    serverHealthText,
    serverHealthClass,
    audioMuted,
    audioState,
    bindingSearchText,
    bindingActiveGroup,
    showBindingEditor,
    connectionStatusKind,
    activeStreamCount,
    controlStatusKind,
    resolutionText,

    // ── status setters / internals ────────────────────────────────
    setServerHealth,
    setClipboardAck,
    setUploadMessage,

    // Audio is not currently piped on any track — kept as a stub so the
    // settings page toggle doesn't crash.
    toggleAudio: () => {
      audioMuted.value = !audioMuted.value;
    },
  };
}
