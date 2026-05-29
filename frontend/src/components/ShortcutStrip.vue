<template>
  <div class="shortcut-grid">
    <button class="shortcut-btn" :disabled="!deviceId" @click="emitKey(24)">
      {{ t("device.volUp") }}
    </button>
    <button class="shortcut-btn" :disabled="!deviceId" @click="emitKey(25)">
      {{ t("device.volDown") }}
    </button>
    <button class="shortcut-btn" :disabled="!deviceId" @click="emitKey(26)">
      {{ t("device.power") }}
    </button>
    <button
      class="shortcut-btn"
      :disabled="!deviceId"
      @click="$emit('screen-on')"
    >
      {{ t("device.screenOn") }}
    </button>
    <button
      class="shortcut-btn"
      :disabled="!deviceId"
      @click="$emit('screen-off')"
    >
      {{ t("device.screenOff") }}
    </button>
    <button class="shortcut-btn" :disabled="!deviceId" @click="emitKey(187)">
      {{ t("device.appSwitch") }}
    </button>
    <button class="shortcut-btn" :disabled="!deviceId" @click="emitKey(82)">
      {{ t("device.menu") }}
    </button>
    <button class="shortcut-btn" :disabled="!deviceId" @click="emitKey(278)">
      {{ t("device.copy") }}
    </button>
    <button class="shortcut-btn" :disabled="!deviceId" @click="emitKey(277)">
      {{ t("device.cut") }}
    </button>
    <button class="shortcut-btn" :disabled="!deviceId" @click="emitKey(279)">
      {{ t("device.paste") }}
    </button>
    <button
      class="shortcut-btn"
      :disabled="!deviceId"
      @click="$emit('paste-text')"
    >
      {{ t("device.pasteText") }}
    </button>
  </div>
</template>

<script setup>
import { useUiI18n } from "../composables/useUiI18n";

const { t } = useUiI18n();

const props = defineProps({
  deviceId: {
    type: String,
    default: "",
  },
});

const emit = defineEmits([
  "set-display-mode",
  "toggle-fullscreen",
  "toggle-pause",
  "resume-pause",
  "reset-display-view",
  "restart-stream",
  "screen-off",
  "screen-on",
  "notification",
  "settings",
  "collapse-panels",
  "paste-text",
  "send-key",
]);

function emitKey(keycode) {
  emit("send-key", { keycode });
}
</script>
