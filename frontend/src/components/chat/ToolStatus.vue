<script setup lang="ts">
import type { ToolStatusPayload } from '../../types/chat'

defineProps<{ statuses: ToolStatusPayload[] }>()

const FALLBACK: Record<string, string> = {
  running: '正在联网搜索…',
  done: '联网搜索完成',
  empty: '联网搜索没有找到相关结果',
  unavailable: '联网搜索不可用',
}
</script>

<template>
  <div v-if="statuses.length" class="tool-status-list">
    <div
      v-for="(status, index) in statuses"
      :key="`${status.tool}-${status.status}-${index}`"
      class="tool-status-item"
      :class="`status-${status.status}`"
    >
      <span class="tool-status-name">联网搜索</span>
      <span class="tool-status-text">{{ status.message || FALLBACK[status.status] }}</span>
    </div>
  </div>
</template>
