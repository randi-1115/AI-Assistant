<script setup lang="ts">
import type { ToolStatusPayload } from '../../types/chat'

defineProps<{ statuses: ToolStatusPayload[] }>()

const TOOL_LABELS: Record<string, string> = {
  calculator: '计算器',
  document_search: '文档检索',
  web_search: '联网搜索',
}

const FALLBACK: Record<string, string> = {
  running: '正在执行…',
  done: '执行完成',
  empty: '没有找到结果',
  unavailable: '当前不可用',
  failed: '执行失败',
}

function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name
}

function statusText(status: ToolStatusPayload): string {
  if (status.error_code === 'timeout') return '执行超时'
  if (status.tool === 'web_search' && status.status === 'running' && !status.message) {
    return '正在联网搜索…'
  }
  return status.message || FALLBACK[status.status] || status.status
}

function durationText(ms?: number | null): string {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms < 0) return ''
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

function summaryText(value?: string | null): string {
  if (!value) return ''
  return value.length > 80 ? `${value.slice(0, 80)}…` : value
}
</script>

<template>
  <div v-if="statuses.length" class="tool-status-list">
    <div
      v-for="(status, index) in statuses"
      :key="status.call_id ?? `${status.tool}-${status.status}-${index}`"
      class="tool-status-item"
      :class="`status-${status.status}`"
      :title="status.error_code ? `错误码：${status.error_code}` : undefined"
    >
      <span class="tool-status-name">{{ toolLabel(status.tool) }}</span>
      <span v-if="summaryText(status.arguments_summary)" class="tool-status-args">
        {{ summaryText(status.arguments_summary) }}
      </span>
      <span class="tool-status-text">{{ statusText(status) }}</span>
      <span v-if="durationText(status.duration_ms)" class="tool-status-duration">
        {{ durationText(status.duration_ms) }}
      </span>
    </div>
  </div>
</template>
