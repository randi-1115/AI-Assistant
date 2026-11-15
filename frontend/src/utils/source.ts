/** 对消息 metadata_json 做运行时校验。
 *
 * metadata 来自后端 JSON，历史数据或旧版本可能缺字段；这里一律宽松处理，
 * 坏数据静默丢弃，绝不让渲染层因为一个脏字段崩掉。
 */

import type { SourcePayload, ToolStatus, ToolStatusPayload } from '../types/chat'
import type { MessageAttachment, ParseStatus } from '../types/file'

const PARSE_STATUSES: ReadonlySet<string> = new Set([
  'pending',
  'processing',
  'ready',
  'failed',
  'unsupported',
])

const TOOL_STATUSES: ReadonlySet<string> = new Set([
  'running',
  'done',
  'empty',
  'unavailable',
])

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function asString(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** 只允许 http/https 绝对链接，防止历史数据里的 javascript:/data:。 */
function safeHttpUrl(value: unknown): string | null {
  const raw = asString(value)
  if (!raw) return null
  try {
    const url = new URL(raw)
    return url.protocol === 'http:' || url.protocol === 'https:' ? raw : null
  } catch {
    return null
  }
}

export function sourcesFromMetadata(
  metadata: Record<string, unknown> | null | undefined,
): SourcePayload[] {
  const raw = metadata?.sources
  if (!Array.isArray(raw)) return []

  const sources: SourcePayload[] = []
  for (const item of raw) {
    if (!isRecord(item)) continue
    const title = asString(item.title)
    if (!title) continue

    if (item.kind === 'file') {
      const fileId = asNumber(item.file_id)
      const chunkIndex = asNumber(item.chunk_index)
      const score = asNumber(item.score)
      if (fileId === null || chunkIndex === null || score === null) continue
      sources.push({
        kind: 'file',
        file_id: fileId,
        chunk_index: chunkIndex,
        title,
        url: null,
        section_title: asString(item.section_title),
        snippet: asString(item.snippet),
        score,
        excerpt_number: asNumber(item.excerpt_number),
        citation_label: asString(item.citation_label),
      })
      continue
    }

    // 兼容没有 kind 的旧格式：有安全 url 就当联网来源处理。
    const url = safeHttpUrl(item.url)
    if (url) {
      sources.push({
        kind: 'web',
        title,
        url,
        snippet: asString(item.snippet),
        published_date: asString(item.published_date),
        domain: asString(item.domain),
        score: asNumber(item.score),
      })
    }
  }
  return sources
}

export function toolStatusesFromMetadata(
  metadata: Record<string, unknown> | null | undefined,
): ToolStatusPayload[] {
  const raw = metadata?.tool_statuses
  if (!Array.isArray(raw)) return []

  const statuses: ToolStatusPayload[] = []
  for (const item of raw) {
    if (!isRecord(item)) continue
    const tool = asString(item.tool)
    const status = asString(item.status)
    if (!tool || !status || !TOOL_STATUSES.has(status)) continue
    statuses.push({
      tool,
      status: status as ToolStatus,
      message: asString(item.message),
      provider: asString(item.provider),
    })
  }
  return statuses
}

export function attachmentsFromMetadata(
  metadata: Record<string, unknown> | null | undefined,
): MessageAttachment[] {
  const raw = metadata?.attachments
  if (!Array.isArray(raw)) return []

  const attachments: MessageAttachment[] = []
  for (const item of raw) {
    if (!isRecord(item)) continue
    const id = asNumber(item.id)
    const name = asString(item.name)
    const parseStatus = asString(item.parse_status)
    if (id === null || !name || !parseStatus || !PARSE_STATUSES.has(parseStatus)) continue
    attachments.push({
      id,
      name,
      parse_status: parseStatus as ParseStatus,
      supported_for_qa: item.supported_for_qa === true,
      error_message: asString(item.error_message) ?? undefined,
    })
  }
  return attachments
}
