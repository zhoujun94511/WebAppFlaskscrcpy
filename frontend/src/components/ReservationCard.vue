<template>
  <div
    class="quick-card reservation-card"
    :class="{ 'is-expiring': expiringSoon, 'is-nudge': nudging, compact }"
  >
    <div class="reservation-head">
      <span v-if="!compact" class="reservation-title">{{
        t("reservation.title")
      }}</span>
      <span class="reservation-badge" :class="badgeClass">{{ badgeText }}</span>
    </div>

    <!-- Free → claim with a chosen duration -->
    <template v-if="!res">
      <p v-if="nudging" class="reservation-warn">
        {{ t("reservation.reserveFirst") }}
      </p>
      <!-- Free state: duration picker + claim on ONE row, mirroring the
           two-control row of the held-by-me branch (extend / release) so the
           card's layout stays consistent across all three states. -->
      <div class="reservation-claim-row">
        <select
          class="reservation-select"
          v-model.number="minutes"
          :aria-label="t('reservation.duration')"
          :title="t('reservation.duration')"
        >
          <option v-for="m in durationOptions" :key="m" :value="m">
            {{ formatDuration(m) }}
          </option>
        </select>
        <button
          class="reservation-btn primary reservation-claim-btn"
          :disabled="!online || busy"
          @click="onClaim"
        >
          {{ t("reservation.claim") }}
        </button>
      </div>
    </template>

    <!-- Held by me → countdown + release / extend -->
    <template v-else-if="res.is_mine">
      <p class="reservation-info">
        {{ t("reservation.heldByYou") }} · {{ remainingText }}
      </p>
      <p v-if="expiringSoon" class="reservation-warn">
        {{ t("reservation.expiringSoonHint") }}
      </p>
      <div class="reservation-actions">
        <button
          class="reservation-btn"
          :class="{ primary: expiringSoon }"
          :disabled="busy"
          @click="onExtend"
        >
          {{ t("reservation.extend") }}
        </button>
        <button
          class="reservation-btn danger"
          :disabled="busy"
          @click="onRelease"
        >
          {{ t("reservation.release") }}
        </button>
      </div>
    </template>

    <!-- Held by someone else -->
    <template v-else>
      <p class="reservation-info">
        {{ t("reservation.heldBy", { user: res.username }) }} · {{ remainingText }}
      </p>
      <button
        v-if="isAdmin"
        class="reservation-btn danger"
        :disabled="busy"
        @click="onRelease"
      >
        {{ t("reservation.forceRelease") }}
      </button>
    </template>

    <p v-if="error" class="reservation-error">{{ error }}</p>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useAppContext } from "../composables/useAppContext";
import { useAuth } from "../composables/useAuth";
import { useReservations } from "../composables/useReservations";

const props = defineProps({
  deviceId: { type: String, required: true },
  online: { type: Boolean, default: true },
  // Bumped by the parent to flash a "reserve first" hint + pulse the card.
  nudge: { type: Number, default: 0 },
  // Condensed single-row layout for embedding inside a grid DeviceCard.
  compact: { type: Boolean, default: false },
});

const nudging = ref(false);
let nudgeTimer = null;
watch(
  () => props.nudge,
  (n) => {
    if (!n) return;
    nudging.value = true;
    if (nudgeTimer) clearTimeout(nudgeTimer);
    nudgeTimer = setTimeout(() => (nudging.value = false), 4000);
  },
);

const { t } = useAppContext();
const { isAdmin } = useAuth();
const { reservationFor, claim, release, defaultMinutes, maxMinutes } =
  useReservations();

const minutes = ref(defaultMinutes.value);
const busy = ref(false);
const error = ref("");
const now = ref(Date.now());
let timer = null;

onMounted(() => {
  timer = setInterval(() => (now.value = Date.now()), 1000);
});
onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
});

const res = computed(() => reservationFor(props.deviceId));

const durationOptions = computed(() => {
  const opts = [15, 30, 60, 120, 240].filter((m) => m <= maxMinutes.value);
  return opts.length ? opts : [maxMinutes.value];
});

// "15 分钟" for sub-hour, "1/2/4 小时" for whole hours — readable + narrow.
function formatDuration(m) {
  return m % 60 === 0
    ? `${m / 60} ${t("reservation.hours")}`
    : `${m} ${t("reservation.minutes")}`;
}

const badgeText = computed(() => {
  if (!res.value) return t("reservation.free");
  return res.value.is_mine
    ? t("reservation.yours")
    : t("reservation.occupied");
});
const badgeClass = computed(() => {
  if (!res.value) return "free";
  return res.value.is_mine ? "mine" : "busy";
});

function parseExpiry(s) {
  if (!s) return 0;
  // Backend stores naive local-time "YYYY-MM-DD HH:MM:SS[.ffffff]".
  const t = Date.parse(s.replace(" ", "T"));
  return Number.isNaN(t) ? 0 : t;
}

