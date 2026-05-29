<template>
  <!-- The aspect-locked live video pane. Renders as ``.device-card-screen``
       (styled globally in style.css) so it remains a direct grid child of
       ``.device-card`` and keeps its row assignment. Self-contained: owns the
       device aspect-ratio, the <video> srcObject binding, drag-to-upload, the
       pointer/wheel → device-coordinate input mapping, and the three overlays
       (drag hint / paused / empty placeholder). -->
  <div
    class="device-card-screen"
    :class="{ 'drag-over': dragOver }"
    :style="screenAspectStyle"
    @click.stop
    @dragenter.prevent="onDragEnter"
    @dragover.prevent="onDragOver"
    @dragleave.prevent="onDragLeave"
    @drop.prevent="onDropFile"
  >
    <video
      v-if="videoStream"
      :key="`${deviceId || 'device'}:${videoStream.id || 'stream'}`"
      ref="videoRef"
      class="card-video"
      autoplay
      playsinline
      :muted="muted"
      @loadedmetadata="onLoadedMetadata"
      @contextmenu.prevent="emitBack"
      @pointerdown.prevent="handlePointerDown"
      @pointermove.prevent="handlePointerMove"
      @pointerup.prevent="handlePointerUp"
      @pointercancel="cancelPointerInteraction"
      @auxclick.prevent="handleAuxClick"
      @wheel.prevent="handleWheel"
    />
    <div v-if="dragOver" class="card-drop-overlay">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" width="40" height="40">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
        <polyline points="17 8 12 3 7 8" />
        <line x1="12" y1="3" x2="12" y2="15" />
      </svg>
      <p>{{ t("quick.dropHint") }}</p>
    </div>
    <div v-if="paused && streaming" class="card-pause-overlay">
      <span>{{ t("viewer.paused") }}</span>
    </div>
    <div v-if="!videoStream" class="card-screen-empty">
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="1.5"
        aria-hidden="true"
      >
        <rect x="6" y="2" width="12" height="20" rx="2.5" />
        <line x1="11" y1="18" x2="13" y2="18" stroke-linecap="round" />
      </svg>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from "vue";
import { useUiI18n } from "../composables/useUiI18n";
import { safeRatio } from "../aspect";

const { t } = useUiI18n();

const props = defineProps({
  deviceId: { type: String, default: "" },
  streaming: { type: Boolean, default: false },
  paused: { type: Boolean, default: false },
  muted: { type: Boolean, default: true },
  videoStream: { type: Object, default: null },
  // Device coord-space for input mapping. Only meaningful on the pane that
  // actually holds the live stream — others ignore input.
  resolutionWidth: { type: Number, default: 0 },
  resolutionHeight: { type: Number, default: 0 },
  // Static geometry (from /api/devices) so the pane is correctly shaped
  // before any stream exists; live <video> resolution takes priority once
  // available.
  staticWidth: { type: Number, default: 0 },
  staticHeight: { type: Number, default: 0 },
});

const emit = defineEmits([
  "media-load",
  "touch",
  "scroll",
  "back",
  "home",
  "upload-file",
]);

// ─── Aspect ─────────────────────────────────────────────────────────
// Live <video> resolution > static wm-size > intrinsic (no override →
// the stylesheet's default 9:16 placeholder shows during loading).
const screenAspectStyle = computed(() => {
  if (safeRatio(props.resolutionWidth, props.resolutionHeight)) {
    return {
      aspectRatio: `${props.resolutionWidth} / ${props.resolutionHeight}`,
    };
  }
  if (safeRatio(props.staticWidth, props.staticHeight)) {
    return { aspectRatio: `${props.staticWidth} / ${props.staticHeight}` };
  }
  return {};
});

function onLoadedMetadata(event) {
  const v = event.target;
  if (v && v.videoWidth && v.videoHeight) {
    emit("media-load", { width: v.videoWidth, height: v.videoHeight });
  }
}

