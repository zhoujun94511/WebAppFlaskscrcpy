<template>
  <button
    class="theme-toggle"
    type="button"
    :title="label"
    :aria-label="label"
    @click="toggleTheme"
  >
    <!-- Official Lucide icons (sun / moon). Show the mode you'll switch TO. -->
    <svg
      v-if="theme === 'dark'"
      class="theme-toggle-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2" />
      <path d="M12 20v2" />
      <path d="m4.93 4.93 1.41 1.41" />
      <path d="m17.66 17.66 1.41 1.41" />
      <path d="M2 12h2" />
      <path d="M20 12h2" />
      <path d="m6.34 17.66-1.41 1.41" />
      <path d="m19.07 4.93-1.41 1.41" />
    </svg>
    <svg
      v-else
      class="theme-toggle-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" />
    </svg>
    <span class="theme-toggle-label">{{ label }}</span>
  </button>
</template>

<script setup>
import { computed } from "vue";
import { useAppContext } from "../composables/useAppContext";

const { t, theme, toggleTheme } = useAppContext();

// Label = the mode a click switches TO (matches the icon shown).
const label = computed(() =>
  theme.value === "dark" ? t("preferences.light") : t("preferences.dark"),
);
</script>

<style scoped>
.theme-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 12px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--panel-bg);
  color: var(--text);
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
  box-shadow: var(--shadow-soft);
  transition: color 0.15s ease, border-color 0.15s ease;
}
.theme-toggle:hover {
  border-color: var(--primary);
}
.theme-toggle-icon {
  width: 16px;
  height: 16px;
  flex: 0 0 auto;
}
.theme-toggle-label {
  line-height: 1;
  white-space: nowrap;
}
</style>
