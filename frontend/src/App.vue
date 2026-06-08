<template>
  <div class="shell">
    <NavRail v-model="activeNav" @open-admin="showAdmin = true" />

    <header class="page-header">
      <div class="page-header-title">
        <h1>
          {{
            activeNav === "settings"
              ? t("navRail.settings")
              : activeNav === "lab"
                ? t("navRail.lab")
                : t("navRail.device")
          }}
        </h1>
      </div>
      <div class="page-header-actions">
        <button class="ghost icon-text" @click="refreshDevices">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <polyline points="23 4 23 10 17 10" />
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
          </svg>
          {{ t("device.refresh") }}
        </button>
        <button
          v-if="visibleDevices.length > 1 && activeNav === 'device'"
          class="ghost icon-text"
          :title="
            viewMode === 'single'
              ? t('viewMode.toggleToGrid')
              : t('viewMode.toggleToSingle')
          "
          @click="toggleViewMode"
        >
          <svg
            v-if="viewMode === 'single'"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <rect x="3" y="3" width="7" height="7" rx="1" />
            <rect x="14" y="3" width="7" height="7" rx="1" />
            <rect x="3" y="14" width="7" height="7" rx="1" />
            <rect x="14" y="14" width="7" height="7" rx="1" />
          </svg>
          <svg
            v-else
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <rect x="5" y="3" width="14" height="18" rx="2" />
          </svg>
          {{
            viewMode === "single" ? t("viewMode.grid") : t("viewMode.single")
          }}
        </button>
        <PreferenceBar />
        <div class="account-chip">
          <span v-if="user?.role" class="account-role">
            {{ t("admin.role_" + user.role) }}
          </span>
          <button class="ghost icon-text" @click="onLogout">
            {{ t("auth.logout") }}
          </button>
        </div>
      </div>
    </header>

    <main class="page-main" v-if="activeNav === 'device'">
      <div v-if="visibleDevices.length === 0" class="empty-devices">
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.2"
          aria-hidden="true"
        >
          <rect x="6" y="2" width="12" height="20" rx="2.5" />
          <line x1="11" y1="18" x2="13" y2="18" stroke-linecap="round" />
        </svg>
        <p>{{ t("device.noneDetected") }}</p>
      </div>

      <template v-else-if="activeNav === 'device' && viewMode === 'single'">
        <div class="stage-layout" v-if="stageDeviceId">
          <aside class="stage-side stage-side-left">
            <ReservationCard
              :device-id="stageDeviceId"
              :online="devices.includes(stageDeviceId)"
              :nudge="reserveNudge"
            />
            <QuickInfoCard :device-id="stageDeviceId" />
            <QuickActionsCard
              :enabled="devices.includes(stageDeviceId)"
              @key="(code) => sendQuickKey(stageDeviceId, code)"
              @screen-on="sendQuickKey(stageDeviceId, 224)"
              @screen-off="sendQuickKey(stageDeviceId, 223)"
            />
          </aside>

          <div class="single-stage">
            <DeviceCard
              :device-id="stageDeviceId"
              :key="stageDeviceId"
              :title="nameOf(stageDeviceId)"
              :streaming="isStreaming(stageDeviceId)"
              :active="true"
              :paused="peekSession(stageDeviceId)?.paused.value || false"
              :muted="false"
              :frame-state="
                peekSession(stageDeviceId)?.frameState.value || 'idle'
              "
              :online="devices.includes(stageDeviceId)"
              :video-stream="videoStreamOf(stageDeviceId)"
              :resolution-width="
                peekSession(stageDeviceId)?.resolutionWidth.value || 0
              "
              :resolution-height="
                peekSession(stageDeviceId)?.resolutionHeight.value || 0
              "
              :static-width="deviceGeometry[stageDeviceId]?.width || 0"
              :static-height="deviceGeometry[stageDeviceId]?.height || 0"
              class="stage-card"
              @select="onStripFocus(stageDeviceId)"
              @focus="onCardFocus(stageDeviceId)"
              @start="onCardStart(stageDeviceId)"
              @stop="onCardStop(stageDeviceId)"
              @toggle-pause="togglePause(stageDeviceId)"
              @toggle-expand="onCardFocus(stageDeviceId)"
              @touch="sendTouch(stageDeviceId, $event)"
              @scroll="sendScroll(stageDeviceId, $event)"
              @back="sendBackKey(stageDeviceId)"
              @home="sendHomeKey(stageDeviceId)"
              @send-key="(code) => sendQuickKey(stageDeviceId, code)"
              @swipe="(dir) => sendSwipe(stageDeviceId, dir)"
              @rotate-device="rotateDevice(stageDeviceId)"
              @media-load="handleMediaLoad(stageDeviceId, $event)"
              @upload-file="(file) => handleUploadFile(stageDeviceId, file)"
            />
          </div>

          <aside class="stage-side stage-side-right">
            <ClipboardCard
              :enabled="isStreaming(stageDeviceId)"
              :remote-content="
                peekSession(stageDeviceId)?.clipboardContent.value || ''
              "
              @refresh="requestClipboard(stageDeviceId)"
              @paste="(text) => pasteBrowserClipboard(stageDeviceId, text)"
              @paste-browser="pasteBrowserClipboard(stageDeviceId, '')"
            />
            <DropZoneCard
              :device-id="stageDeviceId"
              :enabled="isStreaming(stageDeviceId)"
              @upload-file="(file) => handleUploadFile(stageDeviceId, file)"
            />
            <details class="quick-card quick-status">
              <summary>{{ t("panels.streamSettings") }}</summary>
              <StreamSettingsPanel
                :device-id="stageDeviceId"
                :initial-config="streamConfig"
                @applied="handleStreamConfigApplied"
                @adaptive-change="setAdaptiveBitrate"
              />
            </details>
          </aside>
        </div>
        <DeviceStrip
          :devices="visibleDevices"
          :active-device-id="stageDeviceId"
          :streaming-ids="streamingIds"
          :names="deviceNames"
          @focus="onStripFocus"
        />
      </template>

      <template v-else>
      <div ref="gridRef" class="device-grid" :style="gridStyle">
        <DeviceCard
          v-for="id in gridDevices"
          :key="id"
          :device-id="id"
          :title="nameOf(id)"
          :streaming="isStreaming(id)"
          :active="id === focusedDeviceId"
          :expanded="id === expandedDeviceId"
          :paused="peekSession(id)?.paused.value || false"
          :muted="id !== focusedDeviceId"
          :frame-state="peekSession(id)?.frameState.value || 'idle'"
          :online="devices.includes(id)"
          :video-stream="videoStreamOf(id)"
          :resolution-width="peekSession(id)?.resolutionWidth.value || 0"
          :resolution-height="peekSession(id)?.resolutionHeight.value || 0"
          :static-width="deviceGeometry[id]?.width || 0"
          :static-height="deviceGeometry[id]?.height || 0"
          @select="onCardSelect(id)"
          @focus="onCardFocus(id)"
          @start="onCardStart(id)"
          @stop="onCardStop(id)"
          @toggle-pause="togglePause(id)"
          @toggle-expand="onCardToggleExpand(id)"
          @touch="sendTouch(id, $event)"
          @scroll="sendScroll(id, $event)"
          @back="sendBackKey(id)"
          @home="sendHomeKey(id)"
          @send-key="(code) => sendQuickKey(id, code)"
          @swipe="(dir) => sendSwipe(id, dir)"
          @rotate-device="rotateDevice(id)"
          @media-load="handleMediaLoad(id, $event)"
          @upload-file="(file) => handleUploadFile(id, file)"
        >
          <template #reservation>
            <ReservationCard
              :device-id="id"
              :online="devices.includes(id)"
              compact
            />
          </template>
        </DeviceCard>
      </div>
      </template>
    </main>

    <main class="page-main lab-page" v-else-if="activeNav === 'lab'">
      <div class="lab-layout" :class="{ 'lab-split': labHasGroup }">
        <div class="lab-control">
          <SyncPanel
            :devices="visibleDevices"
            :names="deviceNames"
            :ensure-stream="ensureLabStream"
          />
        </div>

        <section v-if="labHasGroup" class="lab-preview">
          <h2 class="lab-preview-label">{{ t("sync.devicePreview") }}</h2>
          <div ref="gridRef" class="device-grid lab-grid" :style="gridStyle">
            <DeviceCard
              v-for="id in gridDevices"
              :key="id"
              :device-id="id"
              :title="nameOf(id)"
              :streaming="isStreaming(id)"
              :active="id === focusedDeviceId"
              :expanded="id === expandedDeviceId"
              :paused="peekSession(id)?.paused.value || false"
              :muted="id !== focusedDeviceId"
              :frame-state="peekSession(id)?.frameState.value || 'idle'"
              :online="devices.includes(id)"
              :video-stream="videoStreamOf(id)"
              :resolution-width="peekSession(id)?.resolutionWidth.value || 0"
              :resolution-height="peekSession(id)?.resolutionHeight.value || 0"
              :static-width="deviceGeometry[id]?.width || 0"
              :static-height="deviceGeometry[id]?.height || 0"
              @select="onCardSelect(id)"
              @focus="onCardFocus(id)"
              @start="onCardStart(id)"
              @stop="onCardStop(id)"
              @toggle-pause="togglePause(id)"
              @toggle-expand="onCardToggleExpand(id)"
              @touch="sendTouch(id, $event)"
              @scroll="sendScroll(id, $event)"
              @back="sendBackKey(id)"
              @home="sendHomeKey(id)"
              @send-key="(code) => sendQuickKey(id, code)"
              @swipe="(dir) => sendSwipe(id, dir)"
              @rotate-device="rotateDevice(id)"
              @media-load="handleMediaLoad(id, $event)"
              @upload-file="(file) => handleUploadFile(id, file)"
            >
              <template #reservation>
                <ReservationCard
                  :device-id="id"
                  :online="devices.includes(id)"
                  compact
                />
              </template>
            </DeviceCard>
          </div>
        </section>
      </div>
    </main>

    <main class="page-main settings-page" v-else>
      <div class="settings-grid">
        <details class="advanced" open>
          <summary>{{ t("panels.connection") }}</summary>
          <div class="content">
            <StatusStrip
              :server-health-class="serverHealthClass"
              :server-health-text="serverHealthText"
              :clipboard-ack-class="clipboardAckClass"
              :clipboard-ack-status="clipboardAckStatus"
              :upload-message-class="uploadMessageClass"
              :upload-message="uploadMessage"
              :upload-busy="uploadBusy"
            />
            <ConnectionSection
              :connection-status-kind="connectionStatusKind"
              :active-stream-count="activeStreamCount"
              :audio-muted="audioMuted"
              :control-status-kind="controlStatusKind"
              :selected-device-id="selectedDeviceId"
              :subscribed-device-id="subscribedDeviceId"
              :resolution-text="resolutionText"
              @toggle-audio="toggleAudio"
              @push-server-jar="pushServerJar"
            />
          </div>
        </details>
      </div>
    </main>

    <div v-if="drawerOpen" class="drawer-scrim" @click="closeDrawer" />

    <DeviceDrawer
      :open="drawerOpen"
      :device-id="focusedDeviceId"
      :title="nameOf(focusedDeviceId)"
      @close="closeDrawer"
    >
      <template #keypad>
        <KeypadPanel
          :device-id="focusedDeviceId"
          v-model:searchText="bindingSearchText"
          v-model:activeGroup="bindingActiveGroup"
          v-model:showEditor="showBindingEditor"
          @send-key="(payload) => sendKey(focusedDeviceId, payload)"
          @send-text="(text) => sendText(focusedDeviceId, text)"
          @set-display-mode="(mode) => setDisplayMode(focusedDeviceId, mode)"
          @toggle-fullscreen="onCardToggleExpand(focusedDeviceId)"
          @toggle-pause="() => togglePause(focusedDeviceId)"
          @resume-pause="() => resumePause(focusedDeviceId)"
          @reset-display-view="() => resetDisplayView(focusedDeviceId)"
          @restart-stream="() => restartStream(focusedDeviceId)"
          @screen-off="() => screenOff(focusedDeviceId)"
          @screen-on="() => screenOn(focusedDeviceId)"
          @notification="() => expandNotification(focusedDeviceId)"
          @settings="() => expandSettings(focusedDeviceId)"
          @collapse-panels="() => collapsePanels(focusedDeviceId)"
          @clipboard-paste="
            (text) => pasteBrowserClipboard(focusedDeviceId, text)
          "
        />
      </template>
      <template #terminal>
        <TerminalPanel :device-id="focusedDeviceId" :socket="socket" />
      </template>
      <template #logcat>
        <LogcatPanel :device-id="drawerOpen ? focusedDeviceId : ''" />
      </template>
      <template #files>
        <DeviceFilesPanel :device-id="drawerOpen ? focusedDeviceId : ''" />
      </template>
      <template #apps>
        <DeviceAppsPanel :device-id="drawerOpen ? focusedDeviceId : ''" />
      </template>
    </DeviceDrawer>

    <AdminPanel v-if="showAdmin" @close="showAdmin = false" />

    <transition name="toast-fade">
      <div v-if="toastMsg" class="app-toast" role="status">{{ toastMsg }}</div>
    </transition>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { useDeviceGrid } from "./composables/useDeviceGrid";
