<script setup lang="ts">
import { ref } from 'vue'

import type { SearchMode } from '../../types/chat'
import { parseStatusLabel, type UploadedFile } from '../../types/file'
import FileUploader from './FileUploader.vue'

const props = defineProps<{
  /** 没有选中会话或正在流式输出时为 true */
  disabled: boolean
  streaming: boolean
  conversationId: number | null
  /** 后端是否配置了联网搜索；false 时开关置灰。 */
  webSearchAvailable?: boolean
}>()

const emit = defineEmits<{
  send: [content: string, attachments: UploadedFile[], searchMode: SearchMode]
  stop: []
}>()

const text = ref('')
const textarea = ref<HTMLTextAreaElement | null>(null)
const attachments = ref<UploadedFile[]>([])
const uploadError = ref<string | null>(null)
const webSearch = ref(false)

function addAttachment(file: UploadedFile): void {
  uploadError.value = null
  if (attachments.value.some((item) => item.file_id === file.file_id)) return
  attachments.value.push(file)
}

function handleUploadError(message: string): void {
  uploadError.value = message
}

function removeAttachment(fileId: number): void {
  attachments.value = attachments.value.filter((item) => item.file_id !== fileId)
}

function submit(): void {
  const content = text.value.trim()
  if (!content || props.disabled) return
  const pending = [...attachments.value]
  const searchMode: SearchMode =
    props.webSearchAvailable === false ? 'off' : webSearch.value ? 'auto' : 'off'
  text.value = ''
  attachments.value = []
  uploadError.value = null
  // 联网搜索开关是用户偏好，发送后保留，不重置。
  emit('send', content, pending, searchMode)
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
    <div class="composer-body">
      <div v-if="attachments.length" class="attachment-chips">
        <span
          v-for="file in attachments"
          :key="file.file_id"
          class="attachment-chip"
          :class="`status-${file.parse_status}`"
          :title="file.error_message ?? file.original_name"
        >
          <span class="attachment-name">{{ file.original_name }}</span>
          <span class="attachment-status">{{ parseStatusLabel(file.parse_status) }}</span>
          <button
            type="button"
            class="chip-remove"
            aria-label="移除附件"
            :disabled="disabled || streaming"
            @click="removeAttachment(file.file_id)"
          >
            ×
          </button>
        </span>
      </div>

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

      <p v-if="uploadError" class="upload-error">{{ uploadError }}</p>
    </div>

    <div class="composer-actions">
      <div class="composer-actions-left">
        <label
          class="search-toggle"
          :class="{ active: webSearch }"
          :title="
            webSearchAvailable === false
              ? '后端未配置联网搜索（Tavily / SerpApi）'
              : '联网搜索：打开后会把当前问题发送给搜索服务'
          "
        >
          <input
            v-model="webSearch"
            type="checkbox"
            :disabled="disabled || streaming || webSearchAvailable === false"
          />
          <span>联网搜索</span>
        </label>
        <FileUploader
          :disabled="disabled || streaming"
          :conversation-id="conversationId"
          @uploaded="addAttachment"
          @error="handleUploadError"
        />
      </div>
      <button v-if="streaming" class="stop-button" type="button" @click="emit('stop')">
        停止生成
      </button>
      <button v-else class="send-button" type="submit" :disabled="disabled || !text.trim()">
        发送
      </button>
    </div>
  </form>
</template>
