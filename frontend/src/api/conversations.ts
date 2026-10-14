/** 会话与历史消息接口。契约见 docs/API-SPEC.md。 */

import type { Capabilities } from '../types/capability'
import type { Page } from '../types/common'
import type {
  Conversation,
  ConversationCreatePayload,
  ConversationRenamePayload,
} from '../types/conversation'
import type { Message } from '../types/message'
import { request } from './http'

export function listConversations(params: {
  keyword?: string
  page?: number
  pageSize?: number
} = {}): Promise<Page<Conversation>> {
  const query = new URLSearchParams()
  if (params.keyword) query.set('keyword', params.keyword)
  query.set('page', String(params.page ?? 1))
  query.set('page_size', String(params.pageSize ?? 50))
  return request<Page<Conversation>>(`/conversations?${query.toString()}`)
}

export function getConversation(conversationId: number): Promise<Conversation> {
  return request<Conversation>(`/conversations/${conversationId}`)
}

export function createConversation(payload: ConversationCreatePayload = {}): Promise<Conversation> {
  return request<Conversation>('/conversations', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function renameConversation(
  conversationId: number,
  payload: ConversationRenamePayload,
): Promise<Conversation> {
  return request<Conversation>(`/conversations/${conversationId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function deleteConversation(conversationId: number): Promise<void> {
  return request<void>(`/conversations/${conversationId}`, { method: 'DELETE' })
}

export function listMessages(conversationId: number, limit = 100): Promise<Message[]> {
  return request<Message[]>(`/conversations/${conversationId}/messages?limit=${limit}`)
}

export function getCapabilities(): Promise<Capabilities> {
  return request<Capabilities>('/capabilities')
}
