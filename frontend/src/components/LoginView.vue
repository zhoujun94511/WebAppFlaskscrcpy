<template>
  <div class="auth-screen">
    <div class="auth-toolbar">
      <LanguageSwitcher />
      <ThemeToggle />
    </div>

    <div class="auth-card">
      <div class="auth-brand">
        <div class="auth-brand-lockup">
          <img class="auth-logo" src="/logo.svg" alt="" width="40" height="40" />
          <h1>WebAppFlaskScrcpy</h1>
        </div>
        <p>{{ t("app.subtitle") }}</p>
      </div>

      <div class="auth-tabs">
        <button
          :class="{ active: mode === 'login' }"
          @click="switchMode('login')"
        >
          {{ t("auth.login") }}
        </button>
        <button
          :class="{ active: mode === 'register' }"
          @click="switchMode('register')"
        >
          {{ t("auth.register") }}
        </button>
      </div>

      <!-- novalidate: suppress the browser's native validation bubble (it
           animates/zooms and can't be styled). We validate in JS and show a
           static inline error instead. -->
      <form class="auth-form" novalidate @submit.prevent="onSubmit">
        <label>
          <span>{{ t("auth.username") }}</span>
          <input
            v-model.trim="username"
            type="text"
            autocomplete="username"
            :placeholder="t('auth.usernamePlaceholder')"
          />
        </label>

        <label v-if="mode === 'register'">
          <span>{{ t("auth.email") }}</span>
          <input
            v-model.trim="email"
            type="email"
            autocomplete="email"
            :placeholder="t('auth.emailPlaceholder')"
          />
        </label>

        <label>
          <span>{{ t("auth.password") }}</span>
          <input
            v-model="password"
            type="password"
            :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
            :placeholder="t('auth.passwordPlaceholder')"
          />
        </label>

        <p v-if="error" class="auth-error">{{ error }}</p>
        <button
          v-if="error && mode === 'login'"
          type="button"
          class="auth-switch-link"
          @click="switchMode('register')"
        >
          {{ t("auth.noAccountRegister") }}
        </button>
        <p v-if="notice" class="auth-notice">{{ notice }}</p>

        <button class="auth-submit" type="submit" :disabled="busy">
          <span v-if="busy" class="auth-spinner" aria-hidden="true"></span>
          <span>{{ busy ? t("auth.working") : submitLabel }}</span>
        </button>
      </form>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue";
import { useAppContext } from "../composables/useAppContext";
import { useAuth } from "../composables/useAuth";
import { useValidators } from "../composables/useValidators";
import ThemeToggle from "./ThemeToggle.vue";
import LanguageSwitcher from "./LanguageSwitcher.vue";

const { t } = useAppContext();
const { login, register } = useAuth();
const { validateUsername, validateEmail, validatePassword } = useValidators();

const mode = ref("login");
const username = ref("");
const email = ref("");
const password = ref("");
const busy = ref(false);
const error = ref("");
const notice = ref("");

const submitLabel = computed(() =>
  mode.value === "login" ? t("auth.login") : t("auth.register"),
);

function switchMode(next) {
  mode.value = next;
  error.value = "";
  notice.value = "";
}

async function onSubmit() {
  error.value = "";
  notice.value = "";
  // Custom (static) validation in place of the native bubble.
  if (!username.value.trim() || !password.value) {
    error.value = t("auth.fillAllFields");
    return;
  }
  // Registration enforces the full format/strength policy client-side for
  // instant feedback (the backend re-checks via services/validators.py).
  // Login only needs non-empty — legacy accounts shouldn't be re-judged.
  if (mode.value === "register") {
    const formErr =
      validateUsername(username.value) ||
      validateEmail(email.value) ||
      validatePassword(password.value);
    if (formErr) {
      error.value = formErr;
      return;
    }
  }
  busy.value = true;
  try {
    if (mode.value === "login") {
      await login(username.value, password.value);
      // useAuth state flips -> AppRoot swaps in the app tree automatically.
    } else {
      await register(username.value, email.value, password.value);
      notice.value = t("auth.registerSuccess");
      mode.value = "login";
      password.value = "";
    }
  } catch (err) {
    error.value = err?.message || t("auth.genericError");
  } finally {
    busy.value = false;
  }
}
</script>

