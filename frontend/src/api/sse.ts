/** 手写 SSE 解析。
 *
 * 为什么不用 EventSource：EventSource 只能发 GET、不能带请求体，而我们要用
 * POST 传消息内容。所以只能用 fetch + ReadableStream 自己解析。
 *
 * 这个文件是纯逻辑、不碰 fetch，便于单独测——**分块边界**是手写 SSE 最容易
 * 写错的地方：一帧可能被 TCP 切成两半，缓冲区必须跨 read() 保留。
 */

export interface SSEEvent {
  event: string
  data: unknown
}

const BLOCK_SEPARATOR = /\r?\n\r?\n/

/**
 * 解析单个事件块（不含分隔空行）。返回 null 表示这一块没有数据、应当忽略。
 */
export function parseSSEBlock(block: string): SSEEvent | null {
  let eventName = 'message'
  const dataLines: string[] = []

  for (const rawLine of block.split(/\r?\n/)) {
    // 以 ':' 开头的是注释，服务端用它做保活心跳。
    if (!rawLine || rawLine.startsWith(':')) continue

    const colon = rawLine.indexOf(':')
    const field = colon === -1 ? rawLine : rawLine.slice(0, colon)
    let value = colon === -1 ? '' : rawLine.slice(colon + 1)
    // 规范允许冒号后有一个空格，要去掉。
    if (value.startsWith(' ')) value = value.slice(1)

    if (field === 'event') {
      eventName = value
    } else if (field === 'data') {
      dataLines.push(value)
    }
  }

  if (dataLines.length === 0) return null

  const raw = dataLines.join('\n')
  try {
    return { event: eventName, data: raw ? JSON.parse(raw) : null }
  } catch {
    // 单帧解析失败不应该让整条流崩掉。
    return null
  }
}

/**
 * 增量解析器。每次收到一个 chunk 就 push 一次，返回这次能凑齐的完整事件。
 *
 * 缓冲区保留原始文本、不预先做换行归一化——因为如果某个 chunk 正好以 '\r'
 * 结尾，提前把它变成 '\n'、下个 chunk 又送来 '\n'，就会凭空造出一个事件边界。
 */
export class SSEParser {
  private buffer = ''

  push(chunk: string): SSEEvent[] {
    this.buffer += chunk
    const events: SSEEvent[] = []

    let match = BLOCK_SEPARATOR.exec(this.buffer)
    while (match !== null) {
      const block = this.buffer.slice(0, match.index)
      this.buffer = this.buffer.slice(match.index + match[0].length)
      const parsed = parseSSEBlock(block)
      if (parsed) events.push(parsed)
      match = BLOCK_SEPARATOR.exec(this.buffer)
    }
    return events
  }

  /** 流结束时调用：处理最后一个没有以空行收尾的事件。 */
  flush(): SSEEvent[] {
    const remaining = this.buffer
    this.buffer = ''
    if (!remaining.trim()) return []
    const parsed = parseSSEBlock(remaining)
    return parsed ? [parsed] : []
  }
}
