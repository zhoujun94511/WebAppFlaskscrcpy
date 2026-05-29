<template>
  <section class="status-strip">
    <div class="status-card" :class="serverHealthClass">
      <div class="status-card-head">
        <span class="status-card-label">{{ t("status.serverJar") }}</span>
        <span class="status-card-badge">{{ serverHealthBadge }}</span>
      </div>
      <strong>{{ resolve(serverHealthText) }}</strong>
    </div>
    <div class="status-card" :class="clipboardAckClass">
      <div class="status-card-head">
        <span class="status-card-label">{{ t("status.clipboard") }}</span>
        <span class="status-card-badge">{{ clipboardAckBadge }}</span>
      </div>
      <strong>{{ resolve(clipboardAckStatus) }}</strong>
    </div>
    <div class="status-card" :class="uploadMessageClass">
      <div class="status-card-head">
        <span class="status-card-label">{{ t("status.upload") }}</span>
        <span class="status-card-badge">{{ uploadBadgeLabel }}</span>
      </div>
      <strong>{{ resolve(uploadMessage) || fallbackUploadText }}</strong>
    </div>
  </section>
</template>

<script setup>
import { computed } from "vue";
import { useUiI18n } from "../composables/useUiI18n";

const { t } = useUiI18n();

const props = defineProps({
  serverHealthClass: { type: String, default: "subtle" },
  // Each *Text/*Status/*Message prop is an object `{ key, params }` or
  // `{ literal }` produced by useScrcpySession's setters. Plain strings
  // are still accepted for backward compatibility — they pass through
  // as-is. ``resolve()`` below picks the right code path.
  serverHealthText: {
    type: [Object, String],
    default: () => ({ key: "server.noDeviceSelected", params: {} }),
  },
  clipboardAckClass: { type: String, default: "subtle" },
  clipboardAckStatus: {
    type: [Object, String],
    default: () => ({ key: "clip.idle", params: {} }),
  },
  uploadMessageClass: { type: String, default: "subtle" },
  uploadMessage: {
    type: [Object, String],
    default: () => ({ key: "upload.ready", params: {} }),
  },
  uploadBusy: { type: Boolean, default: false },
});

function resolve(msg) {
  if (msg == null) return "";
  if (typeof msg === "string") return msg;
  if (typeof msg !== "object") return String(msg);
  if ("literal" in msg && msg.literal) return String(msg.literal);
  if ("key" in msg) return t(msg.key, msg.params || {});
  return "";
}

const serverHealthBadge = computed(() => {
  if (props.serverHealthClass === "ok") return t("status.ok");
  if (props.serverHealthClass === "danger") return t("status.fail");
  return t("status.check");
});

const clipboardAckBadge = computed(() => {
  if (props.clipboardAckClass === "ok") return t("status.ok");
  if (props.clipboardAckClass === "danger") return t("status.fail");
  return t("status.idle");
});

const uploadBadgeLabel = computed(() => {
  if (props.uploadBusy) return t("status.busy");
  if (props.uploadMessageClass === "ok") return t("status.done");
  if (props.uploadMessageClass === "danger") return t("status.fail");
  return t("status.ready");
});

const fallbackUploadText = computed(() => t("upload.install"));
</script>
