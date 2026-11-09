<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  fetchDemoStatus,
  resetDemo,
  seedDemo,
  type DemoStatus,
} from '../api/demo.api'

const status = ref<DemoStatus | null>(null)
const busy = ref<'' | 'load' | 'seed' | 'reset'>('')
const error = ref('')
const message = ref('')

async function load(): Promise<void> {
  busy.value = 'load'
  error.value = ''
  try {
    status.value = await fetchDemoStatus()
  } catch (e) {
    error.value = `Could not load the demo personas (${(e as Error).message}).`
  } finally {
    busy.value = ''
  }
}

async function seed(): Promise<void> {
  busy.value = 'seed'
  error.value = ''
  message.value = ''
  try {
    const r = await seedDemo()
    message.value =
      `Seeded: ${r.created_contacts.length} new contact(s), ${r.created_conversations} conversation(s), ` +
      `${r.rsa_created} RSA incident(s)` +
      (r.skipped_history.length ? `; already there: ${r.skipped_history.join(', ')}` : '') +
      (r.conflicts.length ? `; skipped (number used by a real contact): ${r.conflicts.map((c) => c.persona).join(', ')}` : '') +
      (r.errors.length ? `; notes: ${r.errors.join(' | ')}` : '')
  } catch (e) {
    error.value = `Seed failed (${(e as Error).message}).`
  } finally {
    busy.value = ''
    await load()
  }
}

async function reset(): Promise<void> {
  if (!window.confirm('Remove every [DEMO] record (demo_persona label / "[DEMO]" name) and seed again?')) return
  busy.value = 'reset'
  error.value = ''
  message.value = ''
  try {
    const r = await resetDemo()
    message.value =
      `Reset: removed ${r.removed.contacts} contact(s), ${r.removed.conversations} conversation(s), ` +
      `${r.removed.rsa_incidents} RSA incident(s); re-seeded ${r.seed.created_contacts.length} contact(s)` +
      (r.errors.length ? `; ${r.errors.length} issue(s): ${r.errors.join(' | ')}` : '')
  } catch (e) {
    error.value = `Reset failed (${(e as Error).message}).`
  } finally {
    busy.value = ''
    await load()
  }
}

onMounted(load)
</script>

<template>
  <section class="demo">
    <header class="demo__head">
      <div>
        <h2 class="demo__title">Demo personas</h2>
        <p class="demo__sub">
          [DEMO] contacts and history for the Phone tab's "Call as" personas.
          <template v-if="status">
            Account {{ status.target.account_id }}, inbox {{ status.target.inbox_id }}
            <span :class="status.inbox.ok ? 'ok' : 'bad'">({{ status.inbox.channel_type ?? 'unknown' }})</span>
          </template>
        </p>
      </div>
      <div class="demo__actions">
        <button type="button" class="btn" :disabled="busy !== ''" @click="seed">
          {{ busy === 'seed' ? 'Seeding…' : 'Seed' }}
        </button>
        <button type="button" class="btn btn--warn" :disabled="busy !== ''" @click="reset">
          {{ busy === 'reset' ? 'Resetting…' : 'Reset' }}
        </button>
        <button type="button" class="btn btn--ghost" :disabled="busy !== ''" @click="load">Refresh</button>
      </div>
    </header>

    <p v-if="message" class="note">{{ message }}</p>
    <p v-if="error" class="note note--error">{{ error }}</p>
    <p v-if="status && !status.rsa_configured" class="note">
      RSA store is not configured on this tenant: Farid's past RSA incident is not seeded.
    </p>

    <div v-if="status" class="grid">
      <article v-for="p in status.personas" :key="p.id" class="card" :data-seeded="p.seeded">
        <div class="card__top">
          <strong>{{ p.label }}</strong>
          <span v-if="p.seedable" class="chip" :class="p.seeded ? 'chip--ok' : 'chip--off'">
            {{ p.seeded ? 'seeded' : 'not seeded' }}
          </span>
          <span v-else class="chip">no CRM record</span>
        </div>
        <div class="card__meta">
          <code>{{ p.number || 'no caller ID' }}</code>
          <span v-if="p.language"> · {{ p.language }}</span>
        </div>
        <p class="card__desc">{{ p.description }}</p>
        <ul class="card__shows">
          <li v-for="line in p.shows" :key="line">{{ line }}</li>
        </ul>
        <p class="card__hint"><em>Script:</em> {{ p.scriptHint }}</p>
        <p v-if="p.seeded" class="card__meta">
          {{ p.conversation_count }} conversation(s)<span v-if="p.rsa_incidents"> · {{ p.rsa_incidents }} RSA incident(s)</span>
        </p>
        <p v-if="p.warning" class="note note--warn">{{ p.warning }}</p>
        <nav class="card__links">
          <a v-if="p.contact" :href="p.contact.url" target="_blank" rel="noopener">Contact</a>
          <a v-if="p.latest_conversation" :href="p.latest_conversation.url" target="_blank" rel="noopener">
            Latest conversation ({{ p.latest_conversation.status }})
          </a>
          <a v-if="p.customer360_url" :href="p.customer360_url" target="_blank" rel="noopener">Customer 360</a>
        </nav>
      </article>
    </div>
    <p v-else-if="busy === 'load'" class="note">Loading…</p>
  </section>
