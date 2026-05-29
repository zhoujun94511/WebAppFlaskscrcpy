<template>
  <section class="logcat-panel">
    <header class="logcat-toolbar">
      <select class="logcat-level" v-model="level" :disabled="!deviceId">
        <option value="V">V</option>
        <option value="D">D</option>
        <option value="I">I</option>
        <option value="W">W</option>
        <option value="E">E</option>
        <option value="F">F</option>
      </select>
      <input
        class="logcat-tag"
        v-model="tag"
        :placeholder="t('logcat.tagPlaceholder')"
        :disabled="!deviceId"
      />
      <input
        class="logcat-grep"
        v-model="grep"
        :placeholder="t('logcat.grepPlaceholder')"
      />
      <button
        class="ghost icon-text"
        :class="{ active: streaming }"
        :disabled="!deviceId"
        :title="streaming ? t('logcat.pause') : t('logcat.resume')"
        @click="toggleStream"
      >
        <svg
          v-if="streaming"
          viewBox="0 0 24 24"
          fill="currentColor"
          width="14"
          height="14"
        >
          <path d="M6 4h4v16H6zm8 0h4v16h-4z" />
        </svg>
        <svg
          v-else
          viewBox="0 0 24 24"
          fill="currentColor"
          width="14"
          height="14"
        >
          <path d="M8 5v14l11-7z" />
        </svg>
        {{ streaming ? t("logcat.pause") : t("logcat.resume") }}
      </button>
      <button
        class="ghost icon-text"
        :disabled="!deviceId"
        :title="t('logcat.clear')"
        @click="clear"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          width="14"
          height="14"
        >
          <polyline points="3 6 5 6 21 6" />
          <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
        </svg>
        {{ t("logcat.clear") }}
      </button>
      <button
        class="ghost icon-text"
        :disabled="!visible.length"
        :title="t('logcat.save')"
        @click="save"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          width="14"
          height="14"
        >
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
          <polyline points="7 10 12 15 17 10" />
          <line x1="12" y1="15" x2="12" y2="3" />
        </svg>
        {{ t("logcat.save") }}
      </button>
      <label class="logcat-autoscroll">
        <input type="checkbox" v-model="autoscroll" />
        {{ t("logcat.autoscroll") }}
      </label>
      <span class="logcat-counter">{{ visibleCount }} / {{ totalCount }}</span>
    </header>

    <div ref="viewRef" class="logcat-view" @scroll="onScroll">
      <div v-if="!deviceId" class="logcat-empty">
        {{ t("logcat.selectFirst") }}
      </div>
      <div v-else-if="!visible.length" class="logcat-empty">
        {{ streaming ? t("logcat.waiting") : t("logcat.paused") }}
      </div>
      <div
        v-for="(line, i) in visible"
        :key="line.k"
        class="logcat-line"
        :class="'lvl-' + line.lvl"
      >
        {{ line.raw }}
      </div>
    </div>

    <div v-if="error" class="logcat-error">{{ error }}</div>
  </section>
</template>

<script setup>
import {
  computed,
  nextTick,
  onBeforeUnmount,
  ref,
  shallowRef,
  watch,
} from "vue";
import { useUiI18n } from "../composables/useUiI18n";

const { t } = useUiI18n();

const props = defineProps({
  deviceId: { type: String, default: "" },
});

// ─── Tuning constants ──────────────────────────────────────────────
// chatty tags can emit 2000+ lines/sec. We must NOT update Vue reactivity
// on every line — instead we accumulate into a plain JS buffer and
// commit to a shallowRef at most every FLUSH_MS milliseconds. The DOM
// then diffs once per frame instead of once per line.
const MAX_LINES = 3000;
const FLUSH_MS = 80; // ~12 commits/sec
const RENDER_CAP = 600; // max DOM rows; older lines stay in buffer

const level = ref("I");
const tag = ref("");
const grep = ref("");
const streaming = ref(true);
const autoscroll = ref(true);
const error = ref("");

// shallowRef so the array reference being reassigned triggers updates,
// but individual line objects are not deeply tracked.
const lines = shallowRef([]);

// Plain JS buffer — NOT reactive. Mutated freely between flushes.
let pendingLines = [];
let allLines = []; // canonical buffer (also non-reactive)
let pendingBytes = 0; // for save() — count via buffer instead of lines.value

const viewRef = ref(null);
let es = null;
let seq = 0;
let flushTimer = null;

