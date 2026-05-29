<template>
  <label class="lang-switcher" :title="t('preferences.language')">
    <!-- Globe icon for visual parity with the theme toggle pill. -->
    <svg
      class="lang-switcher-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="10" />
      <path d="M2 12h20" />
      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
    </svg>
    <select
      :value="locale"
      :aria-label="t('preferences.language')"
      @change="setLocale($event.target.value)"
    >
      <option v-for="option in availableLocales" :key="option" :value="option">
        {{ labels[option] || option }}
      </option>
    </select>
  </label>
</template>

<script setup>
import { computed } from "vue";
import { useUiI18n } from "../composables/useUiI18n";

const { locale, availableLocales, setLocale, t } = useUiI18n();

const labels = computed(() => ({
  en: t("preferences.english"),
  "zh-CN": t("preferences.simplifiedChinese"),
  "zh-TW": t("preferences.traditionalChinese"),
}));
</script>

<style scoped>
.lang-switcher {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 8px 0 12px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--panel-bg);
  color: var(--text);
  box-shadow: var(--shadow-soft);
  cursor: pointer;
  transition: border-color 0.15s ease;
}
.lang-switcher:hover {
  border-color: var(--primary);
}
/* Keyboard-focus indicator lives on the PILL, not the inner <select>. */
.lang-switcher:focus-within {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px var(--primary-ring);
}
.lang-switcher-icon {
  width: 16px;
  height: 16px;
  flex: 0 0 auto;
  color: var(--muted);
}
.lang-switcher select {
  border: none;
  background: transparent;
  color: var(--text);
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
  outline: none;
}
/* Suppress the global ``select:focus`` ring / ``select:focus-visible``
   outline on the inner control — it's a borderless transparent pill, so
   that 4px box-shadow rendered as a stray "bubble" behind the dropdown.
   Focus is shown on the pill via :focus-within above instead. */
.lang-switcher select:focus,
.lang-switcher select:focus-visible {
  border-color: transparent;
  box-shadow: none;
  outline: none;
}
/* Option-list colours are handled globally in style.css
   (``select option { background/color }``) so every native dropdown
   follows dark mode — no per-component rule needed here. */
</style>
