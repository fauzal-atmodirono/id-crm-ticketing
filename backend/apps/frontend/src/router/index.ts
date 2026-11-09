import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router';
import HomeView from '@/views/HomeView.vue';
import DashboardView from '@/views/DashboardView.vue';

const routes: RouteRecordRaw[] = [
  { path: '/', name: 'home', component: HomeView },
  { path: '/dashboard', name: 'dashboard', component: DashboardView },
];

export const router = createRouter({
  // BASE_URL follows vite's --base, so the app also works under a sub-path
  // (the VM test console is served at /apps/test-console/).
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
});
