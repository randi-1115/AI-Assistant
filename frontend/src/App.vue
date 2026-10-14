<script setup lang="ts">
</script>

<template>
  <main class="app-shell">
    <aside class="sidebar">
      <h1>AI 助手</h1>
      <button class="new-chat" type="button">+ 新建对话</button>
      <p class="muted">会话列表将在后续阶段接入 API。</p>
    </aside>
    <section class="chat-panel">
      <div class="empty-state">
        <h2>你的 AI 助手</h2>
        <p>项目界面骨架已创建。下一步实现会话 API 与流式聊天。</p>
      </div>
      <form class="composer" @submit.prevent>
        <textarea aria-label="输入消息" placeholder="给 AI 发送消息（聊天功能尚未接入）" rows="2" />
        <button type="submit" disabled>发送</button>
      </form>
    </section>
  </main>
</template>
