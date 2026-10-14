/** 统一请求封装与错误归一化。
 *
 * 后端所有错误都是同一个信封：{"error": {"code", "message", "request_id"}}，
 * 所以前端只需要在**一个地方**把它转成 ApiError，其余代码 catch 到的一律是
 * ApiError，暴露 code / message / requestId 三个有用字段。
 */

import type { ApiErrorResponse } from '../types/common'

// 留空则走相对路径，由 Vite 开发代理转发到后端——开发时完全不涉及跨域。
const RAW_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

export const API_BASE = RAW_BASE.replace(/\/+$/, '')
export const API_PREFIX = '/api/v1'

export class ApiError extends Error {
  readonly code: string
  readonly requestId: string
  readonly status: number

  constructor(code: string, message: string, requestId: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.requestId = requestId
    this.status = status
  }
}

export function apiUrl(path: string): string {
  return `${API_BASE}${API_PREFIX}${path}`
}

/** 把非 2xx 响应转成 ApiError。响应不是 JSON 信封时用兜底文案，不抛解析错误。 */
export async function toApiError(response: Response): Promise<ApiError> {
  let code = `http_${response.status}`
  let message = `请求失败（HTTP ${response.status}）`
  // 直连（非代理）时这个头不在 CORS 暴露列表里，取不到就留空。
  let requestId = response.headers.get('X-Request-ID') ?? ''

  try {
    const body = (await response.json()) as ApiErrorResponse
    if (body?.error) {
      code = body.error.code || code
      message = body.error.message || message
      requestId = body.error.request_id || requestId
    }
  } catch {
    // 响应体不是 JSON（比如网关返回的纯文本），保留上面的兜底文案。
  }
  return new ApiError(code, message, requestId, response.status)
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...((init.headers as Record<string, string> | undefined) ?? {}),
    },
  })

  if (!response.ok) {
    throw await toApiError(response)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}
