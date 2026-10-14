/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * 后端地址。留空则走相对路径 /api/v1/...，由 Vite 开发代理转发到后端，
   * 这样开发时完全不涉及跨域。只有前端和后端不同源部署时才需要填。
   */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
