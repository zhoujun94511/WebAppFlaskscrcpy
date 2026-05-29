import { computed, reactive } from "vue";

// Module-scoped singleton auth state. Every component that calls useAuth()
// shares the same reactive store, so a login in LoginView immediately flips
// the whole app over to the authenticated tree.
const state = reactive({
  ready: false, // has the initial /check-auth round-trip completed?
  user: null, // { id, username, email, role } or null
});

async function postJson(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const payload = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, payload };
}

async function checkAuth() {
  try {
    const r = await fetch("/api/auth/check-auth");
    const payload = await r.json().catch(() => ({}));
    state.user = payload.authenticated ? payload.user : null;
  } catch {
    state.user = null;
  } finally {
    state.ready = true;
  }
  return state.user;
}

async function login(username, password) {
  const { ok, payload } = await postJson("/api/auth/login", {
    username,
    password,
  });
  if (!ok) {
    // Surface the server's (localized) error verbatim; when absent, throw an
    // empty message so the calling component falls back to its i18n string.
    throw new Error(payload.error || "");
  }
  state.user = payload.user;
  return state.user;
}

async function register(username, email, password) {
  const { ok, payload } = await postJson("/api/auth/register", {
    username,
    email,
    password,
  });
  if (!ok) {
    throw new Error(payload.error || "");
  }
  return payload;
}

async function logout() {
  try {
    await postJson("/api/auth/logout", {});
  } catch {
    /* best-effort; clear local state regardless */
  }
  state.user = null;
}

export function useAuth() {
  const isAdmin = computed(() =>
    ["admin", "super_admin"].includes(state.user?.role),
  );
  const isSuperAdmin = computed(() => state.user?.role === "super_admin");
  return {
    state,
    user: computed(() => state.user),
    ready: computed(() => state.ready),
    isAdmin,
    isSuperAdmin,
    checkAuth,
    login,
    register,
    logout,
  };
}