import NavRail from "./components/NavRail.vue";
import PreferenceBar from "./components/PreferenceBar.vue";
import DeviceCard from "./components/DeviceCard.vue";
import DeviceStrip from "./components/DeviceStrip.vue";
import DeviceDrawer from "./components/DeviceDrawer.vue";
import LogcatPanel from "./components/LogcatPanel.vue";
import DeviceFilesPanel from "./components/DeviceFilesPanel.vue";
import DeviceAppsPanel from "./components/DeviceAppsPanel.vue";
import QuickInfoCard from "./components/QuickInfoCard.vue";
import QuickActionsCard from "./components/QuickActionsCard.vue";
import ClipboardCard from "./components/ClipboardCard.vue";
import DropZoneCard from "./components/DropZoneCard.vue";
import ConnectionSection from "./components/ConnectionSection.vue";
import KeypadPanel from "./components/KeypadPanel.vue";
import StatusStrip from "./components/StatusStrip.vue";
import StreamSettingsPanel from "./components/StreamSettingsPanel.vue";
import TerminalPanel from "./components/TerminalPanel.vue";
import ReservationCard from "./components/ReservationCard.vue";
import AdminPanel from "./components/AdminPanel.vue";
import SyncPanel from "./components/SyncPanel.vue";
import { useAppContext } from "./composables/useAppContext";
import { useAuth } from "./composables/useAuth";
import { useReservations } from "./composables/useReservations";
import { useSync } from "./composables/useSync";
import { useScrcpySession } from "./useScrcpySession";

