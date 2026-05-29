<template>
  <div v-if="!ready" class="auth-boot">
    <div class="auth-boot-spinner" />
  </div>
  <LoginView v-else-if="!user" />
  <App v-else />
</template>

<script setup>
import { onMounted } from "vue";
import App from "./App.vue";
import LoginView from "./components/LoginView.vue";
import { provideAppContext } from "./composables/useAppContext";
import { useAuth } from "./composables/useAuth";

// Provide the shared app context (i18n / theme / view-mode) at the true root
// so both the login screen and the authenticated app inject the same one.
provideAppContext();

const { ready, user, checkAuth } = useAuth();

onMounted(() => {
  checkAuth();
});
</script>

<style scoped>
.auth-boot {
  min-height: 100vh;
  display: grid;
  place-items: center;
  background: var(--app-bg);
}
.auth-boot-spinner {
  width: 36px;
  height: 36px;
  border: 3px solid var(--border);
  border-top-color: var(--primary);
  border-radius: 50%;
  animation: auth-spin 0.8s linear infinite;
}
@keyframes auth-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
