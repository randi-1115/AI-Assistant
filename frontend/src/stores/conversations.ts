/** 会话列表状态：加载、新建、重命名、删除、切换。 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import * as api from '../api/conversations'
import { ApiError } from '../api/http'
import type { Capabilities } from '../types/capability'
import type { Conversation } from '../types/conversation'

export function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return '发生未知错误'
}

export const useConversationsStore = defineStore('conversations', () => {
  const items = ref<Conversation[]>([])
  const currentId = ref<number | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const capabilities = ref<Capabilities | null>(null)

  const current = computed(
    () => items.value.find((item) => item.id === currentId.value) ?? null,
  )
  const isEmpty = computed(() => items.value.length === 0)

  async function load(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const page = await api.listConversations()
      items.value = page.items
      // 当前选中的会话被删掉后（比如在别的标签页删的），回落到第一个。
      if (currentId.value !== null && !items.value.some((c) => c.id === currentId.value)) {
        currentId.value = items.value[0]?.id ?? null
      }
    } catch (caught) {
      error.value = describeError(caught)
    } finally {
      loading.value = false
    }
  }

  async function loadCapabilities(): Promise<void> {
    try {
      capabilities.value = await api.getCapabilities()
    } catch {
      // 能力查询失败不影响主流程，按「全部不可用」处理即可。
      capabilities.value = null
    }
  }

  async function create(): Promise<Conversation | null> {
    error.value = null
    try {
      const conversation = await api.createConversation()
      items.value.unshift(conversation)
      currentId.value = conversation.id
      return conversation
    } catch (caught) {
      error.value = describeError(caught)
      return null
    }
  }

  async function rename(conversationId: number, title: string): Promise<void> {
    const trimmed = title.trim()
    if (!trimmed) return
    error.value = null
    try {
      const updated = await api.renameConversation(conversationId, { title: trimmed })
      replace(updated)
    } catch (caught) {
      error.value = describeError(caught)
    }
  }

  async function remove(conversationId: number): Promise<void> {
    error.value = null
    try {
      await api.deleteConversation(conversationId)
      items.value = items.value.filter((item) => item.id !== conversationId)
      if (currentId.value === conversationId) {
        currentId.value = items.value[0]?.id ?? null
      }
    } catch (caught) {
      error.value = describeError(caught)
    }
  }

  function select(conversationId: number | null): void {
    currentId.value = conversationId
  }

  function replace(conversation: Conversation): void {
    const index = items.value.findIndex((item) => item.id === conversation.id)
    if (index === -1) {
      items.value.unshift(conversation)
    } else {
      items.value[index] = conversation
    }
  }

  /** 有新消息时把会话顶到列表最前，和后端按 updated_at 排序保持一致。 */
  function promote(conversationId: number): void {
    const index = items.value.findIndex((item) => item.id === conversationId)
    if (index > 0) {
      const [item] = items.value.splice(index, 1)
      if (item) items.value.unshift(item)
    }
  }

  /** 重新拉取单个会话。首轮消息后标题是后端异步生成的，用它把新标题取回来。 */
  async function refreshOne(conversationId: number): Promise<void> {
    try {
      replace(await api.getConversation(conversationId))
    } catch {
      // 标题没取到不影响使用，忽略。
    }
  }

  return {
    items,
    currentId,
    current,
    isEmpty,
    loading,
    error,
    capabilities,
    load,
    loadCapabilities,
    create,
    rename,
    remove,
    select,
    replace,
    promote,
    refreshOne,
  }
})
