<template>
  <!-- Experimental lab feature: only shown when the backend has ENABLE_SYNC on
       (probed via /api/sync-groups). Fully self-contained + theme-aware. -->
  <section v-if="available" class="sync-panel">
    <header class="sync-head">
      <h2 class="sync-title">
        {{ t("sync.title") }}<span class="sync-badge">{{ t("sync.badge") }}</span>
      </h2>
      <span
        v-if="myGroup"
        class="sync-status"
        :class="myGroup.enabled ? 'on' : 'paused'"
      >
        {{ myGroup.enabled ? t("sync.statusOn") : t("sync.statusPaused") }}
      </span>
    </header>

    <!-- ════════ Active group view ════════ -->
    <div v-if="myGroup" class="sync-active">
      <div class="active-meta">
        <span class="mode-badge">{{
          myGroup.mode === "semantic"
            ? t("sync.modeBadgeSemantic")
            : t("sync.modeBadgeCoordinate")
        }}</span>
        <span class="mode-badge-sub">{{ t("sync.activeHint") }}</span>
      </div>
      <div class="member-line">
        <span class="role-tag master">{{ t("sync.roleMaster") }}</span>
        <span class="member-name">{{ nameOf(myGroup.master_device_id) }}</span>
        <span class="member-arrow" aria-hidden="true">→</span>
        <span
          v-for="sid in myGroup.slave_device_ids"
          :key="sid"
          class="slave-chip"
          :class="slaveClass(sid)"
          :title="nameOf(sid)"
        >
          <span class="slave-dot" />{{ nameOf(sid) }}
          <span class="slave-res">{{ slaveLabel(sid) }}</span>
        </span>
      </div>

      <div class="sync-actions">
        <button
          v-if="myGroup.enabled"
          class="btn btn-ghost"
          :disabled="busy"
          @click="toggle(false)"
        >
          {{ t("sync.pause") }}
        </button>
        <button v-else class="btn btn-primary" :disabled="busy" @click="toggle(true)">
          {{ t("sync.resume") }}
        </button>
        <template v-if="!confirmingStop">
          <button class="btn btn-ghost danger" :disabled="busy" @click="confirmingStop = true">
            {{ t("sync.stop") }}
          </button>
        </template>
        <template v-else>
          <button class="btn btn-danger" :disabled="busy" @click="stop">
            {{ t("sync.confirmStop") }}
          </button>
          <button class="btn btn-ghost" :disabled="busy" @click="confirmingStop = false">
            {{ t("sync.cancel") }}
          </button>
          <span class="muted-hint">{{ t("sync.stopHint") }}</span>
        </template>
      </div>
    </div>

    <!-- ════════ Builder view ════════ -->
    <div v-else class="sync-build">
      <p class="sync-sub">{{ t("sync.subtitle") }}</p>

      <div v-if="devices.length < 2" class="sync-empty">
        {{ t("sync.needTwo") }}
      </div>

      <template v-else>
        <div class="sync-toolbar">
          <input
            v-if="devices.length > 6"
            v-model="query"
            class="sync-search"
            type="search"
            :placeholder="t('sync.searchPlaceholder', { count: devices.length })"
            :aria-label="t('sync.searchPlaceholder', { count: devices.length })"
          />
          <div class="bulk">
            <button
              v-if="devices.length > 2"
              type="button"
              class="mini-btn"
              @click="selectAllSlaves"
            >
              {{ t("sync.bulkSlaves") }}
            </button>
            <button
              type="button"
              class="mini-btn"
              :disabled="!masterId && slaveSet.size === 0"
              @click="clearSelection"
            >
              {{ t("sync.clear") }}
            </button>
          </div>
        </div>

        <div class="dev-scroll">
          <div class="dev-table">
            <div
              v-for="id in filteredDevices"
              :key="id"
              class="dev-row"
              :class="{ 'is-master': isMaster(id), 'is-slave': isSlave(id) }"
            >
              <span class="dev-name">{{ nameOf(id) }}</span>
              <div class="seg" role="group" :aria-label="nameOf(id)">
                <button
                  type="button"
                  class="seg-btn"
                  :class="{ on: isMaster(id) }"
                  :aria-pressed="isMaster(id)"
                  @click="selectMaster(id)"
                >
                  {{ t("sync.roleMaster") }}
                </button>
                <button
                  type="button"
                  class="seg-btn slave"
                  :class="{ on: isSlave(id) }"
                  :aria-pressed="isSlave(id)"
                  :disabled="isMaster(id)"
                  @click="toggleSlave(id)"
                >
                  {{ t("sync.roleSlave") }}
                </button>
              </div>
            </div>
            <div v-if="filteredDevices.length === 0" class="dev-none">
              {{ t("sync.noMatch") }}
            </div>
          </div>
        </div>

        <div class="sync-row-inline">
          <span class="inline-label">{{ t("sync.modeLabel") }}</span>
          <div class="seg mode-seg" role="group" :aria-label="t('sync.modeLabel')">
            <button
              type="button"
              class="seg-btn"
              :class="{ on: syncMode === 'semantic' }"
              :aria-pressed="syncMode === 'semantic'"
              @click="syncMode = 'semantic'"
            >
              {{ t("sync.modeSemantic") }}
            </button>
            <button
              type="button"
              class="seg-btn"
              :class="{ on: syncMode === 'coordinate' }"
              :aria-pressed="syncMode === 'coordinate'"
              @click="syncMode = 'coordinate'"
            >
              {{ t("sync.modeCoordinate") }}
            </button>
          </div>
          <span class="mode-hint">{{
            syncMode === "semantic"
              ? t("sync.modeSemanticHint")
              : t("sync.modeCoordinateHint")
          }}</span>
        </div>

        <div class="sync-row-inline">
          <span class="inline-label">{{ t("sync.contentLabel") }}</span>
          <div class="chips">
            <button
              type="button"
              class="chip"
              :class="{ on: syncTouch }"
              :aria-pressed="syncTouch"
              @click="syncTouch = !syncTouch"
            >
              {{ t("sync.contentTouch") }}
            </button>
            <button
              type="button"
              class="chip"
              :class="{ on: syncKeyevent }"
              :aria-pressed="syncKeyevent"
              @click="syncKeyevent = !syncKeyevent"
            >
              {{ t("sync.contentKey") }}
            </button>
            <button
              type="button"
              class="chip"
              :class="{ on: syncText }"
              :aria-pressed="syncText"
              @click="syncText = !syncText"
            >
              {{ t("sync.contentText") }}
            </button>
          </div>
        </div>

        <div class="sync-foot">
          <button class="btn btn-primary" :disabled="!canStart || busy" @click="start">
            {{ busy ? t("sync.starting") : t("sync.start") }}
          </button>
          <span class="muted-hint" :class="{ ready: canStart }">{{ startHint }}</span>
        </div>
      </template>
    </div>

    <transition name="sync-alert">
      <p v-if="message" class="sync-alert" :class="isError ? 'err' : 'ok'" role="status">
        {{ message }}
      </p>
    </transition>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useSync } from "../composables/useSync";
