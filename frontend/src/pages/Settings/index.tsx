import { useEffect, useState } from 'react'
import { Alert, Button, Form, Input, Select, Spin, Tabs, message } from 'antd'
import {
  AudioOutlined,
  CheckCircleFilled,
  CloudServerOutlined,
  ReloadOutlined,
  RobotOutlined,
  SoundOutlined,
} from '@ant-design/icons'
import { apiService } from '@/services/api'
import type { ApiConfigOut, ApiConfigIn, ProviderConfigIn, ProviderStatus } from '@/types/api'
import VoiceCalibration from '@/components/VoiceCalibration'
import './Settings.css'

const LLM_PROVIDERS = [
  { value: 'custom', label: '自定义（OpenAI 兼容协议）' },
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'tongyi', label: '通义千问' },
  { value: 'zhipu', label: '智谱 GLM' },
  { value: 'openai', label: 'OpenAI' },
]

const ASR_PROVIDERS = [
  { value: 'sherpa_onnx', label: '本地语音识别（免费，推荐）' },
  { value: 'dashscope', label: '阿里云实时识别' },
]

const TTS_PROVIDERS = [
  { value: 'edge', label: 'Edge TTS（免费，无需 API Key）' },
  { value: 'cosyvoice', label: '阿里云 CosyVoice（专业合成音色）' },
  { value: 'qwen_audio', label: '阿里云 Qwen-Audio' },
  { value: 'aliyun', label: '阿里云 TTS' },
  { value: 'custom', label: '自定义（OpenAI 兼容）' },
  { value: 'minimax', label: 'MiniMax' },
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
  providers: Array<{ value: string; label: string }>
  modelLabel: string
  onSave: (kind: 'llm' | 'asr' | 'tts', values: FormValues) => Promise<void>
  saving: boolean
}

function ProviderForm({ kind, status, providers, modelLabel, onSave, saving }: ProviderFormProps) {
  const [form] = Form.useForm<FormValues>()
  const [testing, setTesting] = useState(false)
  const selectedProvider = Form.useWatch('provider', form) ?? status.provider
  const isLocalAsr = kind === 'asr' && selectedProvider === 'sherpa_onnx'

  const providerCopy = {
    llm: {
      title: '内容分析模型',
      description: '负责结构、观点与场景化建议。报告中的语义评价来自这里。',
    },
    asr: {
      title: '实时语音识别',
      description: '把训练音频转成文本，影响口癖、重复与内容分析的输入质量。',
    },
    tts: {
      title: '主持语音合成',
      description: '负责面试官、评审与主持人的语音播报，可使用免密钥的 Edge TTS。',
    },
  }[kind]
  const isReady = status.ready

  useEffect(() => {
    form.setFieldsValue(toFormValues(status))
  }, [form, status])

  const handleTest = async () => {
    setTesting(true)
    try {
      const r = await apiService.testProvider(kind)
      if (r.ok) message.success('连通正常')
      else message.error(r.message)
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error)
      message.error(`测试失败：${detail}`)
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="provider-pane">
      <div className="provider-pane-heading">
        <div>
          <h3>{providerCopy.title}</h3>
          <p>{providerCopy.description}</p>
        </div>
        <span className={`provider-ready-state ${isReady ? 'is-ready' : ''}`}>
          {isReady && <CheckCircleFilled aria-hidden="true" />}
          {isReady ? '已配置' : '待配置'}
        </span>
      </div>

      {status.has_key && !isLocalAsr && (
        <div className="provider-key-notice">
          密钥已安全保存。密钥框留空会保留原值；切换厂商时请输入新密钥。
        </div>
      )}

      <Form form={form} layout="vertical" onFinish={(values) => onSave(kind, values)}>
        {isLocalAsr && (
          <Alert
            className="provider-local-notice"
            type="info"
            showIcon
            message="在这台电脑上识别"
            description={`${status.status_message}。无需 API Key；阿里云选项仍保留，可随时切换。`}
          />
        )}
        <div className="provider-form-grid">
          <Form.Item label="服务厂商" name="provider" rules={[{ required: true, message: '请选择服务厂商' }]}>
            <Select
              options={providers}
              size="large"
              onChange={(provider) => {
                if (kind === 'asr' && provider === 'sherpa_onnx') {
                  form.setFieldsValue({ base_url: '', model: '' })
                }
              }}
            />
          </Form.Item>
          {!isLocalAsr && <Form.Item label={modelLabel} name="model">
            {kind === 'tts' && selectedProvider === 'edge' ? (
              <Select
                size="large"
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
                size="large"
                placeholder={
                  kind === 'llm' ? 'deepseek-v4-pro'
                  : kind === 'tts' && selectedProvider === 'qwen_audio' ? '音色名（如 longanqian）'
                  : kind === 'tts' && selectedProvider === 'cosyvoice' ? '音色名（如 longcheng_v3）'
                  : kind === 'tts' ? 'voice_id'
                  : 'model_id'
                }
              />
            )}
          </Form.Item>}
          {!isLocalAsr && <Form.Item className="provider-form-wide" label="服务地址" name="base_url" extra="使用厂商默认地址时可留空。只有自定义网关或代理才需要填写。">
            <Input
              size="large"
              placeholder={kind === 'tts' && selectedProvider === 'qwen_audio'
                ? '留空使用 Qwen-Audio 默认服务'
                : kind === 'tts' && selectedProvider === 'cosyvoice'
                  ? '留空使用 CosyVoice 默认服务'
                  : 'https://api.example.com/v1'}
            />
          </Form.Item>}
          {!isLocalAsr && <Form.Item label="API Key" name="api_key">
            <Input.Password
              size="large"
              placeholder={status.has_key ? '留空保留现有密钥' : '输入服务密钥'}
              autoComplete="new-password"
            />
          </Form.Item>}
          {kind !== 'llm' && !isLocalAsr && (
            <Form.Item label="API Secret（可选）" name="api_secret" extra="仅部分厂商需要。">
              <Input.Password size="large" autoComplete="new-password" placeholder="按厂商要求填写" />
            </Form.Item>
          )}
        </div>
        <div className="provider-form-actions">
          <Button type="primary" htmlType="submit" loading={saving}>保存当前服务</Button>
          <Button onClick={handleTest} loading={testing}>测试已保存配置</Button>
        </div>
      </Form>
    </div>
  )
}