const { t, viewMode, toggleViewMode } = useAppContext();
const { user, isAdmin, logout } = useAuth();
const { bindSocket, fetchReservations, reservationFor, reservations } =
  useReservations();
const showAdmin = ref(false);

// Lightweight, view-independent toast (e.g. "reserve the device first").
const toastMsg = ref("");
let toastTimer = null;
function showToast(msg) {
  toastMsg.value = msg;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toastMsg.value = ""), 3200);
}
// Bumped to make the reservation card pulse and hint "reserve first".
const reserveNudge = ref(0);
// A control action is allowed only when the current user holds the device's
// reservation (admins included). Mirrors backend reservations.assert_owner:
// an UNRESERVED device blocks everyone until someone claims it.
function canControl(deviceId) {
  const r = reservationFor(deviceId);
  return !!r && (r.is_mine || isAdmin.value);
}
const activeNav = ref("device");
const focusedDeviceId = ref(""); // device the drawer/keypad operates on
const expandedDeviceId = ref(""); // card enlarged inside the grid (grid view only)
const stripFocusId = ref(""); // single-view: which device fills the stage
const drawerOpen = ref(false);
const gridRef = ref(null);

const {
  devices,
  deviceNames,
  deviceGeometry,
  nameOf,
  selectedDeviceId,
  subscribedDeviceId,
  textValue,
  uploadBusy,
  uploadMessage,
  uploadMessageClass,
  clipboardAckStatus,
  clipboardAckClass,
  serverHealthText,
  serverHealthClass,
  audioMuted,
  bindingSearchText,
  bindingActiveGroup,
  showBindingEditor,
  connectionStatusKind,
  activeStreamCount,
  controlStatusKind,
  resolutionText,
  // per-device accessors
  getSession,
  peekSession,
  isStreaming,
  videoStreamOf,
  // lifecycle
  refreshDevices,
  startDevice,
  stopDevice,
  restartStream,
  pushServerJar,
  // input
  sendKey,
  sendQuickKey,
  sendSwipe,
  sendTouch,
  sendScroll,
  sendBackKey,
  sendHomeKey,
  sendText,
  // display
  setDisplayMode,
  togglePause,
  resumePause,
  resetDisplayView,
  screenOff,
  screenOn,
  expandNotification,
  expandSettings,
  collapsePanels,
  rotateDevice,
  toggleAudio,
  handleMediaLoad,
  // upload / clipboard
  handleUploadFile,
  requestClipboard,
  pasteBrowserClipboard,
  // settings panel data
  streamConfig,
  refreshStreamConfig,
  setAdaptiveBitrate,
  socket,
} = useScrcpySession();

