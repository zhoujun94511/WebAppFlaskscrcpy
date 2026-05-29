<template>
  <section class="section keypad">
    <div class="section-head compact">
      <label class="toggle">
        <input v-model="captureKeyboard" type="checkbox" />
        <span>{{ t("keypad.captureKeyboard") }}</span>
      </label>
      <details class="keypad-tip">
        <summary aria-label="Keyboard shortcuts help">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="16" x2="12" y2="12" />
            <line x1="12" y1="8" x2="12.01" y2="8" />
          </svg>
        </summary>
        <p class="tip subtle">{{ t("keypad.tip") }}</p>
      </details>
    </div>

    <ShortcutStrip
      :device-id="deviceId"
      @send-key="$emit('send-key', $event)"
      @set-display-mode="$emit('set-display-mode', $event)"
      @toggle-fullscreen="$emit('toggle-fullscreen')"
      @toggle-pause="$emit('toggle-pause')"
      @resume-pause="$emit('resume-pause')"
      @reset-display-view="$emit('reset-display-view')"
      @restart-stream="$emit('restart-stream')"
      @screen-off="$emit('screen-off')"
      @screen-on="$emit('screen-on')"
      @notification="$emit('notification')"
      @settings="$emit('settings')"
      @collapse-panels="$emit('collapse-panels')"
      @paste-text="pasteBrowserClipboardAsText"
    />

    <BindingWorkbench
      :device-id="deviceId"
      :bindings="bindings"
      :groups="groups"
      :editable-groups="editableGroups"
      :common-keycodes="commonKeycodes"
      :search-text="searchText"
      :active-group="activeGroup"
      :show-editor="showEditor"
      :import-message="importMessage"
      :draft="draft"
      @update:search-text="searchText = $event"
      @update:active-group="activeGroup = $event"
      @update:show-editor="showEditor = $event"
      @restore-defaults="restoreDefaults"
      @export-json="exportCurrentBindings"
      @import-json="triggerImport"
      @add-binding="addBinding"
      @send-binding="emitBinding"
    />

    <input
      ref="importInput"
      class="file-input"
      type="file"
      accept="application/json"
      @change="handleImportFile"
    />
  </section>
</template>