<style scoped>
.auth-screen {
  position: relative;
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background: var(--app-bg);
  color: var(--text);
  overflow: hidden;
}

/* Restrained ambience: two soft brand-coloured glows behind the card.
   STATIC on purpose — an earlier version drifted them on a 16–20s loop,
   but the constant motion behind the form (and behind the browser's native
   validation bubble) was disorienting. Keep the glow, drop the movement. */
.auth-screen::before,
.auth-screen::after {
  content: "";
  position: absolute;
  border-radius: 50%;
  background: var(--primary);
  filter: blur(90px);
  opacity: 0.22;
  pointer-events: none;
  z-index: 0;
}
.auth-screen::before {
  width: 380px;
  height: 380px;
  top: -130px;
  left: -110px;
}
.auth-screen::after {
  width: 320px;
  height: 320px;
  right: -110px;
  bottom: -130px;
}

/* Top-right toolbar groups the language switcher + theme toggle (both are
   self-styled pill components). */
.auth-toolbar {
  position: absolute;
  top: 18px;
  right: 18px;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 10px;
}

.auth-card {
  position: relative;
  z-index: 1;
  width: min(420px, 100%);
  background: var(--panel-bg);
  border: 1px solid var(--panel-border);
  border-radius: 16px;
  box-shadow: var(--shadow);
  padding: 32px;
}

.auth-brand {
  text-align: center;
  margin-bottom: 24px;
}
.auth-brand-lockup {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  margin-bottom: 6px;
}
.auth-logo {
  width: 40px;
  height: 40px;
  flex: 0 0 auto;
}
.auth-brand h1 {
  font-size: 1.25rem;
  margin: 0;
  color: var(--text-strong);
}
.auth-brand p {
  margin: 0;
  font-size: 0.85rem;
  color: var(--muted);
}

/* Segmented switch — a single track with a raised "pill" for the active
   tab, so it reads as a mode toggle rather than two big primary buttons
   competing with the submit button below. */
.auth-tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 22px;
  padding: 4px;
  background: var(--surface-input);
  border: 1px solid var(--border);
  border-radius: 12px;
}
.auth-tabs button {
  flex: 1;
  padding: 8px 10px;
  border: none;
  border-radius: 9px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  font-weight: 600;
  font-size: 0.9rem;
  transition: background 0.15s ease, color 0.15s ease;
}
.auth-tabs button:hover {
  color: var(--text-strong);
}
.auth-tabs button.active {
  background: var(--panel-bg);
  color: var(--text-strong);
  box-shadow: var(--shadow-soft);
}

.auth-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.auth-form label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 0.8rem;
  color: var(--muted);
}
.auth-form input {
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-input);
  color: var(--text);
  font-size: 0.95rem;
}
.auth-form input:focus {
  outline: none;
  border-color: var(--primary);
  box-shadow: 0 0 0 3px var(--primary-ring);
}

.auth-submit {
  margin-top: 6px;
  padding: 11px;
  border: none;
  border-radius: 10px;
  background: var(--primary);
  color: var(--text-on-primary);
  font-weight: 600;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}
.auth-submit:disabled {
  opacity: 0.6;
  cursor: progress;
}
.auth-spinner {
  width: 15px;
  height: 15px;
  border: 2px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: auth-spin 0.6s linear infinite;
}
@keyframes auth-spin {
  to {
    transform: rotate(360deg);
  }
}

.auth-error {
  margin: 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--danger-bg);
  color: var(--danger-fg);
  border: 1px solid var(--danger-border);
  font-size: 0.82rem;
  text-align: center;
}
.auth-switch-link {
  align-self: center;
  margin: -4px 0 0;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--primary);
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
}
.auth-switch-link:hover {
  text-decoration: underline;
}
.auth-notice {
  margin: 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--ok-bg);
  color: var(--ok-fg);
  border: 1px solid var(--ok-border);
  font-size: 0.82rem;
}
</style>
