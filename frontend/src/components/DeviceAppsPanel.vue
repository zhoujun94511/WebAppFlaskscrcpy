<template>
  <section
    class="apps-panel"
    @dragover.prevent="dragOver = true"
    @dragleave="dragOver = false"
    @drop.prevent="onDrop"
  >
    <header class="apps-toolbar">
      <select v-model="scope" :disabled="!deviceId" class="apps-scope">
        <option value="third_party">{{ t("apps.thirdParty") }}</option>
        <option value="system">{{ t("apps.system") }}</option>
        <option value="all">{{ t("apps.all") }}</option>
      </select>
      <input
        class="apps-filter"
        v-model="filterText"
        :placeholder="t('apps.filterPlaceholder')"
        :disabled="!deviceId"
      />
      <button
        class="ghost icon-text"
        :disabled="!deviceId"
        :title="t('apps.refresh')"
        @click="reload"
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
          <polyline points="23 4 23 10 17 10" />
          <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
        </svg>
      </button>
      <button
        class="ghost icon-text"
        :disabled="!deviceId"
        :title="t('apps.install')"
        @click="onInstallClick"
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
          <path d="M12 5v14" />
          <path d="M5 12l7 7 7-7" />
        </svg>
        {{ t("apps.install") }}
      </button>
      <input
        ref="fileInputRef"
        type="file"
        accept=".apk"
        class="hidden-file-input"
        @change="onApkPicked"
      />
      <span class="apps-counter"
        >{{ visible.length }} / {{ packages.length }}</span
      >
    </header>

    <div v-if="!deviceId" class="apps-empty">{{ t("apps.selectFirst") }}</div>
    <div v-else-if="loading" class="apps-empty">{{ t("apps.loading") }}</div>
    <div v-else-if="error" class="files-error-card">
      <strong>{{ friendlyError.title }}</strong>
      <p class="tip subtle">{{ friendlyError.hint }}</p>
      <details v-if="friendlyError.raw" class="files-error-details">
        <summary>{{ t("files.errorDetails") }}</summary>
        <code>{{ friendlyError.raw }}</code>
      </details>
      <div class="files-error-actions">
        <button class="ghost small" @click="reload">{{ t("apps.refresh") }}</button>
      </div>
    </div>
    <div v-else-if="!visible.length" class="apps-empty">
      {{ t("apps.emptyList") }}
    </div>

    <div v-else class="apps-list">
      <div
        v-for="p in visible"
        :key="p.package"
        class="apps-row"
        :class="{ system: p.system }"
      >
        <div class="apps-row-icon">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.6"
            width="20"
            height="20"
          >
            <rect x="3" y="3" width="7" height="7" rx="1" />
            <rect x="14" y="3" width="7" height="7" rx="1" />
            <rect x="3" y="14" width="7" height="7" rx="1" />
            <rect x="14" y="14" width="7" height="7" rx="1" />
          </svg>
        </div>
        <div class="apps-row-meta">
          <div class="apps-row-pkg" :title="p.apk_path">{{ p.package }}</div>
          <div class="apps-row-sub">
            <span :class="['apps-tag', p.system ? 'sys' : 'user']">{{
              p.system ? t("apps.system") : t("apps.thirdParty")
            }}</span>
            <span class="apps-size">{{ fmtBytes(p.size) }}</span>
          </div>
        </div>
        <div class="apps-row-actions">
          <button
            class="card-action"
            :title="t('apps.exportApk')"
            @click.stop="onExport(p)"
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
          </button>
          <button
            class="card-action danger"
            :title="t('apps.uninstall')"
            :disabled="p.system"
            @click.stop="onUninstall(p)"
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
          </button>
        </div>
      </div>
    </div>

    <div v-if="dragOver" class="files-drop-overlay">
      {{ t("apps.dropApk") }}
    </div>
    <div v-if="busy" class="files-status">{{ busy }}</div>
  </section>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { useUiI18n } from "../composables/useUiI18n";

const { t } = useUiI18n();

const props = defineProps({
  deviceId: { type: String, default: "" },
});

const scope = ref("third_party");
const filterText = ref("");
const packages = ref([]);
const loading = ref(false);
const error = ref("");
const busy = ref("");
const dragOver = ref(false);
const fileInputRef = ref(null);

const visible = computed(() => {
  const needle = filterText.value.trim().toLowerCase();
  if (!needle) return packages.value;
  return packages.value.filter((p) => p.package.toLowerCase().includes(needle));
});