</template>

<style scoped>
.demo {
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}
.demo__head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--space-md);
  flex-wrap: wrap;
}
.demo__title {
  margin: 0;
  font-size: 1rem;
  font-weight: 650;
}
.demo__sub {
  margin: 0.2rem 0 0;
  font-size: 0.8rem;
  color: var(--text-muted);
}
.ok {
  color: var(--success);
}
.bad {
  color: var(--danger);
}
.demo__actions {
  display: flex;
  gap: var(--space-sm);
}
.btn {
  background: var(--accent);
  border: 1px solid var(--accent);
  color: var(--bg);
  font-weight: 600;
  font-size: 0.85rem;
  padding: 0.4rem 0.9rem;
  border-radius: var(--radius-md);
}
.btn--warn {
  background: transparent;
  color: var(--danger);
  border-color: var(--danger);
}
.btn--ghost {
  background: transparent;
  color: var(--text-muted);
  border-color: var(--border);
}
.btn:disabled {
  opacity: 0.5;
  cursor: wait;
}
.note {
  margin: 0;
  font-size: 0.8rem;
  color: var(--text-muted);
  overflow-wrap: anywhere;
}
.note--error {
  color: var(--danger);
}
.note--warn {
  color: #fbbf24;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: var(--space-md);
}
.card {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  padding: var(--space-md);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  font-size: 0.82rem;
}
.card[data-seeded='true'] {
  border-color: rgba(74, 222, 128, 0.45);
}
.card__top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-sm);
}
.card__meta {
  margin: 0;
  color: var(--text-muted);
}
.card__desc,
.card__hint {
  margin: 0;
}
.card__hint {
  color: var(--text-muted);
}
.card__shows {
  margin: 0;
  padding-left: 1.1rem;
}
.card__links {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  margin-top: auto;
}
.card__links a {
  color: var(--accent);
  text-decoration: none;
}
.card__links a:hover {
  text-decoration: underline;
}
.chip {
  font-size: 0.68rem;
  padding: 0.05rem 0.5rem;
  border-radius: var(--radius-full);
  border: 1px solid var(--border);
  color: var(--text-muted);
  white-space: nowrap;
}
.chip--ok {
  color: var(--success);
  border-color: rgba(74, 222, 128, 0.45);
}
.chip--off {
  color: var(--text-muted);
}
code {
  font-family: var(--font-mono);
  background: var(--bg);
  padding: 0.05rem 0.35rem;
  border-radius: var(--radius-sm);
}
</style>
