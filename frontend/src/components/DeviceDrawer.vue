<template>
  <aside class="device-drawer" :class="{ open }" :inert="!open">
    <header class="device-drawer-head">
      <div class="device-drawer-title">
        <strong>{{ title || t("device.title") }}</strong>
        <small v-if="deviceId && deviceId !== title">{{ deviceId }}</small>
      </div>
      <button
        class="icon-btn"
        :title="t('actions.close')"
        @click="$emit('close')"
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
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </header>

    <nav class="device-drawer-tabs" role="tablist">
      <button
        v-for="tab in tabs"
        :key="tab.id"
        class="drawer-tab"
        :class="{ active: activeTab === tab.id }"
        role="tab"
        :aria-selected="activeTab === tab.id"
        @click="activeTab = tab.id"
      >
        {{ tab.label }}
      </button>
    </nav>

    <section class="device-drawer-body" role="tabpanel">
      <slot :name="activeTab" />
    </section>
  </aside>
</template>

<script setup>
import { ref, computed, watch } from "vue";
import { useUiI18n } from "../composables/useUiI18n";

const { t } = useUiI18n();

const props = defineProps({
  open: { type: Boolean, default: false },
  deviceId: { type: String, default: "" },
  title: { type: String, default: "" },
});

defineEmits(["close"]);

const activeTab = ref("keypad");

// Trimmed to the 5 panels that actually need a full-width takeover.
// Info / quick actions / clipboard / upload-drop / stream settings /
// connection chip now live in the always-visible side panels of the
// main area; opening the drawer is reserved for high-density tools.
const tabs = computed(() => [
  { id: "keypad", label: t("panels.keypad") },
  { id: "logcat", label: t("logcat.title") },
  { id: "terminal", label: t("panels.terminal") },
  { id: "files", label: t("files.title") },
  { id: "apps", label: t("apps.title") },
]);

// When the drawer opens for a different device, reset to the most useful tab.
watch(
  () => props.deviceId,
  () => {
    activeTab.value = "keypad";
  },
);
</script>
