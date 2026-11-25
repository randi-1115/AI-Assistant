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

/** 工具模式：off 保持 P1 预检索；auto 由模型决定调用哪些工具。 */
export type AgentMode = 'auto' | 'off'

export interface ChatStreamRequest {
  content: string
  attachment_ids?: number[]
  mode?: ChatMode
  search_mode?: SearchMode
  agent_mode?: AgentMode
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

interface SourceBase {
  title: string
  url: string | null
  snippet: string | null
}

/** 文件来源：阶段 2 的主体。file 没有下载端点，url 固定为 null。 */
export interface FileSourcePayload extends SourceBase {
  kind: 'file'
  file_id: number
  chunk_index: number
  section_title: string | null
  score: number
  excerpt_number?: number | null
  citation_label?: string | null
}

/** 联网搜索来源：P1/P2 共用。 */
export interface WebSourcePayload extends SourceBase {
  kind: 'web'
  url: string
  published_date?: string | null
  domain?: string | null
  score?: number | null
}

export type SourcePayload = FileSourcePayload | WebSourcePayload

/** 终态里的 failed 涵盖参数错误、未知工具、执行异常；超时用 error_code 区分。 */
export type ToolStatus =
  | 'running'
  | 'done'
  | 'empty'
  | 'unavailable'
  | 'failed'

export interface ToolStatusPayload {
  tool: string
  status: ToolStatus
  message?: string | null
  provider?: string | null
  call_id?: string | null
  arguments_summary?: string | null
  duration_ms?: number | null
  error_code?: string | null
}

/** 流式回调。source/tool_status 由 P1 检索与 P2 工具执行产生。 */
export interface SSEHandlers {
  onMessageStart?: (payload: MessageStartPayload) => void
  onToken?: (payload: TokenPayload) => void
  onSource?: (payload: SourcePayload) => void
  onToolStatus?: (payload: ToolStatusPayload) => void
  onMessageEnd?: (payload: MessageEndPayload) => void
  onError?: (payload: SSEErrorPayload) => void
}
