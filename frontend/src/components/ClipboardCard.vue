<template>
  <section class="quick-card">
    <header class="quick-card-head">
      <strong>{{ t("clipboard.title") }}</strong>
    </header>
    <textarea
      v-model="text"
      class="clipboard-text"
      :placeholder="t('quick.clipboardPlaceholder')"
      rows="3"
      :disabled="!enabled"
    />
    <div class="clipboard-actions">
      <button
        class="ghost small"
        :disabled="!enabled"
        @click="$emit('refresh')"
        :title="t('quick.clipboardRefresh')"
      >
        {{ t("quick.clipboardRefresh") }}
      </button>
      <button
        class="ghost small"
        :disabled="!enabled || !text"
        @click="$emit('paste', text)"
        :title="t('quick.clipboardPaste')"
      >
        {{ t("quick.clipboardPaste") }}
      </button>
      <button
        class="ghost small"
        :disabled="!enabled"
        @click="$emit('paste-browser')"
        :title="t('quick.pasteFromBrowser')"
      >
        {{ t("quick.pasteFromBrowser") }}
      </button>
    </div>
  </section>
</template>

<script setup>
import { ref, watch } from "vue";
import { useUiI18n } from "../composables/useUiI18n";

const { t } = useUiI18n();
const props = defineProps({
  enabled: { type: Boolean, default: false },
  remoteContent: { type: String, default: "" },
});
defineEmits(["refresh", "paste", "paste-browser"]);

const text = ref("");
// Sync local edit area with the most recently fetched clipboard value.
// We propagate even empty strings — the user just hit "read device" and an
// empty result IS the answer; suppressing it would look like the click did
// nothing. ``flush: 'sync'`` so the textarea reflects the new value before
// the next paint, no flicker.
watch(
  () => props.remoteContent,
  (v) => {
    text.value = v || "";
  },
  { flush: "sync" },
);
</script>
