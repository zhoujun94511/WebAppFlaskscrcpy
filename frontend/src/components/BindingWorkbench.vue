<template>
  <section class="section binding-workbench">
    <div class="binding-filter">
      <input
        :value="searchText"
        type="search"
        class="binding-search"
        :placeholder="t('keypad.searchBinding')"
        @input="$emit('update:searchText', $event.target.value)"
      />
      <select
        :value="activeGroup"
        class="binding-group-select"
        @change="$emit('update:activeGroup', $event.target.value)"
      >
        <option v-for="group in groups" :key="group" :value="group">
          {{ groupLabel(group) }}
        </option>
      </select>
    </div>

    <div class="binding-grid">
      <button
        v-for="binding in visibleBindings"
        :key="binding.id"
        class="binding-key"
        :disabled="!deviceId"
        @click="$emit('send-binding', binding)"
        :title="
          t('keypad.bindingTooltip', {
            label: localizedLabel(binding),
            keycode: binding.keycode,
          })
        "
      >
        <span class="binding-key-label">{{ localizedLabel(binding) }}</span>
        <small class="binding-key-code">{{ binding.keycode }}</small>
      </button>
    </div>

    <p v-if="visibleBindings.length === 0" class="tip subtle">
      {{ t("keypad.noBindingsMatch") }}
    </p>

    <p v-if="importMessage" class="tip subtle">{{ importMessage }}</p>

    <details class="binding-tools">
      <summary>{{ t("keypad.tools") }}</summary>

      <div class="binding-tools-row">
        <button class="ghost small" @click="$emit('restore-defaults')">
          {{ t("keypad.restoreDefaults") }}
        </button>
        <button class="ghost small" @click="$emit('export-json')">
          {{ t("keypad.exportJson") }}
        </button>
        <button class="ghost small" @click="$emit('import-json')">
          {{ t("keypad.importJson") }}
        </button>
        <button
          class="ghost small"
          @click="$emit('update:showEditor', !showEditor)"
        >
          {{ showEditor ? t("keypad.hideMappings") : t("keypad.editMappings") }}
        </button>
      </div>

      <div v-if="showEditor" class="mapping-list">
        <article
          v-for="binding in visibleBindings"
          :key="binding.id"
          class="mapping-row"
        >
          <div class="mapping-main">
            <strong>{{ localizedLabel(binding) }}</strong>
            <small>{{ groupLabel(binding.group) }}</small>
          </div>
          <div class="mapping-actions">
            <input
              v-model="binding.label"
              type="text"
              :placeholder="t('keypad.label')"
            />
            <select v-model.number="binding.keycode">
              <option
                v-for="keycode in commonKeycodes"
                :key="keycode.code"
                :value="keycode.code"
              >
                {{ keycode.name }} ({{ keycode.code }})
              </option>
            </select>
          </div>
        </article>
      </div>

      <div class="custom-card">
        <strong class="custom-card-title">{{ t("keypad.addCustom") }}</strong>
        <input
          v-model="draft.label"
          class="custom-label"
          type="text"
          :placeholder="t('keypad.label')"
        />
        <div class="custom-row">
          <select v-model="draft.group">
            <option v-for="group in editableGroups" :key="group" :value="group">
              {{ groupLabel(group) }}
            </option>
          </select>
          <select v-model.number="draft.keycode">
            <option
              v-for="keycode in commonKeycodes"
              :key="keycode.code"
              :value="keycode.code"
            >
              {{ keycode.name }} ({{ keycode.code }})
            </option>
          </select>
        </div>
        <div class="custom-actions">
          <button
            class="primary small"
            :disabled="!draft.label.trim()"
            @click="$emit('add-binding')"
          >
            {{ t("keypad.add") }}
          </button>
        </div>
      </div>
    </details>
  </section>
</template>

<script setup>
import { computed } from "vue";
import { useUiI18n } from "../composables/useUiI18n";

const { t } = useUiI18n();

const props = defineProps({
  deviceId: {
    type: String,
    default: "",
  },
  bindings: {
    type: Array,
    default: () => [],
  },
  groups: {
    type: Array,
    default: () => [],
  },
  editableGroups: {
    type: Array,
    default: () => [],
  },
  commonKeycodes: {
    type: Array,
    default: () => [],
  },
  searchText: {
    type: String,
    default: "",
  },
  activeGroup: {
    type: String,
    default: "all",
  },
  showEditor: {
    type: Boolean,
    default: false,
  },
  importMessage: {
    type: String,
    default: "",
  },
  draft: {
    type: Object,
    default: () => ({}),
  },
});

defineEmits([
  "update:searchText",
  "update:activeGroup",
  "update:showEditor",
  "restore-defaults",
  "export-json",
  "import-json",
  "add-binding",
  "send-binding",
]);

const visibleBindings = computed(() => {
  const query = props.searchText.trim().toLowerCase();
  return props.bindings.filter((binding) => {
    const matchesGroup =
      props.activeGroup === "all" || binding.group === props.activeGroup;
    const matchesSearch =
      !query ||
      binding.label.toLowerCase().includes(query) ||
      localizedLabel(binding).toLowerCase().includes(query) ||
      String(binding.keycode).includes(query) ||
      binding.group.toLowerCase().includes(query);
    return matchesGroup && matchesSearch;
  });
});

/**
 * Default bindings (id ∈ DEFAULT_KEY_BINDINGS) have their display label
 * looked up under ``bindings.<id>`` so they follow the active locale.
 * User-added / imported bindings live outside that key set and keep
 * whatever label the user typed — we don't second-guess their text.
 */
function localizedLabel(binding) {
  if (!binding) return "";
  const key = `bindings.${binding.id}`;
  const translated = t(key);
  // t() returns the path itself when the key is missing — treat that as
  // "no translation, use the raw label".
  if (translated && translated !== key) return translated;
  return binding.label;
}

function groupLabel(group) {
  return (
    {
      all: t("keypad.all"),
      navigation: t("keypad.navigation"),
      system: t("keypad.system"),
      dpad: t("keypad.dpad"),
      typing: t("keypad.typing"),
      clipboard: t("keypad.clipboard"),
      custom: t("keypad.custom"),
    }[group] || group
  );
}
</script>
