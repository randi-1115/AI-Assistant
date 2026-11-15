<script setup lang="ts">
import type { SourcePayload } from '../../types/chat'

defineProps<{ sources: SourcePayload[] }>()

function fileLabel(source: SourcePayload): string {
  if (source.kind !== 'file') return source.title
  const parts: string[] = []
  if (source.citation_label) parts.push(source.citation_label)
  else if (source.excerpt_number != null) parts.push(`资料 ${source.excerpt_number}`)
  parts.push(`片段 ${source.chunk_index + 1}`)
  if (source.section_title) parts.push(source.section_title)
  // score=0 表示总结类问题回退到文档基线内容，不是关键词命中。
  if (source.score > 0) parts.push(`相关度 ${source.score.toFixed(2)}`)
  return parts.join(' · ')
}
</script>

<template>
  <section v-if="sources.length" class="source-list">
    <div class="source-list-title">参考来源</div>

    <div v-for="(source, index) in sources" :key="`${source.kind}-${index}`" class="source-item">
      <template v-if="source.kind === 'file'">
        <div class="source-head">
          <span class="source-title">{{ source.title }}</span>
          <span class="source-meta">{{ fileLabel(source) }}</span>
        </div>
        <div v-if="source.snippet" class="source-snippet">{{ source.snippet }}</div>
      </template>

      <template v-else>
        <div class="source-head">
          <a :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.title }}</a>
          <span v-if="source.domain" class="source-meta">· {{ source.domain }}</span>
          <span v-if="source.published_date" class="source-meta">· {{ source.published_date }}</span>
          <span v-if="source.score" class="source-meta">· 相关度 {{ source.score.toFixed(2) }}</span>
        </div>
        <div v-if="source.snippet" class="source-snippet">{{ source.snippet }}</div>
      </template>
    </div>
  </section>
</template>
