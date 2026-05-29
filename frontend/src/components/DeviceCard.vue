<template>
  <article
    class="device-card"
    :class="{ active, streaming, expanded }"
    :style="cardAspectStyle"
  >
    <header
      class="device-card-head"
      @click.stop="$emit('select')"
      @dblclick.stop="$emit('focus')"
    >
      <span class="device-card-title" :title="title">{{ title }}</span>
      <span class="device-card-chip" :class="chipClass">
        <span class="dot" />
        {{ chipText }}
      </span>
    </header>

    <!-- Reservation strip (grid view). Stop clicks bubbling to the header's
         select/focus handlers so claim/release controls work in-place. -->
    <div
      v-if="$slots.reservation"
      class="device-card-reservation"
      @click.stop
      @dblclick.stop
    >
      <slot name="reservation" />
    </div>

    <DeviceScreen
      :device-id="deviceId"
      :streaming="streaming"
      :paused="paused"
      :muted="muted"
      :video-stream="videoStream"
      :resolution-width="resolutionWidth"
      :resolution-height="resolutionHeight"
      :static-width="staticWidth"
      :static-height="staticHeight"
      @media-load="$emit('media-load', $event)"
      @touch="$emit('touch', $event)"
      @scroll="$emit('scroll', $event)"
      @back="$emit('back')"
      @home="$emit('home')"
      @upload-file="$emit('upload-file', $event)"
    />

    <footer class="device-card-actions" @click.stop>
      <button
        class="card-action"
        :class="{ primary: !streaming }"
        :title="streaming ? t('device.stop') : t('device.start')"
        :disabled="!deviceId"
        @click="$emit(streaming ? 'stop' : 'start')"
      >
        <svg v-if="streaming" viewBox="0 0 24 24" fill="currentColor">
          <rect x="6" y="6" width="12" height="12" rx="1.5" />
        </svg>
        <svg v-else viewBox="0 0 24 24" fill="currentColor">
          <path d="M8 5v14l11-7z" />
        </svg>
      </button>
      <button
        class="card-action"
        :class="{ active: paused }"
        :title="paused ? t('viewer.resume') : t('viewer.paused')"
        :disabled="!streaming"
        @click="$emit('toggle-pause')"
      >
        <svg v-if="paused" viewBox="0 0 24 24" fill="currentColor">
          <path d="M8 5v14l11-7z" />
        </svg>
        <svg v-else viewBox="0 0 24 24" fill="currentColor">
          <path d="M6 4h4v16H6zm8 0h4v16h-4z" />
        </svg>
      </button>
      <span class="card-action-spacer" />
      <button
        class="card-action"
        :disabled="!deviceId"
        :title="t('device.back')"
        @click.stop="$emit('send-key', 4)"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <polyline points="15 18 9 12 15 6" />
        </svg>
      </button>
      <button
        class="card-action"
        :disabled="!deviceId"
        :title="t('device.home')"
        @click.stop="$emit('send-key', 3)"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path d="M3 12l9-9 9 9" />
          <path d="M5 10v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V10" />
        </svg>
      </button>
      <button
        class="card-action"
        :disabled="!deviceId"
        :title="t('device.appSwitch')"
        @click.stop="$emit('send-key', 187)"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <rect x="4" y="4" width="16" height="16" rx="2" />
        </svg>
      </button>
      <span class="card-action-spacer" />
      <button
        class="card-action"
        :disabled="!streaming"
        :title="t('action.rotate')"
        @click.stop="$emit('rotate-device')"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path d="M21 12a9 9 0 1 1-3.5-7.1" />
          <polyline points="21 3 21 9 15 9" />
        </svg>
      </button>
      <button
        :id="moreButtonId"
        class="card-action"
        :disabled="!deviceId"
        :title="t('device.more')"
        :popovertarget="moreMenuId"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <polyline points="12 5 12 9" />
          <polyline points="9 7 12 4 15 7" />
          <polyline points="12 19 12 15" />
          <polyline points="9 17 12 20 15 17" />
          <polyline points="5 12 9 12" />
          <polyline points="7 9 4 12 7 15" />
          <polyline points="19 12 15 12" />
          <polyline points="17 9 20 12 17 15" />
        </svg>
      </button>
      <button
        class="card-action"
        :title="t('navRail.openDrawer')"
        @click="$emit('focus')"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <circle cx="12" cy="5" r="1.5" />
          <circle cx="12" cy="12" r="1.5" />
          <circle cx="12" cy="19" r="1.5" />
        </svg>
      </button>
    </footer>

    <div
      :id="moreMenuId"
      popover="manual"
      class="dpad-menu"
      role="group"
      :aria-label="t('device.more')"
      @toggle="handleMorePopoverToggle"
    >
      <button
        class="dpad-close"
        type="button"
        :title="t('action.close')"
        :aria-label="t('action.close')"
        @click="closeMoreMenu"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <line x1="6" y1="6" x2="18" y2="18" />
          <line x1="18" y1="6" x2="6" y2="18" />
        </svg>
      </button>
      <button
        class="dpad-key dpad-up"
        :disabled="!deviceId"
        :title="t('action.swipeUp')"
        data-direction="up"
        @click="emitSwipe('up')"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="6 11 12 5 18 11" />
          <line x1="12" y1="19" x2="12" y2="5" />
        </svg>
      </button>
      <button
        class="dpad-key dpad-left"
        :disabled="!deviceId"
        :title="t('action.swipeLeft')"
        data-direction="left"
        @click="emitSwipe('left')"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="11 6 5 12 11 18" />
          <line x1="19" y1="12" x2="5" y2="12" />
        </svg>
      </button>
      <span class="dpad-hub" aria-hidden="true" />
      <button
        class="dpad-key dpad-right"
        :disabled="!deviceId"
        :title="t('action.swipeRight')"
        data-direction="right"
        @click="emitSwipe('right')"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="13 6 19 12 13 18" />
          <line x1="5" y1="12" x2="19" y2="12" />
        </svg>
      </button>
      <button
        class="dpad-key dpad-down"
        :disabled="!deviceId"
        :title="t('action.swipeDown')"
        data-direction="down"
        @click="emitSwipe('down')"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="6 13 12 19 18 13" />
          <line x1="12" y1="5" x2="12" y2="19" />
        </svg>
      </button>
    </div>
  </article>