<script setup>
import { onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { COMMON_KEYCODES } from "../keycodes";
import {
  cloneDefaultBindings,
  exportBindings,
  importBindingsFromText,
  loadBindings,
  normalizeBinding,
  persistBindings,
} from "../bindingStore";
import ShortcutStrip from "./ShortcutStrip.vue";
import BindingWorkbench from "./BindingWorkbench.vue";
import { useUiI18n } from "../composables/useUiI18n";

const { t } = useUiI18n();

const props = defineProps({
  deviceId: {
    type: String,
    default: "",
  },
});

const emit = defineEmits([
  "send-key",
  "send-text",
  "clipboard-paste",
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
]);

const captureKeyboard = ref(true);
const showEditor = ref(false);
const searchText = ref("");
const activeGroup = ref("all");
const bindings = ref(loadBindings());
const groups = [
  "all",
  "navigation",
  "system",
  "dpad",
  "typing",
  "clipboard",
  "custom",
];
const editableGroups = [
  "navigation",
  "system",
  "dpad",
  "typing",
  "clipboard",
  "custom",
];
const commonKeycodes = COMMON_KEYCODES;
const importInput = ref(null);
const importMessage = ref("");

const draft = reactive({
  label: "",
  group: "custom",
  keycode: 3,
});

function restoreDefaults() {
  bindings.value = cloneDefaultBindings();
  importMessage.value = t("keypad.restoredDefaultBindings");
}

function emitKey(keycode, extra = {}) {
  emit("send-key", { keycode, ...extra });
}

function emitBinding(binding) {
  emitKey(binding.keycode);
}

function addBinding() {
  const next = normalizeBinding({
    ...draft,
  });
  bindings.value = [...bindings.value, next];
  draft.label = "";
  draft.group = "custom";
  draft.keycode = 3;
  importMessage.value = t("keypad.customBindingAdded");
}

function exportCurrentBindings() {
  const blob = new Blob([exportBindings(bindings.value)], {
    type: "application/json;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "scrcpy-keybindings.json";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
  importMessage.value = t("keypad.exportedBindings");
}

function triggerImport() {
  importMessage.value = "";
  importInput.value?.click();
}

async function handleImportFile(event) {
  const file = event.target?.files?.[0];
  if (!file) return;

  try {
    const text = await file.text();
    const imported = importBindingsFromText(text);
    bindings.value = imported;
    importMessage.value = t("keypad.importedBindings", {
      count: imported.length,
    });
  } catch (error) {
    importMessage.value =
      error instanceof Error ? error.message : t("keypad.failedImportBindings");
  } finally {
    if (event.target) {
      event.target.value = "";
    }
  }
}

async function pasteBrowserClipboardAsText() {
  if (!props.deviceId) return;
  try {
    const text = await navigator.clipboard.readText();
    if (text) {
      emit("clipboard-paste", text);
    }
  } catch {
    // Clipboard permission may be denied in the browser.
  }
}

function sendCtrlShortcut(key) {
  if (!props.deviceId) return;
  const map = {
    c: 278,
    x: 277,
    v: 279,
  };
  const keycode = map[key];
  if (!keycode) return;
  emitKey(keycode);
}

function sendHostKey(event) {
  if (!captureKeyboard.value || !props.deviceId) return;

  const target = event.target;
  const tagName = target?.tagName?.toLowerCase();
  if (
    tagName === "input" ||
    tagName === "textarea" ||
    tagName === "select" ||
    target?.isContentEditable
  ) {
    return;
  }

  if (event.ctrlKey && !event.altKey && !event.metaKey) {
    const key = event.key.toLowerCase();
    if (key === "c" || key === "x" || key === "v") {
      event.preventDefault();
      if (key === "v" && event.shiftKey) {
        void pasteBrowserClipboardAsText();
      } else {
        sendCtrlShortcut(key);
      }
      return;
    }
  }

  if (event.altKey && !event.ctrlKey && !event.metaKey) {
    const key = event.key.toLowerCase();
    if (key === "f") {
      event.preventDefault();
      emit("toggle-fullscreen");
      return;
    }
    if (key === "g") {
      event.preventDefault();
      emit("set-display-mode", "pixel");
      return;
    }
    if (key === "w") {
      event.preventDefault();
      emit("set-display-mode", "fit");
      return;
    }
    if (key === "z" && event.shiftKey) {
      event.preventDefault();
      emit("resume-pause");
      return;
    }
    if (key === "z") {
      event.preventDefault();
      emit("toggle-pause");
      return;
    }
    if (key === "r" && event.shiftKey) {
      event.preventDefault();
      emit("restart-stream");
      return;
    }
    // ``r`` (rotate-display-right) and ``i`` (toggle-fps-counter) were
    // removed when the client-side fake-rotation / FPS overlay code got
    // dropped. Rotation is now triggered via the device-card footer's
    // rotate button (POSTs /api/device/<id>/rotate).
    if (key === "o" && event.shiftKey) {
      event.preventDefault();
      emit("screen-on");
      return;
    }
    if (key === "o") {
      event.preventDefault();
      emit("screen-off");
      return;
    }
    if (key === "p") {
      event.preventDefault();
      emitKey(26);
      return;
    }
    if (key === "n" && event.shiftKey) {
      event.preventDefault();
      emit("collapse-panels");
      return;
    }
    if (key === "n") {
      event.preventDefault();
      emit("notification");
      return;
    }
    if (key === "m") {
      event.preventDefault();
      emitKey(82);
      return;
    }
    if (key === "h") {
      event.preventDefault();
      emitKey(3);
      return;
    }
    if (key === "b") {
      event.preventDefault();
      emitKey(4);
      return;
    }
    if (key === "s") {
      event.preventDefault();
      emitKey(187);
      return;
    }
    // Arrow keys are reserved for volume up/down ONLY now. The old
    // Shift+Arrow modifiers used to flip/rotate the local-only display
    // view (a half-done feature that's been removed); leaving the
    // bindings in would consume keypresses the user expects to forward
    // to the device as plain arrow keys.
    if (event.key === "ArrowUp") {
      event.preventDefault();
      emitKey(24);
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      emitKey(25);
      return;
    }
  }

  if (
    event.altKey &&
    event.shiftKey &&
    !event.ctrlKey &&
    !event.metaKey &&
    event.key.toLowerCase() === "v"
  ) {
    event.preventDefault();
    void pasteBrowserClipboardAsText();
    return;
  }

  const directMap = {
    Backspace: 67,
    Delete: 112,
    Enter: 66,
    Escape: 111,
    " ": 62,
    ArrowUp: 19,
    ArrowDown: 20,
    ArrowLeft: 21,
    ArrowRight: 22,
    Home: 3,
    End: 123,
    PageUp: 92,
    PageDown: 93,
  };

  const mapped = directMap[event.key];
  if (mapped) {
    event.preventDefault();
    emitKey(mapped);
    return;
  }

  // Printable single-character keys → forward as TYPE_INJECT_TEXT so the
  // device's focused input field actually receives them. Mirrors how
  // upstream scrcpy works: ASCII keystrokes type into Android directly.
  //
  // Requirements:
  //   - exactly one printable character (event.key === "a", not
  //     "Shift" or "ArrowUp")
  //   - no modifier currently held — Ctrl/Alt/Meta combos went through
  //     the keyboard-shortcut branches above and shouldn't double-fire.
  //   - The handler returns to the document, so Tab still moves focus,
  //     F-keys still trigger devtools, etc.
  //
  // IME / multi-byte input (pinyin, 仮名, 한글) is handled separately
  // via the ``compositionend`` listener on ``window`` — see below.
  if (
    event.key.length === 1 &&
    !event.ctrlKey &&
    !event.altKey &&
    !event.metaKey
  ) {
    event.preventDefault();
    emit("send-text", event.key);
  }
}

// IME-composed text (e.g. pinyin → 中文 commit). The host OS's IME
// intercepts the raw keystrokes and fires ``compositionend`` with the
// final committed string on the active element. We listen on
// ``window`` so it works whether the user is focused on the body or a
// (rare) capture sink. Without this, Chinese / Japanese / Korean input
// would silently swallow keys with no text reaching the device.
function sendComposition(event) {
  if (!captureKeyboard.value || !props.deviceId) return;
  const target = event.target;
  const tagName = target?.tagName?.toLowerCase();
  if (
    tagName === "input" ||
    tagName === "textarea" ||
    tagName === "select" ||
    target?.isContentEditable
  ) {
    // Don't shadow a real input field that the user is intentionally
    // typing into (the drawer's text composer, settings inputs, etc.).
    return;
  }
  const text = event.data;
  if (typeof text === "string" && text.length > 0) {
    emit("send-text", text);
  }
}

watch(bindings, () => persistBindings(bindings.value), { deep: true });

onMounted(() => {
  window.addEventListener("keydown", sendHostKey);
  // ``compositionend`` is the only reliable browser signal for IME
  // committed text (pinyin → 中文, etc.). Most browsers fire it on
  // the active element; bubble-listen on window so we catch it for
  // both body-focus and any default-focused element.
  window.addEventListener("compositionend", sendComposition);
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", sendHostKey);
  window.removeEventListener("compositionend", sendComposition);
});
</script>