import { useUiI18n } from "../composables/useUiI18n";

const { t } = useUiI18n();

const props = defineProps({
  devices: { type: Array, default: () => [] },
  names: { type: Object, default: () => ({}) },
  // callback(deviceId) -> Promise; used to ensure each group member streams.
  ensureStream: { type: Function, default: null },
});

const { available, groups, live, fetchGroups, createGroup, deleteGroup, setEnabled } =
  useSync();

const masterId = ref("");
const slaveSet = reactive(new Set());
const query = ref("");
const syncMode = ref("semantic"); // 'semantic' | 'coordinate'
const syncTouch = ref(true);
const syncKeyevent = ref(true);
const syncText = ref(true);
const busy = ref(false);
const message = ref("");
const isError = ref(false);
const confirmingStop = ref(false);

onMounted(fetchGroups);

// The single group the current user owns (one master per user in practice).
const myGroup = computed(() => groups.value.find((g) => g.is_mine) || null);

const canStart = computed(() => !!masterId.value && slaveSet.size > 0);

const startHint = computed(() => {
  if (!masterId.value) return t("sync.hintNoMaster");
  if (slaveSet.size === 0) return t("sync.hintNoSlave");
  return t("sync.hintReady", { count: slaveSet.size });
});

// Search-filter the device list so large fleets (10-20+) stay navigable.
// Selection lives in masterId/slaveSet and persists across filtering.
const filteredDevices = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return props.devices;
  return props.devices.filter((id) => nameOf(id).toLowerCase().includes(q));
});