</template>

<script setup>
import { computed, onBeforeUnmount } from "vue";
import { useUiI18n } from "../composables/useUiI18n";
import { safeRatio } from "../aspect";
import DeviceScreen from "./DeviceScreen.vue";

const { t } = useUiI18n();


// Aspect ratio is decided by a 3-tier priority chain in
// ``cardAspectStyle`` below: live <video> > static wm-size prop > a
// modern-phone default (~9:20). No client-side cache needed — the
// backend's ``/api/devices`` response includes per-device geometry
// from the moment a device is enumerated, so the card chrome shapes
// correctly before any stream starts and stays consistent across
// start/stop cycles. The earlier localStorage hack lived here.

const props = defineProps({
  deviceId: { type: String, default: "" },
  title: { type: String, default: "" },
  active: { type: Boolean, default: false }, // focused in the drawer
  streaming: { type: Boolean, default: false }, // has a live scrcpy stream
  expanded: { type: Boolean, default: false }, // user-toggled "enlarge in grid"
  paused: { type: Boolean, default: false }, // stream paused
  muted: { type: Boolean, default: true }, // <video muted> — only focused card unmutes
  // frameState mirrors useScrcpySession's per-device session.frameState:
  //   'idle'    — never started or stopped
  //   'loading' — start request sent, waiting for first frame
  //   'ready'   — frames decoded, chip shows "connected"
  frameState: { type: String, default: "idle" },
  // True iff the device is currently visible to adb. False = truly offline
  // (USB unplugged, network adb dropped). 'idle' frameState with online=true
  // is the much more common "device here, just hasn't been started" case.
  online: { type: Boolean, default: true },
  videoStream: { type: Object, default: null },
  // Device coord-space for input mapping. Only meaningful on the card
  // that actually holds the live stream — other cards ignore input.
  resolutionWidth: { type: Number, default: 0 },
  resolutionHeight: { type: Number, default: 0 },
  // Static device geometry from ``adb shell wm size`` (via /api/devices'
  // ``geometry`` map). Lets us shape the card chrome correctly BEFORE
  // any stream exists — without this the card defaulted to 9:16 and
  // visibly jumped to the device's real aspect (e.g. 9:20.2 on Pixel 10)
  // the moment streaming started. Live ``<video>`` resolution takes
  // priority once available — it reflects rotation and override sizes.
  staticWidth: { type: Number, default: 0 },
  staticHeight: { type: Number, default: 0 },
  // ``rotation`` / ``flipHorizontal`` / ``flipVertical`` props were
  // removed when we switched to the device-side rotation path. They had
  // no visual effect anyway — only mutated input coords — and parent
  // App.vue no longer passes them. Don't add them back unless you also
  // re-introduce a real visual transform on the <video> element.
});

