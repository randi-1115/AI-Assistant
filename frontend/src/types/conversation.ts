/** 与后端 app/schemas/conversation.py 对应。 */

export interface Conversation {
  id: number
  title: string
  status: string
  /** ISO 8601 字符串 */
  created_at: string
  updated_at: string
}

export interface ConversationCreatePayload {
  title?: string
}

export interface ConversationRenamePayload {
  title: string
}

/** 后端新建会话时的默认标题，前端用它判断标题是否还没被自动生成过。 */
export const DEFAULT_CONVERSATION_TITLE = '新对话'
