<template>
  <section
    class="files-panel"
    @dragover.prevent="dragOver = true"
    @dragleave="dragOver = false"
    @drop.prevent="onDrop"
  >
    <header class="files-toolbar">
      <button
        class="ghost icon-text"
        :disabled="!canGoUp"
        :title="t('files.up')"
        @click="navigate(parent)"
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
          <line x1="12" y1="19" x2="12" y2="5" />
          <polyline points="5 12 12 5 19 12" />
        </svg>
        {{ t("files.up") }}
      </button>
      <button
        class="ghost icon-text"
        :disabled="!deviceId"
        :title="t('files.home')"
        @click="navigate('/sdcard')"
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
          <path d="M3 12l9-9 9 9" />
          <path d="M5 10v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V10" />
        </svg>
        {{ t("files.home") }}
      </button>
      <input
        class="files-path"
        v-model="pathInput"
        :placeholder="t('files.pathPlaceholder')"
        :disabled="!deviceId"
        @keydown.enter="navigate(pathInput)"
      />
      <button
        class="ghost icon-text"
        :disabled="!deviceId"
        :title="t('files.refresh')"
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
        :title="t('files.newFolder')"
        @click="onNewFolder"
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
          <path
            d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"
          />
          <line x1="12" y1="11" x2="12" y2="17" />
          <line x1="9" y1="14" x2="15" y2="14" />
        </svg>
        {{ t("files.newFolder") }}
      </button>
      <button
        class="ghost icon-text"
        :disabled="!deviceId"
        :title="t('files.upload')"
        @click="onUploadClick"
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
          <polyline points="17 8 12 3 7 8" />
          <line x1="12" y1="3" x2="12" y2="15" />
        </svg>
        {{ t("files.upload") }}
      </button>
      <input
        ref="fileInputRef"
        type="file"
        multiple
        class="hidden-file-input"
        @change="onFilesPicked"
      />
    </header>

    <div v-if="!deviceId" class="files-empty">{{ t("files.selectFirst") }}</div>
    <div v-else-if="loading" class="files-empty">{{ t("files.loading") }}</div>
    <div v-else-if="error" class="files-error-card">
      <strong>{{ friendlyError.title }}</strong>
      <p class="tip subtle">{{ friendlyError.hint }}</p>
      <details v-if="friendlyError.raw" class="files-error-details">
        <summary>{{ t("files.errorDetails") }}</summary>
        <code>{{ friendlyError.raw }}</code>
      </details>
      <div class="files-error-actions">
        <button
          v-if="path !== '/sdcard'"
          class="ghost small"
          @click="navigate('/sdcard')"
        >
          {{ t("files.gotoSdcard") }}
        </button>
        <button class="ghost small" @click="reload">
          {{ t("files.refresh") }}
        </button>
      </div>
    </div>
    <div v-else-if="!entries.length" class="files-empty">
      {{ t("files.emptyDir") }}
    </div>

    <div v-else ref="listRef" class="files-list">
      <div
        v-for="e in entries"
        :key="e.path"
        class="files-row"
        :class="{ dir: e.is_dir }"
        @click="onRowClick(e)"
        @dblclick="onRowDblClick(e)"
      >
        <div class="files-row-icon">
          <svg
            v-if="e.is_dir"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.6"
            width="18"
            height="18"
          >
            <path
              d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"
            />
          </svg>
          <svg
            v-else
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.6"
            width="18"
            height="18"
          >
            <path
              d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"
            />
            <polyline points="14 2 14 8 20 8" />
          </svg>
        </div>
        <div class="files-row-body">
          <div class="files-row-name" :title="e.path">{{ e.name }}</div>
          <div class="files-row-meta">
            <span v-if="!e.is_dir">{{ fmtBytes(e.size) }}</span>
            <span v-if="!e.is_dir" class="files-row-meta-sep">·</span>
            <span>{{ fmtMtime(e.mtime) }}</span>
          </div>
        </div>
        <div class="files-row-actions">
          <button
            v-if="!e.is_dir"
            class="card-action"
            :class="{ subtle: !previewKindOf(e.name) }"
            :title="
              previewKindOf(e.name)
                ? t('files.preview')
                : t('files.previewUnsupportedHint')
            "
            @click.stop="openPreview(e)"
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
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
              <circle cx="12" cy="12" r="3" />
            </svg>
          </button>
          <button
            v-if="!e.is_dir"
            class="card-action"
            :title="t('files.download')"
            @click.stop="onDownload(e)"
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
            class="card-action"
            :title="t('files.rename')"
            @click.stop="onRename(e)"
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
              <path
                d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"
              />
              <path
                d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"
              />
            </svg>
          </button>
          <button
            class="card-action danger"
            :title="t('files.delete')"
            @click.stop="onDelete(e)"
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
      {{ t("files.dropHere") }}
    </div>
    <div v-if="busy" class="files-status">{{ busy }}</div>

    <div
      v-if="preview.open"
      class="files-preview-overlay"
      @click.self="closePreview"
    >
      <div class="files-preview-modal">
        <header class="files-preview-head">
          <strong class="files-preview-name" :title="preview.path">{{
            preview.name
          }}</strong>
          <div class="files-preview-actions">
            <button
              class="ghost small"
              @click="onDownload(preview.entry)"
              :disabled="!preview.entry"
            >
              {{ t("files.download") }}
            </button>
            <button
              class="icon-btn"
              :title="t('actions.close')"
              @click="closePreview"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
                width="16"
                height="16"
              >
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
        </header>
        <div
          class="files-preview-body"
          :class="`kind-${preview.kind || 'unsupported'}`"
        >
          <p v-if="preview.error" class="files-error">{{ preview.error }}</p>
          <p v-else-if="preview.loading" class="files-empty">
            {{ t("files.loading") }}
          </p>

          <pre v-else-if="preview.kind === 'text'" class="files-preview-text">{{
            preview.text
          }}</pre>
          <img
            v-else-if="preview.kind === 'image'"
            :src="preview.url"
            :alt="preview.name"
          />
          <audio
            v-else-if="preview.kind === 'audio'"
            :src="preview.url"
            controls
            preload="metadata"
          />
          <iframe v-else-if="preview.kind === 'pdf'" :src="preview.url" />

          <div v-else class="files-preview-unsupported">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.5"
              width="48"
              height="48"
              aria-hidden="true"
            >
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            <strong>{{ t("files.previewUnsupported") }}</strong>
            <p class="tip subtle">{{ t("files.previewUnsupportedHint") }}</p>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { useUiI18n } from "../composables/useUiI18n";