const remainingMs = computed(() =>
  res.value ? parseExpiry(res.value.expires_at) - now.value : 0,
);

// Pre-expiry warning: my reservation with ≤ 2 minutes left.
const expiringSoon = computed(
  () => !!res.value && res.value.is_mine && remainingMs.value <= 120000,
);

const remainingText = computed(() => {
  if (!res.value) return "";
  const ms = remainingMs.value;
  if (ms <= 0) return t("reservation.expiring");
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  const body = h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
  return t("reservation.remaining", { time: body });
});

async function run(fn) {
  error.value = "";
  busy.value = true;
  try {
    await fn();
  } catch (err) {
    error.value = err?.message || t("reservation.actionFailed");
  } finally {
    busy.value = false;
  }
}

const onClaim = () => run(() => claim(props.deviceId, minutes.value));
const onExtend = () => run(() => claim(props.deviceId, minutes.value));
const onRelease = () => run(() => release(props.deviceId));
</script>

<style scoped>
.reservation-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
/* Compact: embedded as a thin strip inside each grid DeviceCard. Tighter
   spacing + smaller type so it fits a narrow card without dominating it. */
.reservation-card.compact {
  gap: 6px;
  padding: 8px 10px;
  border-radius: 10px;
}
.reservation-card.compact .reservation-badge {
  font-size: 0.68rem;
  padding: 1px 7px;
}
.reservation-card.compact .reservation-info {
  font-size: 0.74rem;
}
.reservation-card.compact .reservation-warn {
  font-size: 0.7rem;
  padding: 4px 6px;
}
.reservation-card.compact .reservation-btn,
.reservation-card.compact .reservation-select {
  padding: 6px 8px;
  font-size: 0.76rem;
}
.reservation-card.compact .reservation-claim-btn {
  padding-left: 12px;
  padding-right: 12px;
}
/* Pre-expiry highlight (≤ 2 min left on my reservation). */
.reservation-card.is-expiring {
  outline: 1px solid var(--warning-border);
  outline-offset: 2px;
  border-radius: 12px;
}
/* Nudge: user tried to control without reserving — pulse to draw the eye. */
.reservation-card.is-nudge {
  border-radius: 12px;
  animation: reservation-nudge 0.9s ease-in-out 2;
}
@keyframes reservation-nudge {
  0%,
  100% {
    box-shadow: 0 0 0 0 var(--primary-ring);
  }
  50% {
    box-shadow: 0 0 0 4px var(--primary-ring);
  }
}
@media (prefers-reduced-motion: reduce) {
  .reservation-card.is-nudge {
    animation: none;
    outline: 1px solid var(--primary);
    outline-offset: 2px;
  }
}
.reservation-warn {
  margin: 0;
  padding: 6px 8px;
  border-radius: 8px;
  font-size: 0.78rem;
  background: var(--warning-bg);
  color: var(--warning-fg);
  border: 1px solid var(--warning-border);
}
.reservation-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.reservation-title {
  font-weight: 600;
  font-size: 0.85rem;
  color: var(--text-strong);
}
.reservation-badge {
  font-size: 0.72rem;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--border);
}
.reservation-badge.free {
  background: var(--ok-bg);
  color: var(--ok-fg);
  border-color: var(--ok-border);
}
.reservation-badge.mine {
  background: var(--primary);
  color: var(--text-on-primary);
  border-color: var(--primary-border);
}
.reservation-badge.busy {
  background: var(--warning-bg);
  color: var(--warning-fg);
  border-color: var(--warning-border);
}
/* Free state: [ duration ▼ ] [ 占用 ] on one row. */
.reservation-claim-row {
  display: flex;
  align-items: stretch;
  gap: 8px;
}
.reservation-select {
  flex: 1 1 auto; /* fill the rest and shrink instead of overflowing */
  min-width: 0;
  max-width: 100%;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--surface-input);
  color: var(--text);
  font-size: 0.82rem;
}
.reservation-claim-btn {
  flex: 0 0 auto; /* button keeps its intrinsic width, no full-width bar */
  padding-left: 18px;
  padding-right: 18px;
}
.reservation-info {
  margin: 0;
  font-size: 0.82rem;
  color: var(--text);
}
.reservation-actions {
  display: flex;
  gap: 8px;
}
.reservation-btn {
  flex: 1;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--surface-input);
  color: var(--text);
  cursor: pointer;
  font-size: 0.82rem;
  font-weight: 600;
}
.reservation-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.reservation-btn.primary {
  background: var(--primary);
  color: var(--text-on-primary);
  border-color: var(--primary-border);
}
.reservation-btn.danger {
  background: var(--danger-bg);
  color: var(--danger-fg);
  border-color: var(--danger-border);
}
.reservation-error {
  margin: 0;
  font-size: 0.78rem;
  color: var(--danger-fg);
}
</style>
