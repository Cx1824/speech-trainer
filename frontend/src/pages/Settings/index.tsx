import { useEffect, useState } from 'react'
import { Card, Form, Input, Select, Button, Tabs, Alert, Space, message } from 'antd'
import { apiService } from '@/services/api'
import type { ApiConfigOut, ApiConfigIn, ProviderConfigIn, ProviderStatus } from '@/types/api'
import VoiceCalibration from '@/components/VoiceCalibration'

const LLM_PROVIDERS = [
  { value: 'custom', label: '自定义（OpenAI 兼容协议）' },
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'tongyi', label: '通义千问' },
  { value: 'zhipu', label: '智谱 GLM' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic Claude' },
]

const ASR_PROVIDERS = [
  { value: 'custom', label: '自定义' },
  { value: 'tencent', label: '腾讯云 ASR' },
  { value: 'xfyun', label: '讯飞流式 ASR' },
  { value: 'aliyun', label: '阿里云实时识别' },
  { value: 'whisper', label: 'Whisper API' },
]

const TTS_PROVIDERS = [
  { value: 'edge', label: 'Edge TTS（免费，无需 API Key）' },
  { value: 'cosyvoice', label: '阿里云 CosyVoice（专业合成音色）' },
  { value: 'qwen_audio', label: '阿里云 Qwen-Audio' },
  { value: 'aliyun', label: '阿里云 TTS' },
  { value: 'custom', label: '自定义（OpenAI 兼容）' },
  { value: 'minimax', label: 'MiniMax' },
  { value: 'tencent', label: '腾讯云 TTS' },
  { value: 'xfyun', label: '讯飞语音合成' },
  { value: 'openai', label: 'OpenAI TTS' },
]

interface FormValues extends ProviderConfigIn {
  provider: string
}

function toFormValues(p: ProviderStatus): FormValues {
  return {
    provider: p.provider,
    base_url: p.base_url,
    api_key: '',
    model: p.model,
  }
}

interface ProviderFormProps {
  kind: 'llm' | 'asr' | 'tts'
  status: ProviderStatus
  providers: typeof LLM_PROVIDERS
  modelLabel: string
  onSave: (kind: 'llm' | 'asr' | 'tts', values: FormValues) => Promise<void>
  saving: boolean
}

function ProviderForm({ kind, status, providers, modelLabel, onSave, saving }: ProviderFormProps) {
  const [form] = Form.useForm<FormValues>()
  const [testing, setTesting] = useState(false)

  useEffect(() => {
    form.setFieldsValue(toFormValues(status))
  }, [form, status])

  const handleTest = async () => {
    setTesting(true)
    try {
      const r = await apiService.testProvider(kind)
      if (r.ok) message.success('连通正常')
      else message.error(r.message)
    } catch (e: any) {
      message.error(`测试失败：${e.message}`)
    } finally {
      setTesting(false)
    }
  }

  return (
    <Form form={form} layout="vertical" onFinish={(v) => onSave(kind, v)}>
      {status.has_key && (
        <Alert
          type="success"
          showIcon
          message="API Key 已配置（留空保存表示不修改）"
          style={{ marginBottom: 16 }}
        />
      )}
      <Form.Item label="厂商" name="provider" rules={[{ required: true }]}>
        <Select options={providers} />
      </Form.Item>
      <Form.Item label="Base URL" name="base_url">
        <Input placeholder={kind === 'tts' && status.provider === 'qwen_audio' ? '留空使用默认：qwen-audio-3.0-realtime-flash' : kind === 'tts' && status.provider === 'cosyvoice' ? '留空使用默认：cosyvoice-v3-flash' : 'https://api.example.com/v1'} />
      </Form.Item>
      <Form.Item label="API Key" name="api_key">
        <Input.Password placeholder="sk-..." autoComplete="new-password" />
      </Form.Item>
      {kind !== 'llm' && (
        <Form.Item label="API Secret（部分厂商需要）" name="api_secret">
          <Input.Password autoComplete="new-password" />
        </Form.Item>
      )}
      <Form.Item label={modelLabel} name="model">
        {kind === 'tts' && status.provider === 'edge' ? (
          <Select
            placeholder="选择音色"
            allowClear
            options={[
              { value: 'zh-CN-YunjianNeural', label: '云健（浑厚男声·面试官感）' },
              { value: 'zh-CN-YunxiNeural', label: '云希（年轻男声·沉稳）' },
              { value: 'zh-CN-YunyangNeural', label: '云扬（新闻男声·正式）' },
              { value: 'zh-CN-XiaoxiaoNeural', label: '晓晓（女声·亲切）' },
              { value: 'zh-CN-XiaoyiNeural', label: '晓伊（女声·温柔）' },
            ]}
          />
        ) : (
        <Input
          placeholder={
            kind === 'llm' ? 'deepseek-chat'
            : kind === 'tts' && status.provider === 'qwen_audio' ? '音色名（如 longanqian）'
            : kind === 'tts' && status.provider === 'cosyvoice' ? '音色名（如 longcheng_v3）'
            : kind === 'tts' ? 'voice_id'
            : 'model_id'
          }
        />
        )}
      </Form.Item>
      <Space>
        <Button type="primary" htmlType="submit" loading={saving}>
          保存
        </Button>
        <Button onClick={handleTest} loading={testing}>
          测试连通性
        </Button>
      </Space>
    </Form>
  )
}

export default function Settings() {
  const [data, setData] = useState<ApiConfigOut | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const d = await apiService.getConfig()
      setData(d)
    } catch (e: any) {
      message.error(`加载配置失败：${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const handleSave = async (kind: 'llm' | 'asr' | 'tts', values: FormValues) => {
    setSaving(true)
    try {
      const payload: ProviderConfigIn = { provider: values.provider }
      if (values.base_url) payload.base_url = values.base_url
      if (values.api_key) payload.api_key = values.api_key
      if (values.api_secret) payload.api_secret = values.api_secret
      if (values.model) payload.model = values.model
      const req: ApiConfigIn = { [kind]: payload }
      const updated = await apiService.updateConfig(req)
      setData(updated)
      message.success('已保存')
    } catch (e: any) {
      message.error(`保存失败：${e.message}`)
    } finally {
      setSaving(false)
    }
  }

  if (loading || !data) return <div style={{ textAlign: 'center', padding: 48 }}>加载中...</div>

  return (
    <div style={{ maxWidth: 720, margin: '0 auto' }}>
      <VoiceCalibration />
      <Card title="AI 模型配置">
        <Tabs
          items={[
            {
              key: 'llm',
              label: 'LLM 大模型',
              children: (
                <ProviderForm
                  kind="llm"
                  status={data.llm}
                  providers={LLM_PROVIDERS}
                  modelLabel="模型名"
                  onSave={handleSave}
                  saving={saving}
                />
              ),
            },
            {
              key: 'asr',
              label: 'ASR 语音识别',
              children: (
                <ProviderForm
                  kind="asr"
                  status={data.asr}
                  providers={ASR_PROVIDERS}
                  modelLabel="引擎/模型"
                  onSave={handleSave}
                  saving={saving}
                />
              ),
            },
            {
              key: 'tts',
              label: 'TTS 语音合成',
              children: (
                <ProviderForm
                  kind="tts"
                  status={data.tts}
                  providers={TTS_PROVIDERS}
                  modelLabel="音色 ID"
                  onSave={handleSave}
                  saving={saving}
                />
              ),
            },
          ]}
        />
      </Card>
    </div>
  )
}
