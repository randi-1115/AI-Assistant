/** 消息列表与流式状态。
 *
 * 流式期间本地会先插入「临时消息」立刻显示，收到 message_start 后再把 id
 * 换成后端的真实 id——这样界面不会在等第一个 token 期间空着。
 *
 * 停止生成靠 AbortController：abort() 后浏览器掐断连接，后端感知到断开，
 * 把消息落库为 interrupted。前端不额外发「停止」请求，避免和后端的落库竞态。
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { isAbortError, regenerateChat, streamChat } from '../api/chat'
import * as api from '../api/conversations'
import type {
  SearchMode,
  SourcePayload,
  SSEHandlers,
  ToolStatusPayload,
} from '../types/chat'
import { DEFAULT_CONVERSATION_TITLE } from '../types/conversation'
import type { MessageAttachment, UploadedFile } from '../types/file'
import type { Message } from '../types/message'
import { describeError, useConversationsStore } from './conversations'

/** 自动标题是后端异步生成的，流结束后等一会儿再去取。 */
const TITLE_REFRESH_DELAY_MS = 1500

export const useChatStore = defineStore('chat', () => {
  const messages = ref<Message[]>([])
  const loading = ref(false)
  const streaming = ref(false)
  const error = ref<string | null>(null)

  let controller: AbortController | null = null
  let tempIdCounter = -1
  // 用「加载令牌」解决竞态：切换会话触发的历史加载如果晚于用户开始发消息才返回，
  // 它的结果必须被丢弃，否则会用旧历史覆盖刚插入的临时消息。
  let loadToken = 0

  const canSend = computed(() => !streaming.value)
  const lastMessage = computed(() => messages.value[messages.value.length - 1] ?? null)

  function nextTempId(): number {
    return tempIdCounter--
  }

  function clear(): void {
    loadToken += 1
    messages.value = []
    error.value = null
  }

  async function loadMessages(conversationId: number): Promise<void> {
    const token = (loadToken += 1)
    messages.value = []
    error.value = null
    loading.value = true
    try {
      const loaded = await api.listMessages(conversationId)
      if (token !== loadToken) return
      messages.value = loaded
    } catch (caught) {
      if (token !== loadToken) return
      error.value = describeError(caught)
    } finally {
      if (token === loadToken) loading.value = false
    }
  }

  function attachmentSnapshot(file: UploadedFile): MessageAttachment {
    return {
      id: file.file_id,
      name: file.original_name,
      parse_status: file.parse_status,
      supported_for_qa: file.supported_for_qa,
      error_message: file.error_message ?? undefined,
    }
  }

  function pushMessage(
    partial: Pick<Message, 'conversation_id' | 'role' | 'content' | 'status'> & {
      metadata_json?: Record<string, unknown> | null
    },
  ): Message {
    // 本地追加消息即视为「用户已经开始新的交互」，作废任何在途的历史加载。
    loadToken += 1
    const now = new Date().toISOString()
    messages.value.push({
      id: nextTempId(),
      conversation_id: partial.conversation_id,
      role: partial.role,
      content: partial.content,
      status: partial.status,
      model_provider: null,
      model_name: null,
      metadata_json: partial.metadata_json ?? null,
      created_at: now,
    })
    // 必须从数组里取回响应式代理：直接改原始对象不会触发视图更新。
    return messages.value[messages.value.length - 1] as Message
  }

  async function runStream(
    conversationId: number,
    run: (handlers: SSEHandlers, signal: AbortSignal) => Promise<void>,
    userMessage: Message | null,
    assistantMessage: Message,
  ): Promise<void> {
    streaming.value = true
    error.value = null
    const abort = new AbortController()
    controller = abort
    const sources: SourcePayload[] = []
    const toolStatuses: ToolStatusPayload[] = []
    let runningStatus: ToolStatusPayload | null = null

    const handlers: SSEHandlers = {
      onMessageStart: (payload) => {
        // 用后端真实 id 替换本地临时 id，之后「重新生成」才能找对消息。
        if (userMessage) userMessage.id = payload.user_message_id
        assistantMessage.id = payload.assistant_message_id
      },
      onToken: ({ text }) => {
        assistantMessage.content += text
      },
      onSource: (payload) => {
        sources.push(payload)
        // 来源逐条到达，边流式输出边展示；流结束后后端也会落库同样的结构。
        assistantMessage.metadata_json = {
          ...(assistantMessage.metadata_json ?? {}),
          sources: [...sources],
        }
      },
      onToolStatus: (payload) => {
        // running 只是瞬态展示；终态才写入消息元数据。
        if (payload.status === 'running') {
          runningStatus = payload
        } else {
          toolStatuses.push(payload)
          runningStatus = null
        }
        assistantMessage.metadata_json = {
          ...(assistantMessage.metadata_json ?? {}),
          tool_statuses: runningStatus
            ? [...toolStatuses, runningStatus]
            : [...toolStatuses],
        }
      },
      onMessageEnd: ({ finish_reason }) => {
        assistantMessage.status = finish_reason === 'error' ? 'failed' : 'complete'
      },
      onError: (payload) => {
        // 流已经开始了，后端只能通过事件报错。
        error.value = payload.message
        assistantMessage.status = 'failed'
      },
    }

    try {
      await run(handlers, abort.signal)
      if (assistantMessage.status === 'streaming') {
        assistantMessage.status = 'complete'
      }
    } catch (caught) {
      if (isAbortError(caught)) {
        // 用户主动停止：不是错误，只是没写完。
        assistantMessage.status = 'interrupted'
      } else {
        error.value = describeError(caught)
        assistantMessage.status = 'failed'
      }
    } finally {
      // 中断/出错时不要留下永远「搜索中」的状态。
      if (runningStatus) {
        runningStatus = null
        assistantMessage.metadata_json = {
          ...(assistantMessage.metadata_json ?? {}),
          tool_statuses: [...toolStatuses],
        }
      }
      controller = null
      streaming.value = false

      const conversations = useConversationsStore()
      conversations.promote(conversationId)
      // 只有标题还是默认值时才去取——说明这是首轮，后端正在后台起标题。
      const conversation = conversations.items.find((item) => item.id === conversationId)
      if (conversation?.title === DEFAULT_CONVERSATION_TITLE) {
        window.setTimeout(() => {
          void conversations.refreshOne(conversationId)
        }, TITLE_REFRESH_DELAY_MS)
      }
    }
  }

  async function sendMessage(
    conversationId: number,
    content: string,
    attachments: UploadedFile[] = [],
    searchMode: SearchMode = 'off',
  ): Promise<void> {
    if (streaming.value) return
    const userMessage = pushMessage({
      conversation_id: conversationId,
      role: 'user',
      content,
      status: 'complete',
      metadata_json: {
        search_mode: searchMode,
        ...(attachments.length
          ? { attachments: attachments.map(attachmentSnapshot) }
          : {}),
      },
    })
    const assistantMessage = pushMessage({
      conversation_id: conversationId,
      role: 'assistant',
      content: '',
      status: 'streaming',
    })

    const attachmentIds = attachments.map((file) => file.file_id)
    await runStream(
      conversationId,
      (handlers, signal) =>
        streamChat(
          conversationId,
          {
            content,
            search_mode: searchMode,
            ...(attachmentIds.length ? { attachment_ids: attachmentIds } : {}),
          },
          handlers,
          signal,
        ),
      userMessage,
      assistantMessage,
    )
  }

  async function regenerate(conversationId: number): Promise<void> {
    if (streaming.value) return

    let lastAssistantIndex = -1
    for (let index = messages.value.length - 1; index >= 0; index -= 1) {
      if (messages.value[index]?.role === 'assistant') {
        lastAssistantIndex = index
        break
      }
    }
    if (lastAssistantIndex === -1) {
      error.value = '这个会话还没有可重新生成的回答'
      return
    }

    // 后端会把旧的助手消息删掉重建，本地先移除，避免新旧内容同时显示。
    messages.value.splice(lastAssistantIndex, 1)
    const assistantMessage = pushMessage({
      conversation_id: conversationId,
      role: 'assistant',
      content: '',
      status: 'streaming',
    })

    await runStream(
      conversationId,
      (handlers, signal) => regenerateChat(conversationId, handlers, signal),
      // 重新生成不新增用户消息，沿用库里已有的那条。
      null,
      assistantMessage,
    )
  }

  function stop(): void {
    controller?.abort()
  }

  function clearError(): void {
    error.value = null
  }

  return {
    messages,
    loading,
    streaming,
    error,
    canSend,
    lastMessage,
    clear,
    loadMessages,
    sendMessage,
    regenerate,
    stop,
    clearError,
  }
})