const emit = defineEmits([
  "focus",
  "select",
  "start",
  "stop",
  "toggle-pause",
  "toggle-expand",
  "touch",
  "scroll",
  "back",
  "home",
  "media-load",
  // Inline nav buttons in the footer (Home/Back/AppSwitch). Parent routes
  // these through sendQuickKey(deviceId, code) — same HTTP /keyevent path
  // as the side QuickActionsCard so they fire even without a live stream.
  "send-key",
  // Footer "rotate" button — parent calls useScrcpySession.rotateDevice(),
  // which sends scrcpy's ROTATE_DEVICE control message over the input
  // DataChannel. One click = 90° clockwise rotation of the device itself.
  "rotate-device",
  // Footer quick-swipe buttons — payload is "up" | "down" | "left" |
  // "right". Parent routes through useScrcpySession.sendSwipe(id, dir)
  // → backend ``adb shell input swipe`` so no live stream is required.
  "swipe",
  // File dropped onto the device's live screen area. Parent routes
  // through useScrcpySession.handleUploadFile(deviceId, file) so APKs
  // get installed (with signature-mismatch confirmation if needed) and
  // anything else is pushed to /sdcard/Download/. We emit the raw File
  // object rather than handling the install inline so the dialog +
  // retry logic stays in one place (handleUploadFile).
  "upload-file",
]);

// Unique-per-instance HTML ids for the popover anchor + target. Grid
// view renders many DeviceCards at once; if every card used the same
// literal "more-menu" id the browser would only open the first match.
// Derived from deviceId (Android serials are alphanumeric, safe in
// attribute selectors) with a "card-" prefix to dodge potential
// collisions with non-card ids elsewhere on the page.
const moreButtonId = computed(
  () => `card-more-btn-${props.deviceId || "x"}`,
);
const moreMenuId = computed(
  () => `card-more-menu-${props.deviceId || "x"}`,
);

// Fire the swipe but KEEP the D-pad open — the popover is ``manual`` so
// repeated direction presses don't dismiss it (previously each press
// called hidePopover(), forcing the user to reopen "更多" every time).
// The menu is closed explicitly via its ✕ button (closeMoreMenu) or by
// toggling the "更多" trigger again.
function emitSwipe(direction) {
  emit("swipe", direction);
}

function closeMoreMenu() {
  if (typeof document === "undefined") return;
  const el = document.getElementById(moreMenuId.value);
  if (el && typeof el.hidePopover === "function") {
    try {
      el.hidePopover();
    } catch {
      /* already hidden — harmless */
    }
  }
}

// Position the D-pad popover relative to the trigger button + card
// edge. The HTML Popover API renders the menu in the top-layer at the
// viewport origin by default — we anchor it ourselves to whichever
// side of the card has free space.
//
// CSS Anchor Positioning (Chrome 125+) would let us do this
// declaratively, but Safari/Firefox don't ship it yet, so we run JS.
//
// IMPORTANT: button + card positions can change AFTER the popover
// opens — devtools toggle, window resize, scroll, panel collapse,
// rotation animation, etc. A one-shot ``toggle`` handler would leave
// the menu pinned to stale coordinates and visually orphaned. We
// instead attach window resize + capture-phase scroll listeners +
// a ResizeObserver on the card root while the popover is open, all
// dispatched through a single rAF-coalesced ``reposition()`` call so
// rapid events (a window drag) don't trigger one layout per pixel.
let _morePopoverCleanup = null; // unsubscribe fn while menu is open
let _rafToken = 0;

