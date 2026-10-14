<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { nextTick, ref } from 'vue'

import { useConversationsStore } from '../../stores/conversations'

const store = useConversationsStore()
const { items, currentId, loading, error } = storeToRefs(store)

const editingId = ref<number | null>(null)
const editingTitle = ref('')
const editInput = ref<HTMLInputElement | null>(null)

// 用函数式 ref 而不是 ref="name"：v-for 里的字符串 ref 会被收集成数组，
// 而这里同一时刻只渲染一个输入框，直接拿元素更直观。
function setEditInput(element: unknown): void {
  editInput.value = (element as HTMLInputElement | null) ?? null
}

async function startEdit(conversationId: number, title: string): Promise<void> {
  editingId.value = conversationId
  editingTitle.value = title
  await nextTick()
  editInput.value?.focus()
  editInput.value?.select()
}

async function commitEdit(): Promise<void> {
  const conversationId = editingId.value
  if (conversationId === null) return
  const title = editingTitle.value
  // 先退出编辑态，避免请求期间重复提交。
  editingId.value = null
  await store.rename(conversationId, title)
}

function cancelEdit(): void {
  editingId.value = null
}

async function confirmDelete(conversationId: number, title: string): Promise<void> {
  const ok = window.confirm(`确定删除会话「${title}」？该会话的消息会一并删除。`)
  if (!ok) return
  await store.remove(conversationId)
}
</script>

<template>
  <div class="sidebar-inner">
    <h1>AI 助手</h1>

    <button class="new-chat" type="button" @click="store.create()">+ 新建对话</button>

    <p v-if="error" class="sidebar-error">{{ error }}</p>

    <ul class="conversation-list">
      <li
        v-for="conversation in items"
        :key="conversation.id"
        class="conversation-item"
        :class="{ active: conversation.id === currentId }"
      >
        <input
          v-if="editingId === conversation.id"
          :ref="setEditInput"
          v-model="editingTitle"
          class="conversation-edit"
          type="text"
          maxlength="200"
          @keyup.enter="commitEdit"
          @keyup.esc="cancelEdit"
          @blur="commitEdit"
        />
        <template v-else>
          <button
            class="conversation-title"
            type="button"
            :title="conversation.title"
            @click="store.select(conversation.id)"
            @dblclick="startEdit(conversation.id, conversation.title)"
          >
            {{ conversation.title }}
          </button>
          <span class="conversation-actions">
            <button
              type="button"
              title="重命名"
              @click="startEdit(conversation.id, conversation.title)"
            >
              ✎
            </button>
            <button
              type="button"
              title="删除"
              @click="confirmDelete(conversation.id, conversation.title)"
            >
              ✕
            </button>
          </span>
        </template>
      </li>
    </ul>

    <p v-if="loading" class="muted">加载中…</p>
    <p v-else-if="!items.length" class="muted">还没有会话。点上面的「新建对话」开始。</p>
  </div>
</template>
