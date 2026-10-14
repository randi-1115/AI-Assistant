import { createRouter, createWebHistory } from 'vue-router'

import ChatView from '../views/ChatView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'chat', component: ChatView },
    // P1 会加记忆管理页与设置页。
  ],
})