function nameOf(id) {
  return props.names[id] || id;
}
function isMaster(id) {
  return masterId.value === id;
}
function isSlave(id) {
  return slaveSet.has(id);
}

function isLive(deviceId) {
  const snap = live.value;
  if (!snap || !snap.groups) return false;
  for (const g of snap.groups) {
    if (g.members_live && deviceId in g.members_live) return g.members_live[deviceId];
  }
  return false;
}

// Per-slave last execution result (from the backend's in-memory snapshot).
function resultFor(sid) {
  return live.value?.results?.[sid] || null;
}
function slaveClass(sid) {
  const r = resultFor(sid);
  if (r) {
    if (!r.success) return "dead";
    return r.strategy && r.strategy.startsWith("fallback") ? "warn" : "ok";
  }
  return isLive(sid) ? "live" : "dead";
}
function slaveLabel(sid) {
  const r = resultFor(sid);
  if (r) {
    if (!r.success) return t("sync.resFailed");
    const s = r.strategy || "";
    if (s.startsWith("semantic:")) return t("sync.resSemantic", { strategy: s.slice(9) });
    if (s === "fallback") return t("sync.resFallback");
    if (s.startsWith("coordinate")) return t("sync.resCoordinate");
    return t("sync.resOnline");
  }
  return isLive(sid) ? t("sync.resOnline") : t("sync.resOffline");
}

// Poll the live snapshot only while a group is active, so per-slave results
// refresh as the user drives the master. Stops when no group / unmounted.
let _pollTimer = null;
function _stopPoll() {
  if (_pollTimer) {
    clearInterval(_pollTimer);
    _pollTimer = null;
  }
}
watch(
  () => !!myGroup.value,
  (active) => {
    _stopPoll();
    if (active) _pollTimer = setInterval(fetchGroups, 1500);
  },
  { immediate: true },
);
onBeforeUnmount(_stopPoll);

function selectMaster(id) {
  // Toggle: clicking the current master clears it. A master can't be a slave.
  if (masterId.value === id) {
    masterId.value = "";
    return;
  }
  masterId.value = id;
  if (slaveSet.has(id)) slaveSet.delete(id);
}

function toggleSlave(id) {
  if (masterId.value === id) return; // guarded in UI too
  if (slaveSet.has(id)) slaveSet.delete(id);
  else slaveSet.add(id);
}

// Bulk helpers — essential once there are many devices.
function selectAllSlaves() {
  for (const id of props.devices) {
    if (id !== masterId.value) slaveSet.add(id);
  }
}
function clearSelection() {
  masterId.value = "";
  slaveSet.clear();
}

function setMessage(msg, error = false) {
  message.value = msg;
  isError.value = error;
}

async function start() {
  if (!canStart.value) return;
  busy.value = true;
  setMessage("");
  const slaveDeviceIds = [...slaveSet];
  try {
    await createGroup({
      masterDeviceId: masterId.value,
      slaveDeviceIds,
      mode: syncMode.value,
      syncTouch: syncTouch.value,
      syncKeyevent: syncKeyevent.value,
      syncText: syncText.value,
    });
    if (props.ensureStream) {
      // Start member streams in parallel, but capped so a large group doesn't
      // spike CPU/bandwidth by negotiating + decoding everything at once.
      // Small groups (≤ cap) start fully in parallel — no serial wait.
      const members = [masterId.value, ...slaveDeviceIds];
      const CONCURRENCY = 4;
      for (let i = 0; i < members.length; i += CONCURRENCY) {
        await Promise.all(
          members
            .slice(i, i + CONCURRENCY)
            .map((id) => Promise.resolve(props.ensureStream(id)).catch(() => {})),
        );
      }
    }
    await fetchGroups();
    setMessage(t("sync.started"));
  } catch (err) {
    setMessage(err.message || t("sync.errStart"), true);
  } finally {
    busy.value = false;
  }
}

async function stop() {
  if (!myGroup.value) return;
  busy.value = true;
  setMessage("");
  try {
    await deleteGroup(myGroup.value.id);
    masterId.value = "";
    slaveSet.clear();
    confirmingStop.value = false;
    setMessage(t("sync.stopped"));
  } catch (err) {
    setMessage(err.message || t("sync.errStop"), true);
  } finally {
    busy.value = false;
  }
}

