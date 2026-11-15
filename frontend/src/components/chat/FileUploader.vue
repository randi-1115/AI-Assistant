<script setup lang="ts">
import { ref } from 'vue'

import { uploadFile } from '../../api/files'
import { ApiError } from '../../api/http'
import {
  MAX_UPLOAD_MB,
  SUPPORTED_UPLOAD_EXTENSIONS,
  type UploadedFile,
} from '../../types/file'

const props = defineProps<{
  disabled: boolean
  conversationId: number | null
}>()

const emit = defineEmits<{ uploaded: [file: UploadedFile]; error: [message: string] }>()

const SUPPORTED = new Set<string>(SUPPORTED_UPLOAD_EXTENSIONS)
const input = ref<HTMLInputElement | null>(null)
const uploading = ref(false)

function pick(): void {
  if (props.disabled || uploading.value) return
  input.value?.click()
}

function extensionOf(name: string): string {
  const index = name.lastIndexOf('.')
  return index >= 0 ? name.slice(index).toLowerCase() : ''
}

function describeError(caught: unknown): string {
  if (caught instanceof ApiError) return caught.message
  if (caught instanceof Error) return caught.message
  return '上传失败'
}

async function handleFiles(event: Event): Promise<void> {
  const target = event.target as HTMLInputElement
  const files = Array.from(target.files ?? [])
  // 立刻清空，保证同一文件可以再次选择。
  target.value = ''
  if (!files.length || props.disabled) return

  uploading.value = true
  try {
    for (const file of files) {
      const extension = extensionOf(file.name)
      if (!SUPPORTED.has(extension)) {
        emit('error', `不支持的文件类型：${extension || '未知'}`)
        continue
      }
      if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
        emit('error', `${file.name} 超过 ${MAX_UPLOAD_MB}MB`)
        continue
      }
      try {
        const uploaded = await uploadFile(file, props.conversationId)
        emit('uploaded', uploaded)
        if (!uploaded.supported_for_qa) {
          emit(
            'error',
            uploaded.error_message ?? `${uploaded.original_name} 暂时不能用于问答`,
          )
        }
      } catch (caught) {
        emit('error', describeError(caught))
      }
    }
  } finally {
    uploading.value = false
  }
}
</script>

<template>
  <div class="file-uploader">
    <input
      ref="input"
      type="file"
      hidden
      multiple
      :accept="SUPPORTED_UPLOAD_EXTENSIONS.join(',')"
      @change="handleFiles"
    />
    <button
      type="button"
      class="attach-button"
      :disabled="disabled || uploading"
      :title="`上传文件（最大 ${MAX_UPLOAD_MB}MB）`"
      @click="pick"
    >
      {{ uploading ? '上传中…' : '＋ 文件' }}
    </button>
  </div>
</template>
