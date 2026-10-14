<script setup lang="ts">
import { computed, ref } from 'vue'

import type { Message, MessageStatus } from '../../types/message'
import { renderMarkdown } from '../../utils/markdown'

const props = defineProps<{ message: Message }>()

const copied = ref(false)
let copiedTimer: number | undefined

const isUser = computed(() => props.message.role === 'user')

// 用户消息按纯文本显示（用户写什么就是什么），只有助手回答走 Markdown。
const renderedHtml = computed(() =>
  isUser.value ? '' : renderMarkdown(props.message.content),
)

const STATUS_LABELS: Record<MessageStatus, string> = {
  streaming: '正在生成…',
  complete: '',
  interrupted: '已停止生成',
  failed: '生成失败',
}

const statusLabel = computed(() => STATUS_LABELS[props.message.status] ?? '')

async function copyContent(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.message.content)
  } catch {
    // 非安全上下文（比如 http 下用 IP 访问）没有剪贴板权限，静默失败即可。
    return
  }
  copied.value = true
  window.clearTimeout(copiedTimer)
  copiedTimer = window.setTimeout(() => {
    copied.value = false
  }, 1500)
}
</script>

<template>
  <article class="bubble" :class="[isUser ? 'bubble-user' : 'bubble-assistant', `status-${message.status}`]">
    <div class="bubble-role">{{ isUser ? '我' : 'AI 助手' }}</div>

    <div v-if="isUser" class="bubble-content bubble-plain">{{ message.content }}</div>
    <!-- eslint-disable-next-line vue/no-v-html -- 内容经过 markdown-it（html:false）渲染，不透传原始 HTML -->
    <div v-else class="bubble-content markdown-body" v-html="renderedHtml" />

    <div v-if="!isUser" class="bubble-footer">
      <span v-if="statusLabel" class="bubble-status">{{ statusLabel }}</span>
      <button
        v-if="message.content"
        class="ghost-button"
        type="button"
        :disabled="message.status === 'streaming'"
        @click="copyContent"
      >
        {{ copied ? '已复制' : '复制' }}
      </button>
    </div>
  </article>
</template>
