<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  /** 没有选中会话或正在流式输出时为 true */
  disabled: boolean
  streaming: boolean
}>()

const emit = defineEmits<{ send: [content: string]; stop: [] }>()

const text = ref('')
const textarea = ref<HTMLTextAreaElement | null>(null)

function submit(): void {
  const content = text.value.trim()
  if (!content || props.disabled) return
  text.value = ''
  emit('send', content)
}

function handleKeydown(event: KeyboardEvent): void {
  // isComposing：中文输入法选词时的回车是「确认候选」，不能当作发送。
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  submit()
}

function focus(): void {
  textarea.value?.focus()
}

defineExpose({ focus })
</script>

<template>
  <form class="composer" @submit.prevent="submit">
    <textarea
      ref="textarea"
      v-model="text"
      aria-label="输入消息"
      :placeholder="
        disabled
          ? '正在生成回答…'
          : '给 AI 发送消息（Enter 发送，Shift + Enter 换行）'
      "
      :disabled="disabled"
      rows="2"
      @keydown="handleKeydown"
    />
    <button v-if="streaming" class="stop-button" type="button" @click="emit('stop')">
      停止生成
    </button>
    <button v-else class="send-button" type="submit" :disabled="disabled || !text.trim()">
      发送
    </button>
  </form>
</template>
