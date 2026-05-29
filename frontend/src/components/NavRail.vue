<template>
  <nav class="nav-rail" :aria-label="t('app.title')">
    <div class="nav-rail-brand" :title="t('app.title')">
      <img class="nav-rail-logo" src="/logo.svg" alt="WebAppFlaskScrcpy" />
    </div>
    <button
      v-for="item in items"
      :key="item.id"
      class="nav-rail-item"
      :class="{ active: modelValue === item.id }"
      :title="item.label"
      @click="$emit('update:modelValue', item.id)"
    >
      <component :is="item.icon" />
      <span>{{ item.label }}</span>
    </button>

    <!-- Admin entry: pinned to the bottom and only rendered for admins, so
         non-privileged users never even see it (cleaner than hiding a button
         in the crowded top header). Opens the admin modal. -->
    <button
      v-if="isAdmin"
      class="nav-rail-item nav-rail-admin"
      :title="t('admin.title')"
      @click="$emit('open-admin')"
    >
      <component :is="AdminIcon" />
      <span>{{ t("admin.title") }}</span>
    </button>
  </nav>
</template>

<script setup>
import { computed, h } from "vue";
import { useUiI18n } from "../composables/useUiI18n";
import { useAuth } from "../composables/useAuth";

const { t } = useUiI18n();
const { isAdmin } = useAuth();

defineProps({
  modelValue: { type: String, default: "device" },
});
defineEmits(["update:modelValue", "open-admin"]);

const Svg = (paths) => () =>
  h(
    "svg",
    {
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      "stroke-width": 2,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
      "aria-hidden": "true",
    },
    paths.map((d) => h("path", { d })),
  );

const DeviceIcon = Svg(["M5 4h14v16H5z", "M9 20h6"]);
// Shield with a check — "admin / management".
const AdminIcon = Svg([
  "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
  "M9 12l2 2 4-4",
]);
const SettingsIcon = Svg([
  "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  "M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z",
]);

// MUST be a computed so labels re-translate when the locale changes.
// The earlier plain-array form captured t('navRail.device') ONCE at
// setup time and never updated — symptom: nav rail kept showing the
// initial locale's "Devices / Settings" even after switching to 中文.
const items = computed(() => [
  { id: "device", label: t("navRail.device"), icon: DeviceIcon },
  { id: "settings", label: t("navRail.settings"), icon: SettingsIcon },
]);
</script>
