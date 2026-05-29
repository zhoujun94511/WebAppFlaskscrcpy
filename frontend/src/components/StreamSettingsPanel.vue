<template>
  <section class="section stream-settings">
    <div class="stream-settings-row" v-if="hasPendingChanges || lastMessage">
      <span
        class="stream-settings-msg"
        :class="{ ok: lastMessageOk, danger: lastMessageDanger }"
      >
        {{ statusText }}
      </span>
      <button
        v-if="hasPendingChanges"
        class="ghost small"
        :disabled="busy || !deviceId"
        @click="reset"
      >
        {{ t("settings.reset") }}
      </button>
    </div>

    <details class="stream-settings-hint">
      <summary>{{ t("settings.hintShort") }}</summary>
      <p class="tip subtle">{{ t("settings.hint") }}</p>
    </details>

    <div class="stream-settings-grid">
      <label class="pref-control">
        <span
          >{{ t("settings.bitrate") }} <strong>{{ bitrateLabel }}</strong></span
        >
        <input
          v-model.number="draft.bitrate"
          type="range"
          min="500000"
          max="20000000"
          step="500000"
          :disabled="busy || !deviceId"
        />
      </label>

      <label class="pref-control">
        <span
          >{{ t("settings.maxFps") }} <strong>{{ fpsLabel }}</strong></span
        >
        <input
          v-model.number="draft.max_fps"
          type="range"
          min="0"
          max="60"
          step="5"
          :disabled="busy || !deviceId"
        />
      </label>

      <label class="pref-control">
        <span
          >{{ t("settings.maxWidth") }} <strong>{{ widthLabel }}</strong></span
        >
        <select v-model.number="draft.max_width" :disabled="busy || !deviceId">
          <option :value="0">{{ t("settings.native") }}</option>
          <option :value="2160">2160p</option>
          <option :value="1440">1440p</option>
          <option :value="1080">1080p</option>
          <option :value="720">720p</option>
          <option :value="540">540p</option>
          <option :value="360">360p</option>
        </select>
      </label>

      <label class="toggle stream-settings-adaptive">
        <input
          type="checkbox"
          v-model="adaptive"
          :disabled="busy || !deviceId"
        />
        <span>{{ t("settings.adaptive") }}</span>
      </label>
    </div>

    <div class="stream-settings-actions">
      <button
        class="primary small"
        :disabled="busy || !deviceId || !hasPendingChanges"
        @click="apply"
      >
        {{ busy ? t("settings.applying") : t("settings.apply") }}
      </button>
    </div>
  </section>
</template>

<script setup>
import { computed, reactive, ref, watch } from "vue";
import { useUiI18n } from "../composables/useUiI18n";

const { t } = useUiI18n();

const props = defineProps({
  deviceId: { type: String, default: "" },
  initialConfig: { type: Object, default: () => ({}) },
});

const emit = defineEmits(["applied", "adaptive-change"]);

const baseline = reactive({
  bitrate: 8000000,
  max_fps: 0,
  max_width: 0,
});

const draft = reactive({ ...baseline });
const busy = ref(false);
const lastMessage = ref("");
const lastMessageKind = ref(""); // '' | 'ok' | 'danger'
const adaptive = ref(false);

const hasPendingChanges = computed(
  () =>
    draft.bitrate !== baseline.bitrate ||
    draft.max_fps !== baseline.max_fps ||
    draft.max_width !== baseline.max_width,
);

const bitrateLabel = computed(() => `${Math.round(draft.bitrate / 1000)} kbps`);
const fpsLabel = computed(() =>
  draft.max_fps ? `${draft.max_fps} fps` : t("settings.uncapped"),
);
const widthLabel = computed(() =>
  draft.max_width ? `${draft.max_width}p` : t("settings.native"),
);

// Inline status: pending takes priority over the last applied/failed
// message so the user always sees what's about to happen.
const statusText = computed(() => {
  if (hasPendingChanges.value) return t("settings.pending");
  return lastMessage.value;
});
const lastMessageOk = computed(
  () => !hasPendingChanges.value && lastMessageKind.value === "ok",
);
const lastMessageDanger = computed(
  () => !hasPendingChanges.value && lastMessageKind.value === "danger",
);

watch(
  () => props.initialConfig,
  (cfg) => {
    if (!cfg) return;
    if (Number.isFinite(cfg.bitrate)) {
      baseline.bitrate = cfg.bitrate;
      draft.bitrate = cfg.bitrate;
    }
    if (Number.isFinite(cfg.max_fps)) {
      baseline.max_fps = cfg.max_fps;
      draft.max_fps = cfg.max_fps;
    }
    if (Number.isFinite(cfg.max_width)) {
      baseline.max_width = cfg.max_width;
      draft.max_width = cfg.max_width;
    }
  },
  { immediate: true, deep: true },
);

watch(adaptive, (v) => emit("adaptive-change", v));

function reset() {
  draft.bitrate = baseline.bitrate;
  draft.max_fps = baseline.max_fps;
  draft.max_width = baseline.max_width;
  lastMessage.value = "";
  lastMessageKind.value = "";
}

async function apply() {
  if (!props.deviceId) return;
  busy.value = true;
  lastMessage.value = "";
  lastMessageKind.value = "";
  try {
    const response = await fetch("/api/reconfigure", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        device_id: props.deviceId,
        bitrate: draft.bitrate,
        max_fps: draft.max_fps,
        max_width: draft.max_width,
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.status !== "ok") {
      throw new Error(
        payload.message || `reconfigure failed: ${response.status}`,
      );
    }
    const cfg = payload.config || {};
    baseline.bitrate = cfg.bitrate ?? draft.bitrate;
    baseline.max_fps = cfg.max_fps ?? draft.max_fps;
    baseline.max_width = cfg.max_width ?? draft.max_width;
    lastMessage.value = t("settings.applied");
    lastMessageKind.value = "ok";
    emit("applied", {
      config: payload.config,
      reattached: payload.reattached_peers ?? 0,
    });
  } catch (err) {
    lastMessage.value = err?.message || t("settings.failed");
    lastMessageKind.value = "danger";
  } finally {
    busy.value = false;
  }
}
</script>