// Reservation store: hydrate once and keep it live off the signaling socket
// (reservation_changed / device_released broadcasts).
bindSocket(socket);
const { fetchGroups: fetchSyncGroups, groups: syncGroups } = useSync();
onMounted(() => {
  fetchReservations();
  // Probe the experimental sync feature + hydrate any existing group. Safe
  // no-op when the backend has the feature off (endpoint 404s).
  fetchSyncGroups();
});

async function onLogout() {
  await logout();
  // useAuth state flips -> AppRoot swaps back to the login screen.
}

// Anti-hogging UX: once a NON-admin holds a reservation they're "locked" to
// that one device — every other device is hidden from their grid / strip /
// stage so they can't browse or grab others (the backend enforces the hard
// rule; this just keeps the UI honest). Admins see everything.
// Devices the current user holds. Normally 0 or 1 (anti-hogging), but the
// experimental sync feature lets one user batch-reserve a master + slaves,
// so this can legitimately hold several at once.
const myReservedIds = computed(() =>
  Object.values(reservations.value || {})
    .filter((r) => r && r.is_mine)
    .map((r) => r.device_id),
);
const lockedDeviceId = computed(() => {
  if (isAdmin.value) return "";
  const mine = myReservedIds.value;
  // Only lock-to-one under the single-device rule. A multi-device sync group
  // is exempt (handled in visibleDevices below).
  return mine.length === 1 ? mine[0] : "";
});
const visibleDevices = computed(() => {
  const all = devices.value;
  if (isAdmin.value) return all;
  const mine = myReservedIds.value;
  // Sync group: show exactly the user's reserved members (master + slaves).
  if (mine.length > 1) return all.filter((id) => mine.includes(id));
  const locked = lockedDeviceId.value;
  return locked && all.includes(locked) ? [locked] : all;
});

