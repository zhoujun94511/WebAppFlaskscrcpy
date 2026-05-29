import { DEFAULT_KEY_BINDINGS } from "./keycodes";

const STORAGE_KEY = "webapp-flaskscrcpy-keybindings-v2";
const EXPORT_VERSION = 1;

const EDITABLE_GROUPS = new Set([
  "navigation",
  "system",
  "dpad",
  "typing",
  "clipboard",
  "custom",
]);

function createId() {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return crypto.randomUUID();
  }
  return `binding-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function normalizeBinding(binding) {
  return {
    id: binding?.id || createId(),
    label: String(binding?.label || "").trim() || "Unnamed",
    group: EDITABLE_GROUPS.has(binding?.group) ? binding.group : "custom",
    keycode: Number(binding?.keycode) || 3,
  };
}

export function cloneDefaultBindings() {
  return DEFAULT_KEY_BINDINGS.map((item) => ({ ...item }));
}

export function sanitizeBindings(input) {
  if (!Array.isArray(input)) {
    return cloneDefaultBindings();
  }
  return input.map((binding) => normalizeBinding(binding));
}

export function loadBindings() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return cloneDefaultBindings();
    }
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      return sanitizeBindings(parsed);
    }
    if (parsed && Array.isArray(parsed.bindings)) {
      return sanitizeBindings(parsed.bindings);
    }
  } catch {
    // fall through to defaults
  }
  return cloneDefaultBindings();
}

export function persistBindings(bindings) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(sanitizeBindings(bindings)));
}

export function exportBindings(bindings) {
  return JSON.stringify(
    {
      version: EXPORT_VERSION,
      exportedAt: new Date().toISOString(),
      bindings: sanitizeBindings(bindings),
    },
    null,
    2,
  );
}

export function importBindingsFromText(text) {
  const parsed = JSON.parse(text);
  if (Array.isArray(parsed)) {
    return sanitizeBindings(parsed);
  }
  if (parsed && Array.isArray(parsed.bindings)) {
    return sanitizeBindings(parsed.bindings);
  }
  throw new Error("Invalid binding configuration");
}