const { t } = useUiI18n();

const props = defineProps({
  deviceId: { type: String, default: "" },
});

const path = ref("/sdcard");
const pathInput = ref("/sdcard");
const parent = ref("/");
const entries = ref([]);
const loading = ref(false);
const error = ref("");
const busy = ref("");
const dragOver = ref(false);

// Preview modal state. ``kind`` ∈ 'text'|'image'|'audio'|'pdf'|null.
// ``text`` is a pre-fetched string for the text branch only; binary
// kinds set ``url`` to the inline pull endpoint and let the browser
// stream the bytes — no blob URL juggling, no memory pressure here.
const preview = reactive({
  open: false,
  kind: null,
  name: "",
  path: "",
  entry: null,
  url: "",
  text: "",
  loading: false,
  error: "",
});

// Extension → preview kind mapping. Conservative on text: only formats
// that are reliably UTF-8 / ASCII and meaningful in a <pre> are listed.
// Anything else falls through to "no preview" and the user gets the
// regular Download button.
const PREVIEW_EXTENSIONS = {
  text: new Set([
    "txt",
    "log",
    "md",
    "rst",
    "json",
    "xml",
    "yaml",
    "yml",
    "csv",
    "tsv",
    "ini",
    "conf",
    "cfg",
    "properties",
    "env",
    "sh",
    "bash",
    "zsh",
    "py",
    "js",
    "mjs",
    "ts",
    "tsx",
    "jsx",
    "vue",
    "svelte",
    "html",
    "htm",
    "css",
    "scss",
    "less",
    "java",
    "kt",
    "kts",
    "gradle",
    "pro",
    "c",
    "cc",
    "cpp",
    "cxx",
    "h",
    "hpp",
    "go",
    "rs",
    "php",
    "rb",
    "lua",
    "pl",
    "sql",
    "gitignore",
    "editorconfig",
    "dockerfile",
    "smali",
    "aidl",
  ]),
  image: new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "svg"]),
  audio: new Set(["mp3", "wav", "m4a", "aac", "ogg", "opus", "flac"]),
  pdf: new Set(["pdf"]),
};

// 1 MB cap on text preview — anything bigger gets a "download instead"
// nudge. Reading multi-MB strings into <pre> trashes scroll perf and
// usually wasn't what the user wanted anyway.
const TEXT_PREVIEW_LIMIT = 1024 * 1024;
// 50 MB cap on binary preview — the backend pull buffers in memory; a
// bigger file should be downloaded with progress instead.
const BINARY_PREVIEW_LIMIT = 50 * 1024 * 1024;

function previewKindOf(name) {
  if (!name) return null;
  const i = name.lastIndexOf(".");
  if (i <= 0) return null;
  const ext = name.slice(i + 1).toLowerCase();
  for (const [kind, set] of Object.entries(PREVIEW_EXTENSIONS)) {
    if (set.has(ext)) return kind;
  }
  return null;
}

