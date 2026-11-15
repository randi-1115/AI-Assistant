/** 聊天接口：流式发送与重新生成。
 *
 * SSE 的解析细节都在 sse.ts，这里只负责发请求、把事件分派给回调、以及把
 * 「用户主动停止」和「真的出错」区分开。
 */

import type {
  ChatStreamRequest,
  MessageEndPayload,
  MessageStartPayload,
  SourcePayload,
  SSEErrorPayload,
  SSEHandlers,
  TokenPayload,
  ToolStatusPayload,
} from '../types/chat'
import { apiUrl, toApiError } from './http'
import { SSEParser } from './sse'

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function conversationBase(conversationId: number): string {
  return `/conversations/${conversationId}`
}

async function streamRequest(
  path: string,
  body: ChatStreamRequest | null,
  handlers: SSEHandlers,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(apiUrl(path), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: body === null ? undefined : JSON.stringify(body),
    signal,
  })

  // 流开始**之前**的错误是普通 JSON 错误信封。
  if (!response.ok) {
    throw await toApiError(response)
  }
  if (!response.body) {
    throw new Error('当前浏览器不支持流式响应（response.body 为空）')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  const parser = new SSEParser()

  const dispatch = (events: ReturnType<SSEParser['push']>): void => {
    for (const { event, data } of events) {
      switch (event) {
        case 'message_start':
          handlers.onMessageStart?.(data as MessageStartPayload)
          break
        case 'token':
          handlers.onToken?.(data as TokenPayload)
          break
        case 'message_end':
          handlers.onMessageEnd?.(data as MessageEndPayload)
          break
        case 'error':
          // 流已开始，后端改不了状态码，只能靠事件报错。
          handlers.onError?.(data as SSEErrorPayload)
          break
        case 'source':
          handlers.onSource?.(data as SourcePayload)
          break
        case 'tool_status':
          handlers.onToolStatus?.(data as ToolStatusPayload)
          break
        default:
          break
      }
    }
  }

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      // stream: true 让多字节字符被切开时也能正确解码。
      dispatch(parser.push(decoder.decode(value, { stream: true })))
    }
    dispatch(parser.flush())
  } finally {
    // 主动 abort 时 read() 会抛 AbortError，锁不一定自动释放。
    reader.releaseLock()
  }
}

export async function streamChat(
  conversationId: number,
  payload: ChatStreamRequest,
  handlers: SSEHandlers,
  signal: AbortSignal,
): Promise<void> {
  return streamRequest(`${conversationBase(conversationId)}/messages/stream`, payload, handlers, signal)
}

export async function regenerateChat(
  conversationId: number,
  handlers: SSEHandlers,
  signal: AbortSignal,
): Promise<void> {
  return streamRequest(`${conversationBase(conversationId)}/regenerate`, null, handlers, signal)
}
