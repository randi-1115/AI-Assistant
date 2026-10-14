/** 与后端 app/schemas/message.py 对应。 */

export type MessageRole = 'user' | 'assistant' | 'tool'

export type MessageStatus = 'streaming' | 'complete' | 'interrupted' | 'failed'

export interface Message {
  id: number
  conversation_id: number
  role: MessageRole
  content: string
  status: MessageStatus
  model_provider: string | null
  model_name: string | null
  metadata_json: Record<string, unknown> | null
  /** ISO 8601 字符串 */
  created_at: string
}

/** 流式期间本地先把 id 置为负数占位，收到 message_start 后用后端真实 id 替换。 */
export const TEMPORARY_ID_BASE = -1