// In the Lab preview, once a sync group exists collapse the grid to just its
// members (master + slaves) so a 10-20 device fleet doesn't all render/stream
// during sync. Outside Lab (or before a group exists) the grid is unchanged.
const labHasGroup = computed(() => !!(syncGroups.value || []).find((g) => g.is_mine));

// Lab: ensure a member streams, but DON'T re-start one that's already live —
// re-offering a healthy (often the master, which you're viewing while building
// the group) triggers scrcpy's reset_video re-negotiation path, which stalls
// its video (remote track goes muted). Only (re)start devices not yet ready.
function ensureLabStream(id) {
  if (isStreaming(id)) return Promise.resolve();
  return startDevice(id);
}
const labMemberIds = computed(() => {
  const mine = (syncGroups.value || []).find((g) => g.is_mine);
  return mine ? [mine.master_device_id, ...mine.slave_device_ids] : null;
});
const gridDevices = computed(() => {
  if (activeNav.value === "lab" && labMemberIds.value) {
    return visibleDevices.value.filter((id) => labMemberIds.value.includes(id));
  }
  return visibleDevices.value;
});

// Adaptive grid: picks (cols, cardWidth) to balance visible card area
// against device count + container size. Re-evaluates on ResizeObserver
// and on device-count change. See composables/useDeviceGrid.js.
const { layout } = useDeviceGrid(
  () => gridDevices.value.length,
  () => gridRef.value,
);
const gridStyle = computed(() => ({
  "--grid-cols": layout.value.cols,
  "--grid-card-max-w": `${layout.value.cardWidth}px`,
}));

