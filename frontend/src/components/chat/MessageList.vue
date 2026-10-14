<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

import type { Message } from '../../types/message'
import MessageBubble from './MessageBubble.vue'

const props = defineProps<{
  messages: Message[]
  loading: boolean
  streaming: boolean
  error: string | null
}>()

const emit = defineEmits<{ regenerate: []; dismissError: [] }>()

const container = ref<HTMLElement | null>(null)

function scrollToBottom(): void {
  const element = container.value
  if (element) {
    element.scrollTop = element.scrollHeight
  }
}

// 新消息或新的流式 token 到达时都滚到底部。
// 依赖里带上最后一条消息的内容，否则流式过程中长度不变、不会触发。
watch(
  () => [props.messages.length, props.messages.at(-1)?.content] as const,
  async () => {
    await nextTick()
    scrollToBottom()
  },
)

const hasAssistant = (): boolean => props.messages.some((m) => m.role === 'assistant')
</script>

<template>
  <div ref="container" class="message-list">
    <div v-if="loading" class="empty-state">
      <p class="muted">正在加载历史消息…</p>
    </div>

    <div v-else-if="!messages.length" class="empty-state">
      <h2>你的 AI 助手</h2>
      <p>在下面输入消息开始对话。左侧可以新建和切换会话。</p>
    </div>

    <template v-else>
      <MessageBubble v-for="message in messages" :key="message.id" :message="message" />

      <div v-if="!streaming && hasAssistant()" class="message-actions">
        <button class="ghost-button" type="button" @click="emit('regenerate')">
          ↻ 重新生成
        </button>
      </div>
    </template>

    <p v-if="error" class="chat-error">
      {{ error }}
      <button class="ghost-button" type="button" @click="emit('dismissError')">知道了</button>
    </p>
  </div>
</template>
