<template>
  <section class="panel device-matrix">
    <header class="section-head">
      <div>
        <h2>{{ t("matrix.title") }}</h2>
        <p class="meta-label">{{ t("matrix.subtitle") }}</p>
      </div>
      <div class="terminal-controls">
        <button class="ghost" :disabled="refreshing" @click="refresh">
          {{ refreshing ? t("matrix.refreshing") : t("matrix.refresh") }}
        </button>
      </div>
    </header>

    <p v-if="!streams.length" class="tip subtle">{{ t("matrix.empty") }}</p>

    <div v-else class="matrix-grid">
      <article
        v-for="stream in streams"
        :key="stream.device_id"
        class="matrix-card"
        :class="{ active: stream.device_id === activeDeviceId }"
        @click="$emit('select', stream.device_id)"
      >
        <div class="matrix-thumb">
          <img
            v-if="thumbs[stream.device_id]"
            :src="thumbs[stream.device_id]"
            :alt="stream.device_id"
            loading="lazy"
          />
          <div v-else class="matrix-thumb-empty">
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
          <span
            v-if="stream.device_id === activeDeviceId"
            class="matrix-badge-active"
            >●</span
          >
        </div>
        <div class="matrix-meta">
          <strong class="matrix-name">{{
            stream.device_name || stream.device_id
          }}</strong>
          <span class="matrix-detail">
            <span v-if="stream.resolution">
              {{ stream.resolution[0] }}×{{ stream.resolution[1] }}
            </span>
            <span v-if="stream.config?.bitrate" class="matrix-detail-sep"
              >·</span
            >
            <span v-if="stream.config?.bitrate">
              {{ Math.round((stream.config.bitrate || 0) / 1000) }}k
            </span>
          </span>
        </div>
      </article>
    </div>
  </section>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useUiI18n } from "../composables/useUiI18n";

const { t } = useUiI18n();

const props = defineProps({
  activeDeviceId: { type: String, default: "" },
  intervalMs: { type: Number, default: 8000 },
});

defineEmits(["select"]);

const streams = ref([]);
const thumbs = ref({});
const refreshing = ref(false);
let timer = null;

async function refresh() {
  refreshing.value = true;
  try {
    const r = await fetch("/api/active-streams");
    const payload = await r.json().catch(() => ({}));
    streams.value = Array.isArray(payload.streams) ? payload.streams : [];
    await refreshThumbnails();
  } catch (err) {
    console.warn("[matrix] refresh failed", err);
  } finally {
    refreshing.value = false;
  }
}

async function refreshThumbnails() {
  // Concurrent fetches per device; ignore failures so one offline device
  // doesn't block the rest.
  await Promise.all(
    streams.value.map(async (s) => {
      try {
        const r = await fetch(
          `/api/snapshot?device_id=${encodeURIComponent(s.device_id)}&quality=55`,
          { cache: "no-store" },
        );
        if (!r.ok) return;
        const blob = await r.blob();
        const prev = thumbs.value[s.device_id];
        thumbs.value = {
          ...thumbs.value,
          [s.device_id]: URL.createObjectURL(blob),
        };
        if (prev) URL.revokeObjectURL(prev);
      } catch {
        /* device offline */
      }
    }),
  );
}

onMounted(() => {
  refresh();
  timer = setInterval(refresh, props.intervalMs);
});

onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
  for (const url of Object.values(thumbs.value)) URL.revokeObjectURL(url);
});
</script>
