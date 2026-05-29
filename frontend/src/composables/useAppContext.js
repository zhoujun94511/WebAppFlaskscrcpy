import { computed, inject, provide, ref, watch } from "vue";
import en from "../locales/en/ui.json";
import zhCN from "../locales/zh-CN/ui.json";
import zhTW from "../locales/zh-TW/ui.json";

const APP_CONTEXT_KEY = Symbol("app-context");

const LOCALE_STORAGE_KEY = "webapp-flaskscrcpy-locale";
const THEME_STORAGE_KEY = "webapp-flaskscrcpy-theme";
const VIEW_MODE_STORAGE_KEY = "webapp-flaskscrcpy-view-mode";

const MESSAGES = {
  en,
  "zh-CN": zhCN,
  "zh-TW": zhTW,
};
const AVAILABLE_LOCALES = Object.keys(MESSAGES);
const THEMES = ["light", "dark"];
const VIEW_MODES = ["single", "grid"];

function hasWindow() {
  return typeof window !== "undefined" && typeof document !== "undefined";
}

function readStored(key) {
  if (!hasWindow()) return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key, value) {
  if (!hasWindow()) return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* ignore quota / privacy errors */
  }
}

function detectLocale() {
  const saved = readStored(LOCALE_STORAGE_KEY);
  if (saved && AVAILABLE_LOCALES.includes(saved)) return saved;
  if (!hasWindow()) return "en";
  const browser = (
    navigator.language ||
    navigator.languages?.[0] ||
    "en"
  ).toLowerCase();
  if (
    browser.startsWith("zh-tw") ||
    browser.startsWith("zh-hk") ||
    browser.startsWith("zh-mo")
  ) {
    return "zh-TW";
  }
  if (browser.startsWith("zh")) return "zh-CN";
  return "en";
}

function detectTheme() {
  const saved = readStored(THEME_STORAGE_KEY);
  if (saved && THEMES.includes(saved)) return saved;
  if (!hasWindow()) return "dark";
  return window.matchMedia?.("(prefers-color-scheme: light)").matches
    ? "light"
    : "dark";
}

function detectViewMode() {
  const saved = readStored(VIEW_MODE_STORAGE_KEY);
  if (saved && VIEW_MODES.includes(saved)) return saved;
  return "single";
}

function getPathValue(object, path) {
  return path.split(".").reduce((acc, key) => {
    if (acc && typeof acc === "object" && key in acc) return acc[key];
    return undefined;
  }, object);
}

function interpolate(text, params = {}) {
  return String(text).replace(/\{(\w+)\}/g, (_, key) => {
    const value = params[key];
    return value === undefined || value === null ? "" : String(value);
  });
}

// Track every system-theme mql listener we've ever installed so we
// can tear them down on hot-reload (Vite preserves module state but
// blows away the Vue app; a re-mount that re-runs createAppContext
// without cleaning these up would multi-register handlers, and every
// system-theme change would fire N parallel applyTheme calls).
const _mqlDisposers = new Set();

function _disposeAllMqlHandlers() {
  for (const dispose of _mqlDisposers) {
    try { dispose(); } catch { /* ignore */ }
  }
  _mqlDisposers.clear();
}

if (typeof import.meta !== "undefined" && import.meta.hot) {
  import.meta.hot.dispose(_disposeAllMqlHandlers);
}

