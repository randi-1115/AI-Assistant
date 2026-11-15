<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { onMounted, watch } from 'vue'

import ChatInput from '../components/chat/ChatInput.vue'
import ConversationSidebar from '../components/chat/ConversationSidebar.vue'
import MessageList from '../components/chat/MessageList.vue'
import AppLayout from '../components/layout/AppLayout.vue'
import { useChatStore } from '../stores/chat'
import { useConversationsStore } from '../stores/conversations'
import type { SearchMode } from '../types/chat'
import type { UploadedFile } from '../types/file'

const conversations = useConversationsStore()
const chat = useChatStore()

const { currentId, capabilities } = storeToRefs(conversations)
const { messages, loading, streaming, error } = storeToRefs(chat)

onMounted(async () => {
  await Promise.all([conversations.load(), conversations.loadCapabilities()])
  if (currentId.value !== null) {
    await chat.loadMessages(currentId.value)
  }
})

// 切换会话时加载对应历史。所有加载都经过 chat store 的加载令牌，
// 因此这个 watcher 和「用户立刻开始发消息」不会互相覆盖。
watch(currentId, async (conversationId) => {
  if (conversationId === null) {
    chat.clear()
    return
  }
  await chat.loadMessages(conversationId)
})

async function handleSend(
  content: string,
  attachments: UploadedFile[],
  searchMode: SearchMode,
): Promise<void> {
  let conversationId = currentId.value
  // 还没有会话时直接开聊：自动建一个再发。
  if (conversationId === null) {
    const created = await conversations.create()
    if (!created) return
    conversationId = created.id
  }
  await chat.sendMessage(conversationId, content, attachments, searchMode)
}

async function handleRegenerate(): Promise<void> {
  if (currentId.value === null) return
  await chat.regenerate(currentId.value)
}
</script>

<template>
  <AppLayout>
    <template #sidebar>
      <ConversationSidebar />
    </template>

    <MessageList
      :messages="messages"
      :loading="loading"
      :streaming="streaming"
      :error="error"
      @regenerate="handleRegenerate"
      @dismiss-error="chat.clearError()"
    />

    <ChatInput
      :disabled="streaming"
      :streaming="streaming"
      :conversation-id="currentId"
      :web-search-available="capabilities?.web_search ?? true"
      @send="handleSend"
      @stop="chat.stop()"
    />
  </AppLayout>
</template>
