<template>
  <div class="admin-scrim" @click.self="$emit('close')">
    <div class="admin-modal">
      <header class="admin-header">
        <h2>{{ t("admin.title") }}</h2>
        <button class="admin-close" @click="$emit('close')">×</button>
      </header>

      <nav class="admin-tabs">
        <button :class="{ active: tab === 'users' }" @click="tab = 'users'">
          {{ t("admin.users") }}
        </button>
        <button :class="{ active: tab === 'devices' }" @click="tab = 'devices'">
          {{ t("admin.devices") }}
        </button>
      </nav>

      <section v-if="tab === 'users'" class="admin-body">
        <p v-if="error" class="admin-error">{{ error }}</p>

        <form class="admin-create" @submit.prevent="createUser">
          <label class="admin-field">
            <span>{{ t("auth.username") }}</span>
            <input v-model.trim="newUser.username" autocomplete="off" />
          </label>
          <label class="admin-field">
            <span>{{ t("auth.email") }}</span>
            <input v-model.trim="newUser.email" type="email" autocomplete="off" />
          </label>
          <label class="admin-field">
            <span>{{ t("auth.password") }}</span>
            <input
              v-model="newUser.password"
              type="password"
              autocomplete="new-password"
            />
          </label>
          <label v-if="isSuperAdmin" class="admin-field">
            <span>{{ t("admin.role") }}</span>
            <select v-model="newUser.role">
              <option value="user">{{ t("admin.role_user") }}</option>
              <option value="admin">{{ t("admin.role_admin") }}</option>
              <option value="super_admin">
                {{ t("admin.role_super_admin") }}
              </option>
            </select>
          </label>
          <button
            class="admin-btn primary admin-create-submit"
            type="submit"
            :disabled="busy"
          >
            {{ t("admin.createUser") }}
          </button>
        </form>

        <table class="admin-table">
          <thead>
            <tr>
              <th>{{ t("auth.username") }}</th>
              <th>{{ t("auth.email") }}</th>
              <th>{{ t("admin.role") }}</th>
              <th>{{ t("admin.status") }}</th>
              <th>{{ t("admin.actions") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="u in users" :key="u.id">
              <td>{{ u.username }}</td>
              <td>{{ u.email }}</td>
              <td>{{ t("admin.role_" + u.role) }}</td>
              <td>
                <span :class="['admin-pill', u.is_active ? 'on' : 'off']">
                  {{ u.is_active ? t("admin.active") : t("admin.disabled") }}
                </span>
              </td>
              <td class="admin-row-actions">
                <select
                  v-if="isSuperAdmin && u.role !== 'super_admin'"
                  class="admin-role-select"
                  :value="u.role"
                  @change="changeRole(u, $event.target.value)"
                >
                  <option value="user">{{ t("admin.role_user") }}</option>
                  <option value="admin">{{ t("admin.role_admin") }}</option>
                  <option value="super_admin">
                    {{ t("admin.role_super_admin") }}
                  </option>
                </select>
                <button
                  v-if="u.role !== 'super_admin'"
                  class="admin-btn"
                  @click="editEmail(u)"
                >
                  {{ t("admin.editEmail") }}
                </button>
                <button
                  v-if="u.role !== 'super_admin'"
                  class="admin-btn"
                  @click="resetPassword(u)"
                >
                  {{ t("admin.resetPassword") }}
                </button>
                <button
                  v-if="u.role !== 'super_admin'"
                  class="admin-btn"
                  @click="toggleActive(u)"
                >
                  {{ u.is_active ? t("admin.disable") : t("admin.enable") }}
                </button>
                <button
                  v-if="u.role !== 'super_admin'"
                  class="admin-btn danger"
                  @click="removeUser(u)"
                >
                  {{ t("admin.delete") }}
                </button>
              </td>
            </tr>
            <tr v-if="!users.length">
              <td colspan="5" class="admin-empty">{{ t("admin.noUsers") }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-else class="admin-body">
        <p v-if="error" class="admin-error">{{ error }}</p>
        <table class="admin-table">
          <thead>
            <tr>
              <th>{{ t("admin.device") }}</th>
              <th>{{ t("admin.holder") }}</th>
              <th>{{ t("admin.expiresAt") }}</th>
              <th>{{ t("admin.actions") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in activeReservations" :key="r.device_id">
              <td>{{ r.device_id }}</td>
              <td>{{ r.username }}</td>
              <td>{{ r.expires_at }}</td>
              <td>
                <button class="admin-btn danger" @click="forceRelease(r)">
                  {{ t("reservation.forceRelease") }}
                </button>
              </td>
            </tr>
            <tr v-if="!activeReservations.length">
              <td colspan="4" class="admin-empty">
                {{ t("admin.noReservations") }}
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>

    <!-- In-app prompt / confirm dialog (replaces window.prompt/confirm). -->
    <div
      v-if="dialog.open"
      class="admin-dialog-scrim"
      @click.self="cancelDialog"
    >
      <div class="admin-dialog" role="dialog" aria-modal="true">
        <p class="admin-dialog-title">{{ dialog.title }}</p>
        <input
          v-if="dialog.mode === 'prompt'"
          ref="dialogInputRef"
          v-model="dialog.value"
          :type="dialog.password ? 'password' : 'text'"
          :placeholder="dialog.placeholder"
          class="admin-dialog-input"
          @keyup.enter="confirmDialog"
          @keyup.esc="cancelDialog"
        />
        <div class="admin-dialog-actions">
          <button class="admin-btn" @click="cancelDialog">
            {{ t("admin.dialogCancel") }}
          </button>
          <button class="admin-btn primary" @click="confirmDialog">
            {{ t("admin.dialogConfirm") }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref } from "vue";
import { useAppContext } from "../composables/useAppContext";
import { useAuth } from "../composables/useAuth";
import { useReservations } from "../composables/useReservations";
import { useValidators } from "../composables/useValidators";

defineEmits(["close"]);

const { t } = useAppContext();
const { isSuperAdmin } = useAuth();
const { reservations, fetchReservations, release } = useReservations();
const { validateUsername, validateEmail, validatePassword } = useValidators();

const tab = ref("users");
const users = ref([]);
const error = ref("");
const busy = ref(false);
const newUser = reactive({ username: "", email: "", password: "", role: "user" });

const activeReservations = computed(() => Object.values(reservations.value));

// ── In-app dialog (replaces window.prompt / window.confirm) ──────────
// One reusable dialog driven by a promise: ``askPrompt`` resolves to the
// entered string (or null on cancel); ``askConfirm`` resolves true/false.
const dialog = reactive({
  open: false,
  mode: "prompt", // "prompt" | "confirm"
  title: "",
  value: "",
  placeholder: "",
  password: false,
});
const dialogInputRef = ref(null);
let _dialogResolve = null;

function _openDialog(opts) {
  dialog.open = true;
  dialog.mode = opts.mode || "prompt";
  dialog.title = opts.title || "";
  dialog.value = opts.value || "";
  dialog.placeholder = opts.placeholder || "";
  dialog.password = !!opts.password;
  return new Promise((resolve) => {
    _dialogResolve = resolve;
    if (dialog.mode === "prompt") {
      nextTick(() => {
        const el = dialogInputRef.value;
        el?.focus();
        el?.select?.();
      });
    }
  });
}
function _closeDialog(result) {
  dialog.open = false;
  const resolve = _dialogResolve;
  _dialogResolve = null;
  if (resolve) resolve(result);
}
function confirmDialog() {
  _closeDialog(dialog.mode === "prompt" ? dialog.value : true);
}
function cancelDialog() {
  _closeDialog(dialog.mode === "prompt" ? null : false);
}
function askPrompt(opts) {
  return _openDialog({ ...opts, mode: "prompt" });
}
function askConfirm(title) {
  return _openDialog({ mode: "confirm", title });
}

async function putUser(id, body) {
  const r = await fetch(`/api/auth/users/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await r.json().catch(() => ({}));
  if (!r.ok || payload.status !== "ok") {
    throw new Error(payload.error || t("admin.actionFailed"));
  }
}

async function createUser() {
  error.value = "";
  // Reuse the shared validators (same rules as the backend) for instant feedback.
  const formErr =
    validateUsername(newUser.username) ||
    validateEmail(newUser.email) ||
    validatePassword(newUser.password);
  if (formErr) {
    error.value = formErr;
    return;
  }
  busy.value = true;
  try {
    const r = await fetch("/api/auth/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...newUser }),
    });
    const payload = await r.json().catch(() => ({}));
    if (!r.ok || payload.status !== "ok") {
      throw new Error(payload.error || t("admin.actionFailed"));
    }
    newUser.username = "";
    newUser.email = "";
    newUser.password = "";
    newUser.role = "user";
    await loadUsers();
  } catch (err) {
    error.value = err?.message || t("admin.actionFailed");
  } finally {
    busy.value = false;
  }
}

async function editEmail(u) {
  const email = await askPrompt({
    title: t("admin.newEmailPrompt"),
    value: u.email,
  });
  if (!email || email === u.email) return;
  error.value = "";
  try {
    await putUser(u.id, { email });
    await loadUsers();
  } catch (err) {
    error.value = err?.message || t("admin.actionFailed");
  }
}

async function resetPassword(u) {
  const password = await askPrompt({
    title: t("admin.newPasswordPrompt", { user: u.username }),
    password: true,
  });
  if (!password) return;
  error.value = "";
  try {
    await putUser(u.id, { password });
    error.value = "";
  } catch (err) {
    error.value = err?.message || t("admin.actionFailed");
  }
}

async function changeRole(u, role) {
  if (role === u.role) return;
  error.value = "";
  try {
    await putUser(u.id, { role });
    await loadUsers();
  } catch (err) {
    error.value = err?.message || t("admin.actionFailed");
    await loadUsers(); // revert the select to the server truth
  }
}

async function loadUsers() {
  error.value = "";
  try {
    const r = await fetch("/api/auth/users");
    const payload = await r.json().catch(() => ({}));
    if (!r.ok || payload.status !== "ok") {
      throw new Error(payload.error || t("admin.loadFailed"));
    }
    users.value = payload.users || [];
  } catch (err) {
    error.value = err?.message || t("admin.loadFailed");
  }
}

async function toggleActive(u) {
  error.value = "";
  try {
    const r = await fetch(`/api/auth/users/${u.id}/active`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_active: !u.is_active }),
    });
    const payload = await r.json().catch(() => ({}));
    if (!r.ok || payload.status !== "ok") {
      throw new Error(payload.error || t("admin.actionFailed"));
    }
    await loadUsers();
  } catch (err) {
    error.value = err?.message || t("admin.actionFailed");
  }
}

async function removeUser(u) {
  error.value = "";
  const ok = await askConfirm(t("admin.confirmDelete", { user: u.username }));
  if (!ok) return;
  try {
    const r = await fetch(`/api/auth/users/${u.id}`, { method: "DELETE" });
    const payload = await r.json().catch(() => ({}));
    if (!r.ok || payload.status !== "ok") {
      throw new Error(payload.error || t("admin.actionFailed"));
    }
    await loadUsers();
  } catch (err) {
    error.value = err?.message || t("admin.actionFailed");
  }
}

async function forceRelease(r) {
  error.value = "";
  try {
    await release(r.device_id);
  } catch (err) {
    error.value = err?.message || t("admin.actionFailed");
  }
}

onMounted(() => {
  loadUsers();
  fetchReservations();
});
</script>

<style scoped>
.admin-scrim {
  position: fixed;
  inset: 0;
  background: var(--overlay-strong);
  display: grid;
  place-items: center;
  z-index: 1000;
  padding: 24px;
}
.admin-modal {
  width: min(1040px, 100%);
  max-height: 86vh;
  display: flex;
  flex-direction: column;
  background: var(--panel-bg);
  border: 1px solid var(--panel-border);
  border-radius: 14px;
  box-shadow: var(--shadow);
  overflow: hidden;
}
.admin-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border-soft);
}
.admin-header h2 {
  margin: 0;
  font-size: 1.05rem;
  color: var(--text-strong);
}
.admin-close {
  border: none;
  background: transparent;
  color: var(--muted);
  font-size: 1.5rem;
  line-height: 1;
  cursor: pointer;
}
.admin-tabs {
  display: flex;
  gap: 6px;
  padding: 12px 20px 0;
}
.admin-tabs button {
  padding: 8px 14px;
  border: 1px solid var(--border);
  border-bottom: none;
  border-radius: 8px 8px 0 0;
  background: var(--surface-input);
  color: var(--muted);
  cursor: pointer;
  font-weight: 600;
  font-size: 0.85rem;
}
.admin-tabs button.active {
  background: var(--primary);
  color: var(--text-on-primary);
  border-color: var(--primary-border);
}
.admin-body {
  padding: 16px 20px 20px;
  overflow: auto;
}
.admin-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}
.admin-table th,
.admin-table td {
  text-align: left;
  padding: 10px;
  border-bottom: 1px solid var(--border-soft);
  color: var(--text);
  vertical-align: middle;
}
.admin-table th {
  color: var(--muted);
  font-weight: 600;
  white-space: nowrap;
}
/* Role + status columns stay on one line (fixes the "启\n用" wrap). */
.admin-table td:nth-child(3),
.admin-table td:nth-child(4) {
  white-space: nowrap;
}
.admin-row-actions {
  display: flex;
  gap: 6px 8px; /* row-gap keeps a tidy 2-line fallback on narrow screens */
  flex-wrap: wrap;
  align-items: center;
}
/* Reserve enough width for the action controls so the wide modal keeps the
   role-select + buttons on a single line (the source of the ragged look). */
.admin-table td.admin-row-actions {
  min-width: 540px;
}
/* Keep email + role on one line so rows don't grow uneven heights. */
.admin-table td:nth-child(2) {
  white-space: nowrap;
}

/* Create-user form: a tidy labeled card instead of a cramped input row. */
.admin-create {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px;
  margin-bottom: 18px;
  padding: 14px;
  background: var(--surface-input);
  border: 1px solid var(--border-soft);
  border-radius: 12px;
}
.admin-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  flex: 1 1 150px;
  min-width: 0;
  font-size: 0.74rem;
  color: var(--muted);
}
.admin-create input,
.admin-create select {
  width: 100%;
  height: 34px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel-bg);
  color: var(--text);
  font-size: 0.85rem;
}
.admin-create-submit {
  flex: 0 0 auto;
}

/* Compact inline role select inside a table row. */
.admin-role-select {
  height: 30px;
  max-width: 132px;
  padding: 0 8px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: var(--surface-input);
  color: var(--text);
  font-size: 0.78rem;
}

.admin-btn {
  height: 32px;
  padding: 0 12px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--surface-input);
  color: var(--text);
  cursor: pointer;
  font-size: 0.78rem;
  white-space: nowrap;
}
.admin-btn:hover {
  border-color: var(--primary);
}
.admin-btn.primary {
  background: var(--primary);
  color: var(--text-on-primary);
  border-color: var(--primary-border);
}
.admin-btn.danger {
  background: var(--danger-bg);
  color: var(--danger-fg);
  border-color: var(--danger-border);
}
.admin-pill {
  display: inline-block;
  white-space: nowrap;
  font-size: 0.72rem;
  padding: 3px 9px;
  border-radius: 999px;
}

/* In-app prompt/confirm dialog — replaces the native browser dialogs. */
.admin-dialog-scrim {
  position: fixed;
  inset: 0;
  background: var(--overlay-strong);
  display: grid;
  place-items: center;
  z-index: 1100; /* above the admin modal (1000) */
  padding: 24px;
}
.admin-dialog {
  width: min(420px, 100%);
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 20px;
  background: var(--panel-bg);
  border: 1px solid var(--panel-border);
  border-radius: 14px;
  box-shadow: var(--shadow);
}
.admin-dialog-title {
  margin: 0;
  font-size: 0.92rem;
  font-weight: 600;
  color: var(--text-strong);
}
.admin-dialog-input {
  width: 100%;
  height: 38px;
  padding: 0 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-input);
  color: var(--text);
  font-size: 0.9rem;
}
.admin-dialog-input:focus {
  outline: none;
  border-color: var(--primary-border);
  box-shadow: 0 0 0 3px var(--primary-ring);
}
.admin-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.admin-pill.on {
  background: var(--ok-bg);
  color: var(--ok-fg);
}
.admin-pill.off {
  background: var(--danger-bg);
  color: var(--danger-fg);
}
.admin-empty {
  text-align: center;
  color: var(--muted);
  padding: 18px;
}
.admin-error {
  margin: 0 0 12px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--danger-bg);
  color: var(--danger-fg);
  font-size: 0.82rem;
}
</style>
