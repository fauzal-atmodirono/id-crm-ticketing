import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router';
import HomeView from '@/views/HomeView.vue';
import DashboardView from '@/views/DashboardView.vue';

const routes: RouteRecordRaw[] = [
  { path: '/', name: 'home', component: HomeView },
  { path: '/dashboard', name: 'dashboard', component: DashboardView },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
});