function pullUrl(entry, inline = false) {
  const base = `/api/device/${encodeURIComponent(props.deviceId)}/files/pull?path=${encodeURIComponent(entry.path)}`;
  return inline ? `${base}&inline=1` : base;
}

async function openPreview(entry) {
  // ``kind === null`` means the extension isn't on our allow-list — we
  // still open the modal so the user sees the explicit "not supported"
  // message with a Download fallback, instead of the button being a
  // mysterious no-op.
  const kind = previewKindOf(entry.name);
  preview.open = true;
  preview.kind = kind;
  preview.name = entry.name;
  preview.path = entry.path;
  preview.entry = entry;
  preview.text = "";
  preview.url = "";
  preview.error = "";
  preview.loading = false;

  if (!kind) return; // unsupported branch — body renders the fallback view

  const limit = kind === "text" ? TEXT_PREVIEW_LIMIT : BINARY_PREVIEW_LIMIT;
  if (Number.isFinite(entry.size) && entry.size > limit) {
    preview.error = t("files.previewTooLarge", { size: fmtBytes(entry.size) });
    return;
  }

  if (kind === "text") {
    preview.loading = true;
    try {
      const r = await fetch(pullUrl(entry, true));
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      // Stream the response so we can cap memory if the file lied about
      // its size (or stat said 0 for a special file). A simple .text()
      // would happily load 4 GB into the string heap.
      const reader = r.body.getReader();
      const decoder = new TextDecoder("utf-8", { fatal: false });
      let total = 0;
      let out = "";
      let truncated = false;
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        total += value.byteLength;
        if (total > TEXT_PREVIEW_LIMIT) {
          out += decoder.decode(
            value.slice(0, TEXT_PREVIEW_LIMIT - (total - value.byteLength)),
            { stream: true },
          );
          truncated = true;
          try {
            reader.cancel();
          } catch {
            /* nothing */
          }
          break;
        }
        out += decoder.decode(value, { stream: true });
      }
      out += decoder.decode();
      if (truncated) {
        out += `\n\n— ${t("files.previewTruncated")} —`;
      }
      preview.text = out;
    } catch (err) {
      preview.error = err?.message || String(err);
    } finally {
      preview.loading = false;
    }
  } else {
    // Image / audio / PDF — just point the element at the inline URL.
    // No fetch, no blob; the browser streams bytes as needed.
    preview.url = pullUrl(entry, true);
  }
}

function closePreview() {
  preview.open = false;
  preview.kind = null;
  preview.url = "";
  preview.text = "";
  preview.entry = null;
  preview.error = "";
  preview.loading = false;
}

// Esc closes the modal. We attach to window because the modal isn't
// always focused — clicking inside an <audio>/<iframe> swallows the
// keydown otherwise.
function onKeydown(e) {
  if (preview.open && e.key === "Escape") closePreview();
}
if (typeof window !== "undefined") {
  window.addEventListener("keydown", onKeydown);
}
onBeforeUnmount(() => {
  if (typeof window !== "undefined")
    window.removeEventListener("keydown", onKeydown);
});
const fileInputRef = ref(null);
const listRef = ref(null);

const canGoUp = computed(() => path.value !== "/" && !!props.deviceId);

// Map raw adb/sync errors into a friendly localized title + hint. Pattern
// matching is on the *English* substrings adbutils / Android emit because
// that text comes from libc / kernel and isn't translated server-side.
const friendlyError = computed(() => {
  const raw = String(error.value || "");
  const lc = raw.toLowerCase();
  let key = "files.errorUnknown";
  // Errno 22 (EINVAL) — sync server rejects the path. Usually means we
  // tried to list "/" or another non-syncable special directory.
  if (lc.includes("invalid argument") || lc.includes("errno 22")) {
    key = "files.errorInvalidPath";
  } else if (lc.includes("permission denied") || lc.includes("eacces")) {
    key = "files.errorPermission";
  } else if (
    lc.includes("no such file") ||
    lc.includes("not found") ||
    lc.includes("enoent")
  ) {
    key = "files.errorNotFound";
  } else if (lc.includes("not a directory") || lc.includes("enotdir")) {
    key = "files.errorNotDir";
  } else if (lc.includes("device") && lc.includes("not found")) {
    key = "files.errorNoDevice";
  } else if (lc.includes("timeout") || lc.includes("timed out")) {
    key = "files.errorTimeout";
  }
  return {
    title: t(key),
    hint: t(`${key}Hint`),
    raw,
  };
});

