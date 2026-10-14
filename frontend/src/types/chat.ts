/** 与后端 app/schemas/chat.py 对应，含 SSE 事件载荷。 */

export type ChatMode =
  | 'chat'
  | 'write'
  | 'polish'
  | 'summarize'
  | 'translate'
  | 'brainstorm'
  | 'study'

export type SearchMode = 'auto' | 'off'

export interface ChatStreamRequest {
  content: string
  attachment_ids?: number[]
  mode?: ChatMode
  search_mode?: SearchMode
}

export interface ChatUsage {
  input_tokens: number | null
  output_tokens: number | null
}

/** SSE 事件名，与后端 app/api/v1/sse.py 发出的一致。 */
export type SSEEventName =
  | 'message_start'
  | 'token'
  | 'source'
  | 'tool_status'
  | 'message_end'
  | 'error'

export interface MessageStartPayload {
  user_message_id: number
  assistant_message_id: number
}

export interface TokenPayload {
  text: string
}

export interface MessageEndPayload {
  finish_reason: string
  usage: ChatUsage | null
}

export interface SSEErrorPayload {
  code: string
  message: string
  request_id: string
}

export interface SourcePayload {
  title: string
  url: string
  snippet?: string
}

export interface ToolStatusPayload {
  tool: string
  status: string
}

/** 流式回调。P0 只用到 message_start / token / message_end / error；
 *  source 与 tool_status 是给 P1 预留的。 */
export interface SSEHandlers {
  onMessageStart?: (payload: MessageStartPayload) => void
  onToken?: (payload: TokenPayload) => void
  onSource?: (payload: SourcePayload) => void
  onToolStatus?: (payload: ToolStatusPayload) => void
  onMessageEnd?: (payload: MessageEndPayload) => void
  onError?: (payload: SSEErrorPayload) => void
}
