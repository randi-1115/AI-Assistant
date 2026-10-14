/** 与后端 app/schemas/common.py 对应的通用结构。 */

export interface Page<T> {
  items: T[]
  page: number
  page_size: number
  total: number
}

/** 后端统一错误信封：{"error": {"code", "message", "request_id"}} */
export interface ApiErrorBody {
  code: string
  message: string
  request_id: string
}

export interface ApiErrorResponse {
  error: ApiErrorBody
}
