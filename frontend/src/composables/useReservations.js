import { computed, reactive, watch } from "vue";

// Shared module-scoped reservation store. Mirrors useAuth: one reactive
// source of truth so the device cards, the reservation bar and the admin
// panel all stay in sync off a single fetch / socket update.
const state = reactive({
  byDevice: {}, // device_id -> { device_id, username, expires_at, is_mine }
  maxMinutes: 240,
  defaultMinutes: 60,
  loaded: false,
});

let _socketBound = false;

async function _doFetch() {
  try {
    const r = await fetch("/api/reservations");
    const payload = await r.json().catch(() => ({}));
    if (!r.ok || payload.status !== "ok") return;
    const map = {};
    for (const res of payload.reservations || []) {
      if (res) map[res.device_id] = res;
    }
    state.byDevice = map;
    if (payload.max_minutes) state.maxMinutes = payload.max_minutes;
    if (payload.default_minutes) state.defaultMinutes = payload.default_minutes;
  } catch {
    /* leave last-known state on transient failure */
  } finally {
    state.loaded = true;
  }
}

// Coalesce refetches: a single release/claim triggers BOTH an explicit
// refetch AND a socket broadcast (reservation_changed / device_released),
// which would otherwise fire two near-simultaneous GET /api/reservations.
// Collapse all calls within a short window into one request; every caller's
// awaited promise resolves once that single fetch completes. Safe because the
// endpoint returns the full current state, not a delta.
let _debounceTimer = null;
let _waiters = [];
function fetchReservations() {
  return new Promise((resolve) => {
    _waiters.push(resolve);
    if (_debounceTimer) clearTimeout(_debounceTimer);
    _debounceTimer = setTimeout(async () => {
      _debounceTimer = null;
      const waiters = _waiters;
      _waiters = [];
      try {
        await _doFetch();
      } finally {
        waiters.forEach((w) => w());
      }
    }, 120);
  });
}

async function claim(deviceId, minutes) {
  const r = await fetch("/api/reservations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device_id: deviceId, minutes }),
  });
  const payload = await r.json().catch(() => ({}));
  if (!r.ok || payload.status !== "ok") {
    throw new Error(payload.error || "占用失败");
  }
  await fetchReservations();
  return payload.reservation;
}

async function release(deviceId) {
  const r = await fetch(`/api/reservations/${encodeURIComponent(deviceId)}`, {
    method: "DELETE",
  });
  const payload = await r.json().catch(() => ({}));
  if (!r.ok || payload.status !== "ok") {
    throw new Error(payload.error || "释放失败");
  }
  await fetchReservations();
}

// Bind once to the live Socket.IO ref so server-side changes (someone else
// claims, a sweep expires, an admin force-releases) refresh our view.
function bindSocket(socketRef) {
  if (_socketBound) return;
  _socketBound = true;
  const attach = (sock) => {
    if (!sock) return;
    sock.on("reservation_changed", fetchReservations);
    sock.on("device_released", fetchReservations);
  };
  watch(
    () => socketRef?.value,
    (sock) => attach(sock),
    { immediate: true },
  );
}

export function useReservations() {
  function reservationFor(deviceId) {
    return state.byDevice[deviceId] || null;
  }
  return {
    state,
    reservations: computed(() => state.byDevice),
    maxMinutes: computed(() => state.maxMinutes),
    defaultMinutes: computed(() => state.defaultMinutes),
    reservationFor,
    fetchReservations,
    claim,
    release,
    bindSocket,
  };
}
