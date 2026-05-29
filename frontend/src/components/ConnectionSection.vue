<template>
  <section class="section">
    <div class="section-head">
      <h2>{{ t("connection.title") }}</h2>
      <span class="badge" :class="statusClass">{{ statusText }}</span>
    </div>
    <div class="section-head compact">
      <span class="meta-label">{{ t("connection.audio") }}</span>
      <button
        class="ghost"
        @click="$emit('toggle-audio')"
        :disabled="!subscribedDeviceId"
      >
        {{ audioMuted ? t("connection.audioMuted") : t("connection.audioOn") }}
      </button>
    </div>
    <p class="tip subtle">{{ t("connection.audioDisabledTip") }}</p>
    <p class="tip subtle">{{ controlText }}</p>
    <div class="section-head compact">
      <span class="meta-label">{{ t("connection.serverJar") }}</span>
      <button
        class="ghost"
        @click="$emit('push-server-jar')"
        :disabled="!selectedDeviceId"
      >
        {{ t("connection.pushJar") }}
      </button>
    </div>
    <div class="meta-grid">
      <div>
        <span class="meta-label">{{ t("connection.room") }}</span>
        <strong>{{ subscribedDeviceId || "-" }}</strong>
      </div>
      <div>
        <span class="meta-label">{{ t("connection.resolution") }}</span>
        <strong>{{ resolutionText }}</strong>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed } from "vue";
import { useUiI18n } from "../composables/useUiI18n";

const { t } = useUiI18n();

const props = defineProps({
  // Semantic enum from useScrcpySession: 'disconnected' | 'subscribed' | 'connected'
  connectionStatusKind: { type: String, default: "disconnected" },
  // Number of active streams — used by the "subscribed N" label
  activeStreamCount: { type: Number, default: 0 },
  audioMuted: { type: Boolean, default: false },
  // Semantic enum from useScrcpySession: 'idle' | 'selected' | 'streaming'
  controlStatusKind: { type: String, default: "idle" },
  selectedDeviceId: { type: String, default: "" },
  subscribedDeviceId: { type: String, default: "" },
  resolutionText: { type: String, default: "" },
});

defineEmits(["toggle-audio", "push-server-jar"]);

const statusText = computed(() => {
  if (props.connectionStatusKind === "disconnected")
    return t("connection.disconnected");
  if (props.connectionStatusKind === "subscribed") {
    return t("connection.subscribedCount", { count: props.activeStreamCount });
  }
  return t("connection.connected");
});

const statusClass = computed(() => {
  if (props.connectionStatusKind === "disconnected") return "danger";
  if (
    props.connectionStatusKind === "subscribed" ||
    props.connectionStatusKind === "connected"
  )
    return "ok";
  return "idle";
});

const controlText = computed(() => {
  if (props.controlStatusKind === "streaming") {
    return t("control.streamingDevice", { id: props.subscribedDeviceId });
  }
  if (props.controlStatusKind === "selected") {
    return t("control.selectedDevice", { id: props.selectedDeviceId });
  }
  return t("control.selectFirst");
});
</script>