// ─── <video> srcObject binding ──────────────────────────────────────
const videoRef = ref(null);

function bindStream() {
  const el = videoRef.value;
  if (!el) return;
  if (el.srcObject !== props.videoStream) {
    try {
      el.pause?.();
    } catch {
      /* ignore */
    }
    el.srcObject = props.videoStream || null;
  }
  if (props.videoStream) {
    // CRITICAL: force the .muted PROPERTY to true BEFORE .play().
    // We also defensively set ``defaultMuted`` so any future HMR
    // re-mount that constructs a fresh element starts muted before
    // the first property assignment.
    try {
      el.muted = true;
      el.defaultMuted = true;
    } catch {
      /* ignore */
    }
    void el.play?.().catch(() => {
      // Last-ditch: if play() still rejected (e.g. props.muted is
      // legitimately false because the card was focused), force-mute
      // and retry once. Browsers always allow muted programmatic play.
      try {
        el.muted = true;
        void el.play?.().catch(() => { /* give up silently */ });
      } catch {
        /* ignore */
      }
    });
  }
}

// Re-bind on either stream change OR ref availability (v-if mounts/unmounts).
watch(() => props.videoStream, bindStream, { flush: "post" });
watch(videoRef, bindStream, { flush: "post" });

// ─── Drag-and-drop onto the live screen area ────────────────────────
// Drop an APK/file onto the visible device screen → emit ``upload-file``
// so the parent routes it through useScrcpySession.handleUploadFile (the
// one shared signature-mismatch confirmation path). Without this the
// browser takes over the drop and just opens the file as a URL.
const dragOver = ref(false);

// HTML5 drag-and-drop fires ``dragleave`` on EVERY child-element
// transition, not just when the cursor truly leaves. Count enter/leave
// pairs and only flip ``dragOver`` when depth returns to zero, else the
// overlay flickers (unmount/remount) over the live video.
let _dragDepth = 0;

function _hasDraggedFiles(ev) {
  // ``dataTransfer.types`` is the most reliable cross-browser signal —
  // ``.files`` is empty during dragenter/dragover for security reasons
  // and only populated on drop.
  const types = ev.dataTransfer?.types;
  if (!types) return false;
  return Array.from(types).includes("Files");
}

function onDragEnter(ev) {
  if (!props.deviceId) return;
  if (!_hasDraggedFiles(ev)) return;
  _dragDepth += 1;
  dragOver.value = true;
}

function onDragOver(ev) {
  if (!props.deviceId) return;
  if (!_hasDraggedFiles(ev)) return;
  // ``copy`` shows the OS's "add file" cursor and tells the browser we'll
  // accept the drop (without this it falls back to ``none``).
  if (ev.dataTransfer) ev.dataTransfer.dropEffect = "copy";
}

function onDragLeave(_ev) {
  _dragDepth = Math.max(0, _dragDepth - 1);
  if (_dragDepth === 0) dragOver.value = false;
}

function onDropFile(ev) {
  _dragDepth = 0;
  dragOver.value = false;
  if (!props.deviceId) return;
  const files = [...(ev.dataTransfer?.files || [])];
  if (!files.length) return;
  for (const file of files) {
    emit("upload-file", file);
  }
}

// ─── Input forwarding (pointer / wheel → device coords) ─────────────
const pointerState = reactive({ dragging: false, pointerId: null, last: null });
// Cached bounding rect for the active gesture — snapshot at pointerdown
// and reuse for the whole gesture instead of a forced layout per move
// at 120-240 Hz. Cleared on up/cancel.
let cachedRect = null;
function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v));
}

function readVideoRect() {
  const el = videoRef.value;
  if (!el) return null;
  const r = el.getBoundingClientRect();
  if (!r.width || !r.height) return null;
  return r;
}

