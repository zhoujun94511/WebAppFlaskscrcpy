<template>
  <nav
    v-if="devices.length > 1"
    class="device-strip-wrap"
    :aria-label="t('viewMode.strip')"
  >
    <!-- Jump-to-any picker: a searchable dropdown that scales to many
         devices without endless horizontal scrolling. -->
    <div class="device-picker" ref="pickerRoot">
      <button
        class="device-picker-btn"
        :class="{ open: pickerOpen }"
        type="button"
        :title="t('device.pickTitle')"
        @click="togglePicker"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <rect x="3" y="3" width="7" height="7" rx="1" />
          <rect x="14" y="3" width="7" height="7" rx="1" />
          <rect x="3" y="14" width="7" height="7" rx="1" />
          <rect x="14" y="14" width="7" height="7" rx="1" />
        </svg>
        <span class="device-picker-count">{{ devices.length }}</span>
      </button>

      <div v-if="pickerOpen" class="device-picker-pop" role="listbox">
        <input
          ref="searchInput"
          v-model.trim="query"
          class="device-picker-search"
          :placeholder="t('device.searchPlaceholder')"
          @keydown.esc="closePicker"
        />
        <ul class="device-picker-list">
          <li v-for="id in filtered" :key="id">
            <button
              class="device-picker-option"
              :class="{ active: id === activeDeviceId }"
              role="option"
              :aria-selected="id === activeDeviceId"
              @click="select(id)"
            >
              <span
                class="device-strip-dot"
                :class="streamingSet.has(id) ? 'ok' : 'idle'"
              />
              <span class="device-picker-name">{{ names[id] || id }}</span>
              <span v-if="names[id]" class="device-picker-serial">{{ id }}</span>
            </button>
          </li>
          <li v-if="!filtered.length" class="device-picker-empty">
            {{ t("device.noMatch") }}
          </li>
        </ul>
      </div>
    </div>

    <!-- Quick switch strip: horizontally scrollable, active item kept in view. -->
    <div class="device-strip" role="tablist" ref="stripRef">
      <button
        v-for="id in devices"
        :key="id"
        class="device-strip-item"
        :class="{
          active: id === activeDeviceId,
          streaming: streamingSet.has(id),
        }"
        :data-id="id"
        :title="names[id] || id"
        role="tab"
        :aria-selected="id === activeDeviceId"
        @click="$emit('focus', id)"
      >
        <span
          class="device-strip-dot"
          :class="streamingSet.has(id) ? 'ok' : 'idle'"
        />
        <span class="device-strip-label">{{ names[id] || id }}</span>
      </button>
    </div>
  </nav>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useUiI18n } from "../composables/useUiI18n";

const { t } = useUiI18n();

const props = defineProps({
  devices: { type: Array, default: () => [] },
  activeDeviceId: { type: String, default: "" },
  streamingIds: { type: Array, default: () => [] },
  // Friendly-name lookup keyed by serial. Falls back to the serial when
  // a name hasn't been resolved yet (cold cache / device offline).
  names: { type: Object, default: () => ({}) },
});

const emit = defineEmits(["focus"]);

const streamingSet = computed(() => new Set(props.streamingIds));

// ── jump-to-any picker ──────────────────────────────────────────────
const pickerOpen = ref(false);
const query = ref("");
const pickerRoot = ref(null);
const searchInput = ref(null);

const filtered = computed(() => {
  const q = query.value.toLowerCase();
  if (!q) return props.devices;
  return props.devices.filter((id) => {
    const name = (props.names[id] || "").toLowerCase();
    return id.toLowerCase().includes(q) || name.includes(q);
  });
});

function onDocClick(ev) {
  if (pickerRoot.value && !pickerRoot.value.contains(ev.target)) closePicker();
}
function togglePicker() {
  if (pickerOpen.value) closePicker();
  else openPicker();
}
function openPicker() {
  pickerOpen.value = true;
  query.value = "";
  document.addEventListener("click", onDocClick, true);
  nextTick(() => searchInput.value?.focus());
}
function closePicker() {
  pickerOpen.value = false;
  document.removeEventListener("click", onDocClick, true);
}
function select(id) {
  emit("focus", id);
  closePicker();
}

// ── keep the active chip scrolled into view on switch ───────────────
const stripRef = ref(null);
watch(
  () => props.activeDeviceId,
  () => {
    nextTick(() => {
      const el = stripRef.value?.querySelector(".device-strip-item.active");
      el?.scrollIntoView({
        block: "nearest",
        inline: "nearest",
        behavior: "smooth",
      });
    });
  },
);

onBeforeUnmount(() => document.removeEventListener("click", onDocClick, true));
</script>