export default function Settings() {
  const [data, setData] = useState<ApiConfigOut | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [loadError, setLoadError] = useState('')

  const load = async () => {
    setLoading(true)
    setLoadError('')
    try {
      const d = await apiService.getConfig()
      setData(d)
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error)
      setLoadError(detail)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    document.body.classList.add('is-operate-surface')
    void load()
    return () => document.body.classList.remove('is-operate-surface')
  }, [])

  const handleSave = async (kind: 'llm' | 'asr' | 'tts', values: FormValues) => {
    setSaving(true)
    try {
      const payload: ProviderConfigIn = {
        provider: values.provider,
        base_url: values.base_url ?? '',
        model: values.model ?? '',
      }
      if (values.api_key) payload.api_key = values.api_key
      if (values.api_secret) payload.api_secret = values.api_secret
      const req: ApiConfigIn = { [kind]: payload }
      const updated = await apiService.updateConfig(req)
      setData(updated)
      message.success('已保存')
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error)
      message.error(`保存失败：${detail}`)
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <main className="operate-state">
        <div className="operate-state-inner">
          <Spin size="large" />
          <h1>正在读取训练配置</h1>
          <p>正在检查内容分析、语音识别和语音合成服务。</p>
        </div>
      </main>
    )
  }

  if (!data) {
    return (
      <main className="operate-state">
        <div className="operate-state-inner">
          <CloudServerOutlined className="operate-state-icon" />
          <h1>配置暂时无法读取</h1>
          <p>{loadError || '请确认本地后端已启动，然后重试。'}</p>
          <Button type="primary" icon={<ReloadOutlined />} onClick={() => void load()}>重新读取</Button>
        </div>
      </main>
    )
  }

  const readyCount = [
    data.llm.ready,
    data.asr.ready,
    data.tts.ready,
  ].filter(Boolean).length

  return (
    <main className="operate-page settings-page">
      <div className="operate-container">
        <header className="operate-heading settings-heading">
          <div>
            <h1>设置训练引擎</h1>
            <p>集中管理内容分析、实时语音与个人声音基线。配置只保存在你的本地数据库中。</p>
          </div>
          <div className="settings-readiness" aria-label={`已就绪 ${readyCount} 项，共 3 项`}>
            <strong>{readyCount}<span>/3</span></strong>
            <div><b>服务已就绪</b><span>{readyCount === 3 ? '可以开始完整语音训练' : '完成剩余配置后即可开始'}</span></div>
          </div>
        </header>

        <div className="settings-layout">
          <section className="settings-service-panel" aria-labelledby="service-settings-title">
            <div className="settings-section-heading">
              <div>
                <h2 id="service-settings-title">模型与语音服务</h2>
                <p>逐项保存，切换标签不会丢失已保存配置。</p>
              </div>
            </div>
            <Tabs
              className="settings-provider-tabs"
              items={[
                {
                  key: 'llm',
                  label: <span><RobotOutlined />内容分析</span>,
                  children: (
                    <ProviderForm kind="llm" status={data.llm} providers={LLM_PROVIDERS} modelLabel="模型名" onSave={handleSave} saving={saving} />
                  ),
                },
                {
                  key: 'asr',
                  label: <span><AudioOutlined />语音识别</span>,
                  children: (
                    <ProviderForm kind="asr" status={data.asr} providers={ASR_PROVIDERS} modelLabel="引擎 / 模型" onSave={handleSave} saving={saving} />
                  ),
                },
                {
                  key: 'tts',
                  label: <span><SoundOutlined />语音合成</span>,
                  children: (
                    <ProviderForm kind="tts" status={data.tts} providers={TTS_PROVIDERS} modelLabel="音色 / 模型" onSave={handleSave} saving={saving} />
                  ),
                },
              ]}
            />
          </section>

          <aside className="settings-calibration-panel" aria-label="个人声音基线">
            <VoiceCalibration />
            <Alert
              type="info"
              showIcon
              message="评分口径"
              description="声音波动只作为个人变化事实，不会被解释为紧张、自信或其他心理状态。"
            />
          </aside>
        </div>
      </div>
    </main>
  )
}
