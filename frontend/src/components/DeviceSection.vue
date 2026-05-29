<template>
  <section class="section">
    <div class="section-head">
      <h2>{{ t("device.title") }}</h2>
      <button class="ghost" @click="$emit('refresh')">
        {{ t("device.refresh") }}
      </button>
    </div>

    <label class="field-label" for="device-select">{{
      t("device.current")
    }}</label>
    <select id="device-select" :value="selectedDeviceId" @change="onChange">
      <option v-for="device in devices" :key="device" :value="device">
        {{ device }}
      </option>
    </select>

    <div class="cta-row">
      <button
        class="cta-primary"
        @click="$emit('start')"
        :disabled="!selectedDeviceId"
      >
        <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <path d="M8 5v14l11-7z" />
        </svg>
        {{ t("device.start") }}
      </button>
      <button
        class="stop-btn"
        :title="t('device.stop')"
        @click="$emit('stop')"
        :disabled="!subscribedDeviceId"
      >
        <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <rect x="6" y="6" width="12" height="12" rx="1.5" />
        </svg>
      </button>
    </div>

    <p v-if="!devices.length" class="tip">{{ t("device.noneDetected") }}</p>
  </section>
</template>

<script setup>
import { useUiI18n } from "../composables/useUiI18n";

const { t } = useUiI18n();

const props = defineProps({
  devices: {
    type: Array,
    default: () => [],
  },
  selectedDeviceId: {
    type: String,
    default: "",
  },
  subscribedDeviceId: {
    type: String,
    default: "",
  },
});

const emit = defineEmits([
  "refresh",
  "update:selectedDeviceId",
  "start",
  "stop",
]);

function onChange(event) {
  emit("update:selectedDeviceId", event.target.value);
}
</script>