// ─── Filtering — skip the work entirely when no needle ────────────
const visible = computed(() => {
  const needle = grep.value.trim().toLowerCase();
  const all = lines.value;
  if (!needle) {
    return all.length > RENDER_CAP ? all.slice(all.length - RENDER_CAP) : all;
  }
  // Filter then cap at RENDER_CAP to keep DOM cheap.
  const out = [];
  for (let i = all.length - 1; i >= 0 && out.length < RENDER_CAP; i -= 1) {
    if (all[i].raw.toLowerCase().includes(needle)) out.push(all[i]);
  }
  return out.reverse();
});

// totalCount = entries currently held in the reactive shallowRef
// (lines.value === allLines after flush, capped at MAX_LINES). Reading
// the raw `allLines` here would NOT be reactive.
const totalCount = computed(() => lines.value.length);
const visibleCount = computed(() => visible.value.length);

// ─── Stream lifecycle ─────────────────────────────────────────────
function open() {
  close();
  if (!props.deviceId || !streaming.value) return;
  error.value = "";
  const params = new URLSearchParams({ level: level.value });
  if (tag.value.trim()) params.set("tag", tag.value.trim());
  es = new EventSource(
    `/api/device/${encodeURIComponent(props.deviceId)}/logcat?${params}`,
  );
  es.addEventListener("message", onMessage);
  es.addEventListener("error", () => {
    error.value = t("logcat.connectionLost");
  });
  es.addEventListener("open", () => {
    error.value = "";
  });
  startFlushTimer();
}

function close() {
  if (es) {
    try {
      es.close();
    } catch {
      /* ignore */
    }
    es = null;
  }
  stopFlushTimer();
}

function onMessage(ev) {
  const raw = ev.data;
  if (!raw) return;
  let lvl = "I";
  const m = raw.match(/\s([VDIWEF])\s/);
  if (m) lvl = m[1];
  seq += 1;
  // Push to the NON-REACTIVE buffer. Vue doesn't see this.
  pendingLines.push({ k: seq, raw, lvl });
  pendingBytes += raw.length;
}

function startFlushTimer() {
  if (flushTimer) return;
  flushTimer = setInterval(flush, FLUSH_MS);
}

function stopFlushTimer() {
  if (!flushTimer) return;
  clearInterval(flushTimer);
  flushTimer = null;
}

function flush() {
  if (!pendingLines.length) return;
  // Move pending into the canonical buffer, drop the oldest if over cap.
  allLines = allLines.concat(pendingLines);
  pendingLines = [];
  if (allLines.length > MAX_LINES) {
    allLines = allLines.slice(allLines.length - MAX_LINES);
  }
  // Single reactive write per flush — Vue diffs the DOM once.
  lines.value = allLines;
  if (autoscroll.value) void scrollToBottom();
}

async function scrollToBottom() {
  await nextTick();
  const el = viewRef.value;
  if (!el) return;
  el.scrollTop = el.scrollHeight;
}

function onScroll() {
  const el = viewRef.value;
  if (!el) return;
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  if (!atBottom && autoscroll.value) autoscroll.value = false;
  if (atBottom && !autoscroll.value) autoscroll.value = true;
}

function toggleStream() {
  streaming.value = !streaming.value;
  if (streaming.value) open();
  else close();
}

async function clear() {
  pendingLines = [];
  allLines = [];
  pendingBytes = 0;
  lines.value = [];
  if (props.deviceId) {
    try {
      await fetch(
        `/api/device/${encodeURIComponent(props.deviceId)}/logcat/clear`,
        { method: "POST" },
      );
    } catch {
      /* ignore — UI buffer is already empty */
    }
  }
}

function save() {
  // Save what's actually visible to the user (respects current filter).
  const blob = new Blob([visible.value.map((l) => l.raw).join("\n")], {
    type: "text/plain;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  a.href = url;
  a.download = `logcat-${props.deviceId}-${stamp}.txt`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ─── Reactive triggers: rewire stream on device / filter changes ────
watch(
  () => props.deviceId,
  () => {
    pendingLines = [];
    allLines = [];
    pendingBytes = 0;
    lines.value = [];
    if (streaming.value) open();
    else close();
  },
  { immediate: true },
);

// Changing the level / tag filter must also clear the buffered lines.
// Without this, rows captured under the OLD filter stay in the
// rendered list (mixed with new-filter rows) until they age out of
// MAX_LINES — confusing the user who explicitly narrowed the view.
// Same buffer-reset shape as the deviceId watcher above.
watch([level, tag], () => {
  pendingLines = [];
  allLines = [];
  pendingBytes = 0;
  lines.value = [];
  if (streaming.value) open();
});

onBeforeUnmount(() => close());
</script>
