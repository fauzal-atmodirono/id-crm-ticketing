<script setup lang="ts">
import { ref } from 'vue';
import { postCsat } from '@/features/chat/api/chat.api';

const props = defineProps<{ sessionId: string }>();
const emit = defineEmits<{ (e: 'done', score: number): void }>();

const submitting = ref(false);
const thanks = ref<string | null>(null);
const error = ref<string | null>(null);

async function rate(score: number): Promise<void> {
  if (submitting.value || thanks.value) return;
  submitting.value = true;
  error.value = null;
  try {
    const res = await postCsat(props.sessionId, score);
    thanks.value = res.message;
    emit('done', score);
  } catch (e) {
    error.value = e instanceof Error ? e.message : 'Failed to submit rating';
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="csat">
    <template v-if="thanks">
      <p class="csat__thanks">{{ thanks }}</p>
    </template>
    <template v-else>
      <p class="csat__prompt">How would you rate your experience?</p>
      <div class="csat__scale">
        <button
          v-for="n in 5"
          :key="n"
          type="button"
          class="csat__btn"
          :disabled="submitting"
          @click="rate(n)"
        >
          {{ n }}
        </button>
      </div>
      <p v-if="error" class="csat__error">{{ error }}</p>
    </template>
  </div>
</template>

<style scoped>
.csat { padding: 0.75rem; border-radius: 0.5rem; background: #f4f4f5; }
.csat__prompt { margin: 0 0 0.5rem; font-size: 0.9rem; }
.csat__scale { display: flex; gap: 0.5rem; }
.csat__btn {
  width: 2.25rem; height: 2.25rem; border-radius: 0.5rem;
  border: 1px solid #d4d4d8; background: #fff; cursor: pointer; font-weight: 600;
}
.csat__btn:disabled { opacity: 0.5; cursor: default; }
.csat__error { color: #b91c1c; font-size: 0.8rem; margin: 0.5rem 0 0; }
.csat__thanks { margin: 0; font-size: 0.9rem; }
</style>
