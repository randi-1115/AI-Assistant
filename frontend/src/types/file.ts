/** 与后端 app/schemas/file.py 对应。 */

export type ParseStatus = 'pending' | 'processing' | 'ready' | 'failed' | 'unsupported'

/** 上传接口的返回体。 */
export interface UploadedFile {
  file_id: number
  original_name: string
  size_bytes: number
  parse_status: ParseStatus
  supported_for_qa: boolean
  error_message?: string | null
}

/** GET /files/{id} 的返回体。 */
export interface FileMeta extends UploadedFile {
  conversation_id: number | null
  mime_type: string | null
  extension: string
  created_at: string
}

/** 写进用户消息 metadata_json 的附件快照。 */
export interface MessageAttachment {
  id: number
  name: string
  parse_status: ParseStatus
  supported_for_qa: boolean
  error_message?: string
}

export const MAX_UPLOAD_MB = 20

/** 与后端 app/rag/parsers.py 的 SUPPORTED_EXTENSIONS 对齐（阶段 2 不含图片）。 */
export const SUPPORTED_UPLOAD_EXTENSIONS = [
  '.pdf',
  '.docx',
  '.txt',
  '.md',
  '.markdown',
  '.rst',
  '.log',
  '.csv',
  '.json',
  '.yaml',
  '.yml',
  '.toml',
  '.ini',
  '.xml',
  '.html',
  '.htm',
  '.css',
  '.scss',
  '.less',
  '.js',
  '.jsx',
  '.ts',
  '.tsx',
  '.vue',
  '.py',
  '.java',
  '.c',
  '.h',
  '.cpp',
  '.hpp',
  '.cs',
  '.go',
  '.rs',
  '.rb',
  '.php',
  '.swift',
  '.kt',
  '.kts',
  '.scala',
  '.sh',
  '.bash',
  '.sql',
  '.tex',
] as const

export function parseStatusLabel(status: ParseStatus): string {
  switch (status) {
    case 'ready':
      return '可问答'
    case 'failed':
      return '解析失败'
    case 'unsupported':
      return '暂不支持问答'
    case 'processing':
      return '解析中'
    default:
      return '等待解析'
  }
}