function mapToDevice(event, rectOverride = null) {
  // Device-side rotation (ROTATE_DEVICE control msg) means we no longer
  // apply any client-side rotation/flip transform — the device's own
  // coordinate system rotates with the screen and onLoadedMetadata
  // refreshes resolutionWidth/Height on every re-negotiation. Plain
  // rect-relative percentage → device-pixel mapping.
  if (!props.resolutionWidth || !props.resolutionHeight) return null;
  const rect = rectOverride || readVideoRect();
  if (!rect) return null;
  const x = clamp((event.clientX - rect.left) / rect.width, 0, 1);
  const y = clamp((event.clientY - rect.top) / rect.height, 0, 1);
  return {
    x: Math.round(x * props.resolutionWidth),
    y: Math.round(y * props.resolutionHeight),
  };
}

function handlePointerDown(event) {
  if (!props.streaming) return;
  if (event.button === 2) {
    emit("back");
    return;
  }
  if (event.button === 1) {
    emit("home");
    return;
  }
  if (event.button !== 0) return;
  cachedRect = readVideoRect();
  const p = mapToDevice(event, cachedRect);
  if (!p) {
    cachedRect = null;
    return;
  }
  pointerState.dragging = true;
  pointerState.pointerId = event.pointerId;
  pointerState.last = p;
  videoRef.value?.setPointerCapture?.(event.pointerId);
  emit("touch", { x: p.x, y: p.y, action: 0 });
}
function handlePointerMove(event) {
  if (!pointerState.dragging || pointerState.pointerId !== event.pointerId)
    return;
  const p = mapToDevice(event, cachedRect);
  if (!p) return;
  pointerState.last = p;
  emit("touch", { x: p.x, y: p.y, action: 2 });
}
function finishPointer(event, sendUp) {
  if (!pointerState.dragging || pointerState.pointerId !== event.pointerId)
    return;
  const p = mapToDevice(event, cachedRect) || pointerState.last;
  if (sendUp && p) emit("touch", { x: p.x, y: p.y, action: 1 });
  try {
    videoRef.value?.releasePointerCapture?.(event.pointerId);
  } catch {
    /* ignore */
  }
  pointerState.dragging = false;
  pointerState.pointerId = null;
  pointerState.last = null;
  cachedRect = null;
}
function handlePointerUp(event) {
  finishPointer(event, true);
}
function cancelPointerInteraction(event) {
  finishPointer(event, false);
}
function handleAuxClick(event) {
  if (event.button === 1) emit("home");
}
function emitBack() {
  emit("back");
}

// Idle-based rect cache for wheel events — wheel has no down/up bracket,
// so cache the rect for ~250 ms after the last wheel to dodge a forced
// layout per smooth-scroll tick.
let wheelRect = null;
let wheelRectExpiresAt = 0;
const WHEEL_RECT_TTL_MS = 250;

// Diagnostic kill-switch for wheel → scroll forwarding (BufferQueue /
// Virtual-Display storm investigation). Disable from DevTools without a
// rebuild:  localStorage.setItem("scrcpy:disableWheelForward", "1")
function isWheelForwardDisabled() {
  try {
    return window.localStorage?.getItem("scrcpy:disableWheelForward") === "1";
  } catch {
    return false;
  }
}

function handleWheel(event) {
  if (!props.streaming) return;
  if (isWheelForwardDisabled()) return;
  // Drop sub-unit wheel ticks — touchpads emit an inertia tail of
  // ``|delta| < 1`` events that made device touch-feedback jitter.
  if (Math.abs(event.deltaX) < 1 && Math.abs(event.deltaY) < 1) return;
  const now = (typeof performance !== "undefined" ? performance.now() : Date.now());
  if (!wheelRect || now > wheelRectExpiresAt) {
    wheelRect = readVideoRect();
  }
  wheelRectExpiresAt = now + WHEEL_RECT_TTL_MS;
  const p = mapToDevice(event, wheelRect);
  if (!p) return;
  emit("scroll", {
    x: p.x,
    y: p.y,
    h: Math.round(event.deltaX * 12),
    v: Math.round(event.deltaY * 12),
  });
}
</script>