// Device that fills the single-stage area. Priority:
//   1) explicit strip click (`stripFocusId`)
//   2) focused device (drawer's target)
//   3) most-recently-started (subscribedDeviceId)
//   4) first detected device
const stageDeviceId = computed(() => {
  const candidates = [
    stripFocusId.value,
    focusedDeviceId.value,
    subscribedDeviceId.value,
  ];
  for (const id of candidates) {
    if (id && visibleDevices.value.includes(id)) return id;
  }
  return visibleDevices.value[0] || "";
});

// Reactive list of device IDs whose PeerConnection is up and streaming —
// used by DeviceStrip to render a green dot.
const streamingIds = computed(() =>
  devices.value.filter((id) => isStreaming(id)),
);

function onStripFocus(deviceId) {
  stripFocusId.value = deviceId;
  focusedDeviceId.value = deviceId;
}

function handleStreamConfigApplied(payload) {
  refreshStreamConfig?.(payload?.config);
}

// `select` = soft highlight (header single-click). Does NOT open the
// drawer — drawer is intrusive and was being spuriously summoned by
// swipes inside the screen area.
function onCardSelect(deviceId) {
  focusedDeviceId.value = deviceId;
}

// `focus` = user explicitly asked to open the drawer for this device
// (⋮ button or header double-click).
function onCardFocus(deviceId) {
  focusedDeviceId.value = deviceId;
  drawerOpen.value = true;
}

function closeDrawer() {
  drawerOpen.value = false;
}

async function onCardStart(deviceId) {
  // Guide the user before they hit the backend 403: streaming requires
  // holding the device reservation. Nudge them to "Reserve" first.
  if (!canControl(deviceId)) {
    showToast(t("reservation.reserveFirst"));
    reserveNudge.value++;
    return;
  }
  selectedDeviceId.value = deviceId;
  focusedDeviceId.value = deviceId;
  await startDevice(deviceId);
}

async function onCardStop(deviceId) {
  await stopDevice(deviceId);
}

// In-page "enlarge this card" toggle. Spans the card across the grid so
// the video region grows without dumping the user into browser fullscreen.
function onCardToggleExpand(deviceId) {
  expandedDeviceId.value = expandedDeviceId.value === deviceId ? "" : deviceId;
}

// Drawer text composer sends to the focused device.
function onDrawerSendText() {
  const id = focusedDeviceId.value;
  const text = (textValue.value || "").trim();
  if (!id || !text) return;
  sendText(id, text);
  textValue.value = "";
}
</script>