function createAppContext() {
  const localeRef = ref(detectLocale());
  const themeRef = ref(detectTheme());
  const themeIsExplicit = ref(readStored(THEME_STORAGE_KEY) !== null);
  const viewModeRef = ref(detectViewMode());

  function applyLocale(next) {
    const resolved = AVAILABLE_LOCALES.includes(next) ? next : "en";
    writeStored(LOCALE_STORAGE_KEY, resolved);
    if (hasWindow()) {
      document.documentElement.lang = resolved;
      document.documentElement.setAttribute("lang", resolved);
      // Browser tab title — was hardcoded "Scrcpy Control Panel" in
      // index.html. Re-sync from the locale bundle so switching language
      // updates the tab too. We read the bundle directly here (instead
      // of via t()) because applyLocale also runs before the closure
      // around t() is captured below.
      const bundle = MESSAGES[resolved] || MESSAGES.en;
      const title = bundle?.app?.title;
      if (title) document.title = title;
    }
  }

  function applyTheme(next, { persist = true } = {}) {
    const resolved = THEMES.includes(next) ? next : "dark";
    if (persist) writeStored(THEME_STORAGE_KEY, resolved);
    if (hasWindow()) {
      document.documentElement.dataset.theme = resolved;
      document.documentElement.style.colorScheme = resolved;
    }
  }

  applyLocale(localeRef.value);
  applyTheme(themeRef.value, { persist: false });

  if (hasWindow() && window.matchMedia) {
    const mql = window.matchMedia("(prefers-color-scheme: light)");
    const handler = (event) => {
      // Re-check storage every time rather than caching at construction:
      // the user could clear ``THEME_STORAGE_KEY`` from devtools and we
      // should then resume tracking the system theme. Pre-fix
      // ``themeIsExplicit`` was captured at construction and never
      // refreshed.
      if (readStored(THEME_STORAGE_KEY) !== null) return;
      const next = event.matches ? "light" : "dark";
      themeRef.value = next;
      applyTheme(next, { persist: false });
    };
    mql.addEventListener?.("change", handler);
    _mqlDisposers.add(() => mql.removeEventListener?.("change", handler));
  }

  const locale = computed({
    get: () => localeRef.value,
    set: (value) => {
      const resolved = AVAILABLE_LOCALES.includes(value) ? value : "en";
      localeRef.value = resolved;
      applyLocale(resolved);
    },
  });

  const theme = computed({
    get: () => themeRef.value,
    set: (value) => {
      const resolved = THEMES.includes(value) ? value : "dark";
      themeRef.value = resolved;
      themeIsExplicit.value = true;
      applyTheme(resolved);
    },
  });

  function setLocale(next) {
    locale.value = next;
  }

  function setTheme(next) {
    theme.value = next;
  }

  function toggleTheme() {
    setTheme(themeRef.value === "dark" ? "light" : "dark");
  }

  const viewMode = computed({
    get: () => viewModeRef.value,
    set: (value) => {
      const resolved = VIEW_MODES.includes(value) ? value : "single";
      viewModeRef.value = resolved;
      writeStored(VIEW_MODE_STORAGE_KEY, resolved);
    },
  });

  function setViewMode(next) {
    viewMode.value = next;
  }

  function toggleViewMode() {
    setViewMode(viewModeRef.value === "single" ? "grid" : "single");
  }

  function t(path, params = {}) {
    const current = MESSAGES[localeRef.value] || MESSAGES.en;
    const fallback =
      getPathValue(current, path) ?? getPathValue(MESSAGES.en, path) ?? path;
    if (typeof fallback === "string") return interpolate(fallback, params);
    return fallback;
  }

  return {
    locale,
    availableLocales: AVAILABLE_LOCALES,
    setLocale,
    t,
    theme,
    themes: THEMES,
    setTheme,
    toggleTheme,
    viewMode,
    viewModes: VIEW_MODES,
    setViewMode,
    toggleViewMode,
  };
}

let fallbackContext = null;
let _fallbackWarned = false;
function getFallbackContext() {
  if (!fallbackContext) {
    fallbackContext = createAppContext();
    // The fallback path is a defensive safety net for components
    // that try to inject before ``provideAppContext()`` ran at the
    // root. In correct usage we never hit this; warn once in dev so
    // the misuse surfaces instead of silently creating a parallel
    // context with its own mql listener + divergent reactive state.
    if (!_fallbackWarned) {
      _fallbackWarned = true;
      const isDev = typeof import.meta !== "undefined" && import.meta.env?.DEV;
      if (isDev && typeof console !== "undefined") {
        console.warn(
          "[useAppContext] called before provideAppContext() — falling back " +
            "to a module-scoped context. Ensure App.vue calls provideAppContext() " +
            "at root setup.",
        );
      }
    }
  }
  return fallbackContext;
}

export function provideAppContext() {
  const ctx = createAppContext();
  provide(APP_CONTEXT_KEY, ctx);
  return ctx;
}

export function useAppContext() {
  return inject(APP_CONTEXT_KEY, null) ?? getFallbackContext();
}
