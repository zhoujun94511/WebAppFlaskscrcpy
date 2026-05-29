<template>
  <section
    class="quick-card drop-zone"
    :class="{ hover: hover, busy: !!busy, disabled: !enabled }"
    @dragover.prevent="hover = enabled"
    @dragleave="hover = false"
    @drop.prevent="onDrop"
    @click="enabled && fileInput?.click()"
  >
    <input
      ref="fileInput"
      type="file"
      class="hidden-file-input"
      @change="onPicked"
    />
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.6"
      width="26"
      height="26"
    >
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="17 8 12 3 7 8" />
      <line x1="12" y1="3" x2="12" y2="15" />
    </svg>
    <p v-if="busy" class="drop-zone-msg">{{ busy }}</p>
    <p v-else-if="!enabled" class="drop-zone-msg subtle">
      {{ t("quick.dropDisabled") }}
    </p>
    <p v-else class="drop-zone-msg">{{ t("quick.dropHint") }}</p>
    <p v-if="lastMessage" class="drop-zone-msg subtle">{{ lastMessage }}</p>
  </section>
</template>

<script setup>
import { ref } from "vue";
import { useUiI18n } from "../composables/useUiI18n";

const { t } = useUiI18n();
const props = defineProps({
  deviceId: { type: String, default: "" },
  enabled: { type: Boolean, default: false },
});

// Emit each dropped/picked file to the parent. App.vue routes through
// ``useScrcpySession.handleUploadFile`` so APK install, signature-
// mismatch confirmation, and ``/sdcard/Download/`` push share one
// centralised path with the device-card drop and the apps panel —
// otherwise this card's local fetch() would bypass the confirmation
// dialog and surface the raw ``INSTALL_FAILED_UPDATE_INCOMPATIBLE``
// error instead.
const emit = defineEmits(["upload-file"]);

const fileInput = ref(null);
const hover = ref(false);
const busy = ref("");
const lastMessage = ref("");

async function onDrop(ev) {
  hover.value = false;
  if (!props.enabled) return;
  const files = [...(ev.dataTransfer?.files || [])];
  for (const f of files) emit("upload-file", f);
}

async function onPicked(ev) {
  const f = ev.target?.files?.[0];
  ev.target.value = "";
  if (f) emit("upload-file", f);
}
</script>
