<script setup lang="ts">
/** 三栏骨架：左侧会话列表 + 右侧聊天区。
 *  布局样式复用 styles/main.css 里已有的 .app-shell / .sidebar / .chat-panel。 */
</script>

<template>
  <main class="app-shell">
    <aside class="sidebar">
      <slot name="sidebar" />
    </aside>
    <section class="chat-panel">
      <slot />
    </section>
  </main>
</template>
