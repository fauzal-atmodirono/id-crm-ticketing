<script setup lang="ts">
import type { ProductCard } from '@/features/chat/types';

defineProps<{ items: ProductCard[] }>();
</script>

<template>
  <div class="carousel" role="list">
    <article v-for="(item, i) in items" :key="i" class="card" role="listitem">
      <img
        v-if="item.image_url"
        :src="item.image_url"
        :alt="item.title"
        class="card-img"
        loading="lazy"
      />
      <div class="card-body">
        <h4 class="card-title">{{ item.title }}</h4>
        <p v-if="item.price" class="card-price">{{ item.price }}</p>
        <p class="card-desc">{{ item.description }}</p>
        <a
          v-if="item.url"
          :href="item.url"
          target="_blank"
          rel="noopener noreferrer"
          class="card-link"
        >Learn more</a>
      </div>
    </article>
  </div>
</template>

<style scoped>
.carousel {
  display: flex;
  gap: var(--space-md);
  overflow-x: auto;
  padding: var(--space-sm) 0;
  scroll-snap-type: x mandatory;
}
.card {
  flex: 0 0 220px;
  scroll-snap-align: start;
  background: var(--surface-elevated, var(--surface));
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.card-img {
  width: 100%;
  height: 130px;
  object-fit: cover;
  background: var(--bg);
}
.card-body {
  padding: var(--space-sm);
  display: flex;
  flex-direction: column;
  gap: var(--space-xs);
}
.card-title {
  margin: 0;
  font-size: 0.95rem;
  color: var(--text);
}
.card-price {
  margin: 0;
  color: var(--accent);
  font-weight: 600;
  font-size: 0.85rem;
}
.card-desc {
  margin: 0;
  font-size: 0.8rem;
  color: var(--text-muted);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.card-link {
  margin-top: auto;
  font-size: 0.8rem;
  color: var(--assistant);
  text-decoration: underline;
}
.card-link:hover { text-decoration: none; }
</style>