async function reload() {
  if (!props.deviceId) return;
  loading.value = true;
  error.value = "";
  try {
    const url = `/api/device/${encodeURIComponent(props.deviceId)}/files?path=${encodeURIComponent(path.value)}`;
    const r = await fetch(url);
    const data = await r.json().catch(() => ({}));
    if (!data.ok && data.error) throw new Error(data.error);
    entries.value = data.entries || [];
    parent.value = data.parent || "/";
    pathInput.value = data.path || path.value;
    path.value = data.path || path.value;
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
    entries.value = [];
  } finally {
    loading.value = false;
  }
}

function navigate(next) {
  const target = (next || "").trim() || "/";
  path.value = target;
  void reload();
}

// Single click on a directory navigates into it — that's the standard
// file-manager UX. Files are opened only via the explicit "Download"
// action button to avoid accidentally pulling large blobs.
function onRowClick(e) {
  if (e.is_dir) navigate(e.path);
}
// Double click stays as a redundant accelerator: dir → enter, file →
// download. Some users will reach for double-click out of muscle memory.
function onRowDblClick(e) {
  if (e.is_dir) navigate(e.path);
  else onDownload(e);
}

async function onDownload(e) {
  const url = `/api/device/${encodeURIComponent(props.deviceId)}/files/pull?path=${encodeURIComponent(e.path)}`;
  // Use a hidden anchor so the browser handles streaming + Content-Disposition.
  const a = document.createElement("a");
  a.href = url;
  a.download = e.name;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function onRename(e) {
  const next = window.prompt(t("files.renameTo"), e.name);
  if (!next || next === e.name) return;
  busy.value = t("files.renaming");
  try {
    const sep = path.value === "/" ? "" : "/";
    const dst = path.value + sep + next;
    const r = await fetch(
      `/api/device/${encodeURIComponent(props.deviceId)}/files/rename`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ src: e.path, dst }),
      },
    );
    const data = await r.json().catch(() => ({}));
    if (data.status !== "ok") throw new Error(data.message || "rename failed");
    await reload();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    busy.value = "";
  }
}

async function onDelete(e) {
  const recursive = e.is_dir;
  const msg = recursive
    ? t("files.confirmDeleteDir", { name: e.name })
    : t("files.confirmDeleteFile", { name: e.name });
  if (!window.confirm(msg)) return;
  busy.value = t("files.deleting");
  try {
    const r = await fetch(
      `/api/device/${encodeURIComponent(props.deviceId)}/files`,
      {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: e.path, recursive }),
      },
    );
    const data = await r.json().catch(() => ({}));
    if (data.status !== "ok") throw new Error(data.message || "delete failed");
    await reload();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    busy.value = "";
  }
}

async function onNewFolder() {
  const name = window.prompt(t("files.newFolderName"), "NewFolder");
  if (!name) return;
  busy.value = t("files.creating");
  try {
    const sep = path.value === "/" ? "" : "/";
    const target = path.value + sep + name;
    const r = await fetch(
      `/api/device/${encodeURIComponent(props.deviceId)}/files/mkdir`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: target }),
      },
    );
    const data = await r.json().catch(() => ({}));
    if (data.status !== "ok") throw new Error(data.message || "mkdir failed");
    await reload();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    busy.value = "";
  }
}

function onUploadClick() {
  fileInputRef.value?.click();
}

async function onFilesPicked(ev) {
  const files = [...(ev.target?.files || [])];
  ev.target.value = "";
  await uploadFiles(files);
}

async function onDrop(ev) {
  dragOver.value = false;
  const files = [...(ev.dataTransfer?.files || [])];
  await uploadFiles(files);
}

async function uploadFiles(files) {
  if (!files.length || !props.deviceId) return;
  busy.value = t("files.uploading");
  try {
    const fd = new FormData();
    fd.append("dir", path.value);
    files.forEach((f) => fd.append("file", f, f.name));
    const r = await fetch(
      `/api/device/${encodeURIComponent(props.deviceId)}/files/push`,
      {
        method: "POST",
        body: fd,
      },
    );
    const data = await r.json().catch(() => ({}));
    if (data.status !== "ok") {
      // Partial / failed — surface the first error.
      const failed = (data.results || []).filter((r) => !r.ok);
      const msg = failed.length
        ? `${failed[0].name}: ${failed[0].error}`
        : data.message || "upload failed";
      throw new Error(msg);
    }
    await reload();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    busy.value = "";
  }
}

function fmtBytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let v = n,
    i = 0;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
}

function fmtMtime(t) {
  if (!t) return "-";
  try {
    return new Date(t * 1000).toLocaleString();
  } catch {
    return "-";
  }
}

watch(
  () => props.deviceId,
  (next) => {
    if (next) {
      path.value = "/sdcard";
      void reload();
    } else {
      entries.value = [];
    }
  },
  { immediate: true },
);
</script>
