/** 与后端 app/api/v1/endpoints/system.py 的 /capabilities 对应。 */

export interface Capabilities {
  text_chat: boolean
  streaming: boolean
  vision: boolean
  tool_calling: boolean
  agent_mode: boolean
  web_search: boolean
  document_qa: boolean
  memory: boolean
}