function positionMorePopover() {
  const menu = document.getElementById(moreMenuId.value);
  const btn = document.getElementById(moreButtonId.value);
  if (!menu || !btn) return;
  // Only position while the popover is actually showing.
  if (typeof menu.matches === "function" && !menu.matches(":popover-open")) {
    return;
  }
  const btnRect = btn.getBoundingClientRect();
  const menuRect = menu.getBoundingClientRect();
  const margin = 8;

  // Anchor priority: right of card → left of card → above trigger.
  // The card edge (not the trigger's own rect) is the reference for
  // horizontal placement so the menu hangs off the card frame, not
  // floating arbitrarily in the footer.
  const card = btn.closest(".device-card");
  const cardRect = card ? card.getBoundingClientRect() : btnRect;
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  let left;
  if (cardRect.right + margin + menuRect.width <= vw - margin) {
    left = cardRect.right + margin;
  } else if (cardRect.left - margin - menuRect.width >= margin) {
    left = cardRect.left - margin - menuRect.width;
  } else {
    // Both sides cramped — fall back to centred above the trigger.
    // Will overlap the video, but only on viewports too narrow to
    // fit the D-pad alongside the card.
    left = btnRect.left + btnRect.width / 2 - menuRect.width / 2;
  }

  // Vertical anchor: align the menu's bottom with the trigger's bottom
  // so the D-pad hangs off the bottom-right corner of the card.
  let top = btnRect.bottom - menuRect.height;
  if (top < margin) top = margin;
  if (top + menuRect.height > vh - margin) {
    top = vh - menuRect.height - margin;
  }
  if (left < margin) left = margin;
  if (left + menuRect.width > vw - margin) {
    left = vw - menuRect.width - margin;
  }
  menu.style.top = `${Math.round(top)}px`;
  menu.style.left = `${Math.round(left)}px`;
  menu.style.right = "auto";
  menu.style.bottom = "auto";
  menu.style.margin = "0";
  // Clear the CSS-fallback centring transform — leaving it on would
  // shift the menu by half its own size up-left of the anchor.
  menu.style.transform = "none";
}

function scheduleReposition() {
  if (_rafToken) return;
  _rafToken = requestAnimationFrame(() => {
    _rafToken = 0;
    positionMorePopover();
  });
}

function handleMorePopoverToggle(event) {
  if (event.newState === "open") {
    // Initial position.
    positionMorePopover();
    // Subscribe to every event that can shift the trigger or card.
    // ``capture: true`` on scroll catches ancestor scroll containers
    // (e.g. drawer / grid scrolling) — bubbled scroll events from
    // arbitrary ancestors don't reach window without capture.
    const onScroll = () => scheduleReposition();
    const onResize = () => scheduleReposition();
    window.addEventListener("resize", onResize, { passive: true });
    window.addEventListener("scroll", onScroll, {
      capture: true,
      passive: true,
    });
    // ResizeObserver catches layout-only changes (devtools docking,
    // sidebar collapse, font-loading) that don't fire resize/scroll.
    const card = document
      .getElementById(moreButtonId.value)
      ?.closest(".device-card");
    const ro = card ? new ResizeObserver(scheduleReposition) : null;
    if (ro && card) ro.observe(card);

    _morePopoverCleanup = () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", onScroll, { capture: true });
      if (ro) ro.disconnect();
      if (_rafToken) {
        cancelAnimationFrame(_rafToken);
        _rafToken = 0;
      }
    };
  } else if (event.newState === "closed" && _morePopoverCleanup) {
    _morePopoverCleanup();
    _morePopoverCleanup = null;
  }
}

// If the component is destroyed while the popover is open (HMR reload,
// device unplugged → card removed, route change), tear down listeners
// so we don't leak a resize/scroll subscription against a dead card.
onBeforeUnmount(() => {
  if (_morePopoverCleanup) {
    _morePopoverCleanup();
    _morePopoverCleanup = null;
  }
});

const chipClass = computed(() => {
  if (!props.online) return "idle";
  if (props.streaming) return "ok";
  if (props.frameState === "loading") return "warning";
  return "idle-online";
});
const chipText = computed(() => {
  if (!props.online) return t("connection.disconnected");
  if (props.streaming) return t("connection.connected");
  if (props.frameState === "loading") return t("connection.connecting");
  return t("connection.notStreaming");
});

// ``--card-aspect`` set on the card ROOT so the ``.stage-card`` width
// formula (style.css) can shrink the single-stage chrome to the device
// shape. CSS variables cascade DOWN, so it must live on the card, not
// the inner screen. Priority chain:
//   1. Live ``<video>`` resolutionWidth/Height — reflects current
//      rotation + any user ``wm size`` override, freshest source.
//   2. Static ``staticWidth/Height`` from /api/devices' geometry map —
//      known BEFORE any stream exists, so the placeholder is already
//      correctly shaped on first render.
//   3. 0.45 (~9:20) — modern-phone default for the brief window before
//      the device-list call resolves, or if ``wm size`` failed.
// (The inner pane's own aspect now lives in DeviceScreen.vue.)
const cardAspectStyle = computed(() => {
  const live = safeRatio(props.resolutionWidth, props.resolutionHeight);
  if (live) return { "--card-aspect": String(live) };
  const stat = safeRatio(props.staticWidth, props.staticHeight);
  if (stat) return { "--card-aspect": String(stat) };
  return { "--card-aspect": "0.45" };
});
</script>
