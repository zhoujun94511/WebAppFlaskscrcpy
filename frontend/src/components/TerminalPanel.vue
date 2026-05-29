<template>
  <section class="section terminal-panel">
    <div class="section-head">
      <h3>{{ t("terminal.title") }}</h3>
      <div class="terminal-controls">
        <button
          v-if="!sessionOpen"
          class="ghost"
          :disabled="!deviceId || connecting"
          @click="open"
        >
          {{ connecting ? t("terminal.opening") : t("terminal.open") }}
        </button>
        <button v-else class="ghost" @click="close">
          {{ t("terminal.close") }}
        </button>
      </div>
    </div>

    <p v-if="statusMessage" class="tip subtle">{{ statusMessage }}</p>

    <div
      ref="hostRef"
      class="terminal-host"
      :class="{ idle: !sessionOpen }"
    ></div>
  </section>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { useUiI18n } from "../composables/useUiI18n";

const { t } = useUiI18n();

const props = defineProps({
  deviceId: { type: String, default: "" },
  socket: { type: Object, default: null },
});

const hostRef = ref(null);
const statusMessage = ref("");
const connecting = ref(false);
const sessionOpen = ref(false);

let term = null;
let fitAddon = null;
let resizeObserver = null;

function bindSocketEvents() {
  const s = props.socket?.value || props.socket;
  if (!s) return;

  s.off("terminal:opened");
  s.off("terminal:output");
  s.off("terminal:closed");
  s.off("terminal:error");

  s.on("terminal:opened", () => {
    connecting.value = false;
    sessionOpen.value = true;
    statusMessage.value = "";
    term?.focus();
  });
  s.on("terminal:output", (data) => {
    if (data?.data && term) term.write(data.data);
  });
  s.on("terminal:closed", (data) => {
    sessionOpen.value = false;
    connecting.value = false;
    statusMessage.value = data?.reason
      ? `${t("terminal.closedWithReason")} ${data.reason}`
      : t("terminal.closed");
  });
  s.on("terminal:error", (data) => {
    sessionOpen.value = false;
    connecting.value = false;
    statusMessage.value = data?.error || t("terminal.error");
  });
}

function ensureTerminal() {
  if (term) return;
  term = new Terminal({
    cursorBlink: true,
    fontFamily: 'Menlo, Consolas, "Liberation Mono", monospace',
    fontSize: 13,
    scrollback: 5000,
    theme: { background: "#0b1220", foreground: "#e2e8f0" },
  });
  fitAddon = new FitAddon();
  term.loadAddon(fitAddon);
  if (hostRef.value) {
    term.open(hostRef.value);
    fitAddon.fit();
  }
  term.onData((chunk) => {
    const s = props.socket?.value || props.socket;
    if (sessionOpen.value && s) s.emit("terminal:input", { data: chunk });
  });

  resizeObserver = new ResizeObserver(() => {
    try {
      fitAddon?.fit();
      const s = props.socket?.value || props.socket;
      if (sessionOpen.value && s && term) {
        s.emit("terminal:resize", { cols: term.cols, rows: term.rows });
      }
    } catch {
      /* layout not ready yet */
    }
  });
  if (hostRef.value) resizeObserver.observe(hostRef.value);
}

function open() {
  if (!props.deviceId) return;
  const s = props.socket?.value || props.socket;
  if (!s) {
    statusMessage.value = t("terminal.noSocket");
    return;
  }
  ensureTerminal();
  connecting.value = true;
  statusMessage.value = "";
  term?.clear();
  s.emit("terminal:open", { device_id: props.deviceId });
}

function close() {
  const s = props.socket?.value || props.socket;
  if (s) s.emit("terminal:close", {});
  sessionOpen.value = false;
}

watch(
  () => props.socket?.value ?? props.socket,
  (s) => {
    if (s) {
      bindSocketEvents();
      maybeAutoOpen();
    }
  },
  { immediate: true },
);

watch(
  () => props.deviceId,
  (id, prev) => {
    // Switching devices: just re-open. The backend's SessionRegistry.open
    // sees the device_id changed and atomically closes the old session
    // before starting the new one — no client-side close+open race.
    if (id && id !== prev) {
      sessionOpen.value = false;
      term?.clear();
      maybeAutoOpen();
    }
  },
);

function maybeAutoOpen() {
  if (!props.deviceId || connecting.value) return;
  const s = props.socket?.value || props.socket;
  if (!s) return;
  // Defer to next tick so xterm host has dimensions when fit() runs.
  setTimeout(open, 0);
}

onMounted(() => {
  ensureTerminal();
  bindSocketEvents();
  maybeAutoOpen();
});

onBeforeUnmount(() => {
  close();
  resizeObserver?.disconnect();
  resizeObserver = null;
  term?.dispose();
  term = null;
});
</script>
