/**
 * 后端 API 类型定义（与 backend/app/schemas 对齐）。
 */

export interface ProviderStatus {
  provider: string
  base_url: string
  model: string
  has_key: boolean
}

export interface ApiConfigOut {
  llm: ProviderStatus
  asr: ProviderStatus
  tts: ProviderStatus
}

export interface ProviderConfigIn {
  provider: string
  base_url?: string
  api_key?: string
  api_secret?: string
  model?: string
}

export interface ApiConfigIn {
  llm?: ProviderConfigIn
  asr?: ProviderConfigIn
  tts?: ProviderConfigIn
}