async function toggle(enabled) {
  if (!myGroup.value) return;
  busy.value = true;
  setMessage("");
  try {
    await setEnabled(myGroup.value.id, enabled);
  } catch (err) {
    setMessage(err.message || t("sync.errToggle"), true);
  } finally {
    busy.value = false;
  }
}
</script>

<style scoped>
.sync-panel {
  max-width: 560px;
  border: 1px solid var(--panel-border);
  border-radius: 14px;
  padding: 14px 16px;
  margin-bottom: 16px;
  background: var(--panel-bg);
  box-shadow: var(--shadow-soft);
  color: var(--text);
}

/* ── header: the title is the loudest thing here ── */
.sync-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.sync-title {
  margin: 0;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--text-strong);
}
.sync-badge {
  font-size: 0.62rem;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--warning-bg);
  color: var(--warning-fg);
  border: 1px solid var(--warning-border);
}
.sync-status {
  font-size: 0.72rem;
  font-weight: 600;
  padding: 2px 9px;
  border-radius: 999px;
  border: 1px solid var(--border);
}
.sync-status.on {
  background: var(--ok-bg);
  color: var(--ok-fg);
  border-color: var(--ok-border);
}
.sync-status.paused {
  background: var(--warning-bg);
  color: var(--warning-fg);
  border-color: var(--warning-border);
}
.sync-sub {
  margin: 6px 0 12px;
  font-size: 0.76rem;
  color: var(--muted);
}

/* ── builder toolbar: search + bulk (scales to many devices) ── */
.sync-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.sync-search {
  flex: 1;
  min-width: 0;
  padding: 5px 10px;
  font-size: 0.78rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--surface-input);
  color: var(--text);
}
.bulk {
  display: flex;
  gap: 6px;
  flex: none;
  margin-left: auto;
}
.mini-btn {
  padding: 4px 10px;
  font-size: 0.72rem;
  font-weight: 500;
  border-radius: 8px;
  border: 1px solid var(--border-strong);
  background: transparent;
  color: var(--text);
  cursor: pointer;
  transition: background 0.15s ease;
}
.mini-btn:hover:not(:disabled) {
  background: var(--surface-2);
}
.mini-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* ── builder: scroll-bounded, quiet divided rows (matches admin table) ── */
.dev-scroll {
  max-height: 240px;
  overflow-y: auto;
  border: 1px solid var(--border-soft);
  border-radius: 10px;
  margin-bottom: 12px;
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
}
.dev-scroll::-webkit-scrollbar {
  width: 6px;
}
.dev-scroll::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 3px;
}
.dev-none {
  padding: 14px;
  text-align: center;
  font-size: 0.78rem;
  color: var(--muted);
}
.dev-table {
  overflow: hidden;
}
.dev-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 7px 12px;
  background: transparent;
  box-shadow: inset 0 0 0 0 transparent;
  transition: box-shadow 0.15s ease, background 0.15s ease;
}
.dev-row + .dev-row {
  border-top: 1px solid var(--border-soft);
}
.dev-row.is-master {
  box-shadow: inset 3px 0 0 0 var(--primary);
  background: var(--primary-ring);
}
.dev-row.is-slave {
  box-shadow: inset 3px 0 0 0 var(--ok-fg);
}
.dev-name {
  font-size: 0.84rem;
  font-weight: 500;
  color: var(--text);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dev-row.is-master .dev-name,
.dev-row.is-slave .dev-name {
  color: var(--text-strong);
}

/* compact segmented role control */
.seg {
  display: inline-flex;
  flex: none;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid var(--border);
}
.seg-btn {
  padding: 3px 11px;
  font-size: 0.72rem;
  font-weight: 600;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.seg-btn + .seg-btn {
  border-left: 1px solid var(--border);
}
.seg-btn:hover:not(:disabled) {
  background: var(--surface-2);
  color: var(--text);
}
.seg .seg-btn.on {
  background: var(--primary);
  color: var(--text-on-primary);
}
.seg .seg-btn.slave.on {
  background: var(--ok-fg);
  color: #06281a;
}
.seg-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* ── sync-content: secondary, quiet (no loud default fill) ── */
.sync-row-inline {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}
.inline-label {
  font-size: 0.74rem;
  color: var(--muted);
  flex: none;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.chip {
  padding: 3px 10px;
  font-size: 0.72rem;
  font-weight: 500;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  transition: all 0.15s ease;
}
.chip.on {
  border-color: var(--primary-border);
  color: var(--primary);
  font-weight: 600;
}

/* ── footer ── */
.sync-foot,
.sync-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}
.muted-hint {
  font-size: 0.74rem;
  color: var(--muted);
}
.muted-hint.ready {
  color: var(--ok-fg);
}

/* ── active view (compact, single line) ── */
.member-line {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin: 4px 0 12px;
  font-size: 0.82rem;
}
.role-tag {
  font-size: 0.62rem;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 5px;
}
.role-tag.master {
  background: var(--primary);
  color: var(--text-on-primary);
}
.member-name {
  font-weight: 600;
  color: var(--text-strong);
}
.member-arrow {
  color: var(--muted);
}
.slave-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 9px;
  border-radius: 999px;
  border: 1px solid var(--border);
  font-size: 0.76rem;
}
.slave-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex: none;
}
.slave-chip.live .slave-dot,
.slave-chip.ok .slave-dot {
  background: var(--ok-fg);
}
.slave-chip.warn .slave-dot {
  background: var(--warning-fg);
}
.slave-chip.dead .slave-dot {
  background: var(--danger-fg);
}
.slave-chip.ok {
  border-color: var(--ok-border);
}
.slave-chip.warn {
  border-color: var(--warning-border);
}
.slave-chip.dead {
  border-color: var(--danger-border);
}
.slave-res {
  font-size: 0.68rem;
  color: var(--muted);
}
.slave-chip.ok .slave-res {
  color: var(--ok-fg);
}
.slave-chip.warn .slave-res {
  color: var(--warning-fg);
}
.slave-chip.dead .slave-res {
  color: var(--danger-fg);
}

