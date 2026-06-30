<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { fetchHealth, type HealthCheck } from '@/plugins/api';

const health = ref<HealthCheck | null>(null);
const error = ref<string | null>(null);

onMounted(async () => {
  try {
    health.value = await fetchHealth();
  } catch (e) {
    error.value = e instanceof Error ? e.message : 'Backend unreachable';
  }
});
</script>

<template>
  <header class="app-header">
    <div class="brand">
      <h1>Proton Conversational AI</h1>
      <nav class="nav">
        <router-link to="/">Channels</router-link>
        <router-link to="/dashboard">Dashboard</router-link>
      </nav>
    </div>
    <div v-if="health" class="badges">
      <span class="badge">CRM: <strong>{{ health.crm_provider }}</strong></span>
      <span class="badge">Voice: <strong>{{ health.voice_provider }}</strong></span>
      <span class="badge">Model: <strong>{{ health.model }}</strong></span>
    </div>
    <div v-else-if="error" class="badges">
      <span class="badge err">{{ error }}</span>
    </div>
    <div v-else class="badges">
      <span class="badge">Connecting…</span>
    </div>
  </header>
</template>

<style scoped>
.app-header {
  padding: var(--space-md) var(--space-lg);
  border-bottom: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-md);
}

h1 {
  margin: 0;
  font-size: 1.05rem;
  font-weight: 600;
}

.badges {
  display: flex;
  gap: var(--space-sm);
  flex-wrap: wrap;
}

.badge {
  background: var(--surface);
  border: 1px solid var(--border);
  padding: 0.25rem 0.75rem;
  border-radius: var(--radius-full);
  font-size: 0.8rem;
  color: var(--text-muted);
}

.badge strong {
  color: var(--text);
}

.badge.err {
  color: var(--danger);
  border-color: var(--danger);
}

.brand { display: flex; align-items: center; gap: var(--space-md); }
.nav { display: flex; gap: var(--space-sm); }
.nav a {
  font-size: 0.85rem;
  color: var(--text-muted);
  text-decoration: none;
  padding: 0.25rem 0.6rem;
  border-radius: var(--radius-full);
}
.nav a.router-link-active { color: var(--text); background: var(--surface); }
</style>