// Map raw fetch/server errors into localized {title, hint, raw}. Pattern
// matches on the *English* substring the backend / browser emits because
// those texts aren't translated server-side. Mirrors the DeviceFilesPanel
// approach so error UX stays consistent across the drawer's tabs.
const friendlyError = computed(() => {
  const raw = String(error.value || "");
  const lc = raw.toLowerCase();
  let key = "apps.errorUnknown";
  // The classic "Failed to fetch" — browser couldn't reach the dev
  // server (backend crashed, address changed, port closed).
  if (lc.includes("failed to fetch") || lc.includes("networkerror") || lc.includes("network error")) {
    key = "apps.errorNetwork";
  } else if (lc.startsWith("http 5") || lc.includes("internal server error")) {
    key = "apps.errorServer";
  } else if (lc.startsWith("http 4") || lc.includes("bad request") || lc.includes("not found")) {
    key = "apps.errorBadRequest";
  } else if (lc.includes("timeout") || lc.includes("timed out")) {
    key = "apps.errorTimeout";
  } else if (lc.includes("device") && (lc.includes("not found") || lc.includes("offline"))) {
    key = "apps.errorNoDevice";
  }
  return { title: t(key), hint: t(`${key}Hint`), raw };
});

async function reload() {
  if (!props.deviceId) return;
  loading.value = true;
  error.value = "";
  try {
    const url = `/api/device/${encodeURIComponent(props.deviceId)}/apps?scope=${scope.value}`;
    const r = await fetch(url);
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok)
      throw new Error(data.error || data.message || `HTTP ${r.status}`);
    packages.value = data.packages || [];
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
    packages.value = [];
  } finally {
    loading.value = false;
  }
}

function onExport(p) {
  const url = `/api/device/${encodeURIComponent(props.deviceId)}/apps/${encodeURIComponent(p.package)}/apk`;
  const a = document.createElement("a");
  a.href = url;
  a.download = `${p.package}.apk`;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function onUninstall(p) {
  if (p.system) return;
  if (!window.confirm(t("apps.confirmUninstall", { pkg: p.package }))) return;
  busy.value = t("apps.uninstalling");
  try {
    const r = await fetch(
      `/api/device/${encodeURIComponent(props.deviceId)}/apps/${encodeURIComponent(p.package)}`,
      {
        method: "DELETE",
      },
    );
    const data = await r.json().catch(() => ({}));
    if (data.status !== "ok")
      throw new Error(data.message || "uninstall failed");
    await reload();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    busy.value = "";
  }
}

function onInstallClick() {
  fileInputRef.value?.click();
}

async function onApkPicked(ev) {
  const f = ev.target?.files?.[0];
  ev.target.value = "";
  if (!f) return;
  await installApk(f);
}

async function onDrop(ev) {
  dragOver.value = false;
  const f = [...(ev.dataTransfer?.files || [])].find((x) =>
    /\.apk$/i.test(x.name),
  );
  if (f) await installApk(f);
}

async function installApk(file, { force = false } = {}) {
  busy.value = force
    ? t("apps.installingReplace", { name: file.name }) ||
      `重新安装 ${file.name}(先卸载冲突应用)...`
    : t("apps.installing", { name: file.name });
  try {
    const fd = new FormData();
    fd.append("file", file, file.name);
    if (force) fd.append("force", "1");
    const r = await fetch(
      `/api/device/${encodeURIComponent(props.deviceId)}/apps/install`,
      {
        method: "POST",
        body: fd,
      },
    );
    const data = await r.json().catch(() => ({}));
    if (data.status === "ok") {
      await reload();
      return;
    }
    // Signature mismatch: silently auto-recover via uninstall+reinstall.
    // The earlier behaviour (window.confirm prompt) blocked the entire
    // UI thread, which the user explicitly objected to — for this
    // developer-facing tool, dragging an APK in is already explicit
    // intent to install it; if data preservation matters, the user
    // would manually uninstall first. The status chip ``busy.value``
    // switches to the "reinstall after uninstall" wording so the user
    // can still see what's happening, just without a blocking dialog.
    if (data.error_code === "signature_mismatch" && !force) {
      await installApk(file, { force: true });
      return;
    }
    throw new Error(data.message || "install failed");
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    busy.value = "";
  }
}

function fmtBytes(n) {
  if (!n) return "-";
  const u = ["B", "KB", "MB", "GB"];
  let v = n,
    i = 0;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
}

watch(
  [() => props.deviceId, scope],
  ([id]) => {
    if (id) void reload();
    else packages.value = [];
  },
  { immediate: true },
);
</script>
