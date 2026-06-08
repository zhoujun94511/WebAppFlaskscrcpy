import { computed, reactive } from "vue";

// Shared store for the experimental master→slave sync lab feature.
// Mirrors useReservations' single-reactive-source pattern. The whole
// feature is opt-in on the backend (ENABLE_SYNC=1); when off, the
// /api/sync-groups endpoint 404s and `available` stays false so the UI
// hides every sync control.
const state = reactive({
  available: false, // backend feature flag detected
  probed: false, // have we tried at least once
  groups: [], // [{ id, master_device_id, slave_device_ids, enabled, is_mine, ... }]
  live: null, // status snapshot from the backend
});

// Module-scope set of device ids currently acting as a sync SLAVE, exposed
// as a plain getter so useScrcpySession can make those devices read-only
// (block manual input) without importing Vue reactivity or creating a cycle.
const _slaveIds = new Set();

function _rebuildSlaveIds() {
  _slaveIds.clear();
  for (const g of state.groups) {
    if (!g || !g.enabled) continue;
    for (const sid of g.slave_device_ids || []) _slaveIds.add(sid);
  }
}

// Read by useScrcpySession.emitTo — keep it dependency-free and synchronous.
export function isSyncSlave(deviceId) {
  return _slaveIds.has(deviceId);
}

async function fetchGroups() {
  try {
    const r = await fetch("/api/sync-groups");
    if (r.status === 404) {
      state.available = false;
      state.probed = true;
      return;
    }
    const payload = await r.json().catch(() => ({}));
    if (!r.ok || payload.status !== "ok") {
      state.probed = true;
      return;
    }
    state.available = true;
    state.groups = payload.groups || [];
    state.live = payload.live || null;
    _rebuildSlaveIds();
  } catch {
    /* leave last-known state on transient failure */
  } finally {
    state.probed = true;
  }
}

async function createGroup({
  masterDeviceId,
  slaveDeviceIds,
  mode = "coordinate",
  syncTouch = true,
  syncKeyevent = true,
  syncText = true,
}) {
  const r = await fetch("/api/sync-groups", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      master_device_id: masterDeviceId,
      slave_device_ids: slaveDeviceIds,
      mode,
      sync_touch: syncTouch,
      sync_keyevent: syncKeyevent,
      sync_text: syncText,
    }),
  });
  const payload = await r.json().catch(() => ({}));
  if (!r.ok || payload.status !== "ok") {
    throw new Error(payload.error || "创建同步组失败");
  }
  await fetchGroups();
  return payload.group;
}

async function deleteGroup(groupId) {
  const r = await fetch(`/api/sync-groups/${encodeURIComponent(groupId)}`, {
    method: "DELETE",
  });
  const payload = await r.json().catch(() => ({}));
  if (!r.ok || payload.status !== "ok") {
    throw new Error(payload.error || "停止同步失败");
  }
  await fetchGroups();
}

async function setEnabled(groupId, enabled) {
  const action = enabled ? "enable" : "disable";
  const r = await fetch(
    `/api/sync-groups/${encodeURIComponent(groupId)}/${action}`,
    { method: "POST" },
  );
  const payload = await r.json().catch(() => ({}));
  if (!r.ok || payload.status !== "ok") {
    throw new Error(payload.error || "切换同步状态失败");
  }
  await fetchGroups();
}

export function useSync() {
  return {
    state,
    available: computed(() => state.available),
    groups: computed(() => state.groups),
    live: computed(() => state.live),
    fetchGroups,
    createGroup,
    deleteGroup,
    setEnabled,
    isSyncSlave,
  };
}
