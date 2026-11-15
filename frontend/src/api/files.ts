/** 文件上传与元数据接口。
 *
 * 上传必须用原生 fetch + FormData：request() 会硬编码 JSON Content-Type，
 * multipart 需要浏览器自己生成 boundary，手动设置会直接解析失败。
 */

import type { FileMeta, UploadedFile } from '../types/file'
import { apiUrl, request, toApiError } from './http'

export async function uploadFile(
  file: File,
  conversationId?: number | null,
): Promise<UploadedFile> {
  const form = new FormData()
  form.append('file', file)
  if (conversationId != null) {
    form.append('conversation_id', String(conversationId))
  }

  const response = await fetch(apiUrl('/files/upload'), {
    method: 'POST',
    body: form,
  })
  if (!response.ok) {
    throw await toApiError(response)
  }
  return (await response.json()) as UploadedFile
}

export function getFile(fileId: number): Promise<FileMeta> {
  return request<FileMeta>(`/files/${fileId}`)
}

export async function deleteFile(fileId: number): Promise<void> {
  await request<void>(`/files/${fileId}`, { method: 'DELETE' })
}
