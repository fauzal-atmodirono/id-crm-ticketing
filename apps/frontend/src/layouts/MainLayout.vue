<script setup lang="ts">
import AppHeader from '@/components/layout/AppHeader.vue';
</script>

<template>
  <AppHeader />
  <main>
    <slot />
  </main>
</template>

<style scoped>
main {
  flex: 1;
  width: 100%;
  padding: var(--space-lg);
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}
</style>