/* active-view meta + mode badge */
.active-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.mode-badge {
  font-size: 0.7rem;
  font-weight: 600;
  padding: 2px 9px;
  border-radius: 999px;
  background: var(--primary-ring);
  color: var(--primary);
  border: 1px solid var(--primary-border);
}
.mode-badge-sub {
  font-size: 0.74rem;
  color: var(--muted);
}

/* builder mode selector */
.mode-seg {
  flex: none;
}
.mode-hint {
  font-size: 0.74rem;
  color: var(--muted);
}

/* ── empty / buttons / alert ── */
.sync-empty {
  font-size: 0.8rem;
  color: var(--muted);
  padding: 12px;
  border: 1px dashed var(--border);
  border-radius: 10px;
  text-align: center;
}
.btn {
  padding: 6px 14px;
  font-size: 0.78rem;
  font-weight: 600;
  border-radius: 9px;
  border: 1px solid transparent;
  cursor: pointer;
  transition: filter 0.15s ease, background 0.15s ease, opacity 0.15s ease;
}
.btn-primary {
  background: var(--primary);
  color: var(--text-on-primary);
}
.btn-primary:hover:not(:disabled) {
  filter: brightness(1.08);
}
.btn-ghost {
  background: transparent;
  border-color: var(--border-strong);
  color: var(--text);
}
.btn-ghost:hover:not(:disabled) {
  background: var(--surface-2);
}
.btn-ghost.danger {
  color: var(--danger-fg);
  border-color: var(--danger-border);
}
.btn-danger {
  background: var(--danger-fg);
  color: #fff;
}
.btn:disabled {
  opacity: 0.48;
  cursor: not-allowed;
}
.sync-alert {
  margin: 12px 0 0;
  padding: 7px 11px;
  border-radius: 9px;
  font-size: 0.78rem;
  border: 1px solid transparent;
}
.sync-alert.ok {
  background: var(--ok-bg);
  color: var(--ok-fg);
  border-color: var(--ok-border);
}
.sync-alert.err {
  background: var(--danger-bg);
  color: var(--danger-fg);
  border-color: var(--danger-border);
}
.sync-alert-enter-active,
.sync-alert-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.sync-alert-enter-from,
.sync-alert-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

@media (max-width: 520px) {
  .dev-row {
    flex-direction: column;
    align-items: stretch;
    gap: 8px;
  }
  .seg {
    width: 100%;
  }
  .seg-btn {
    flex: 1;
  }
  .sync-row-inline {
    align-items: flex-start;
    flex-direction: column;
    gap: 6px;
  }
}
</style>
