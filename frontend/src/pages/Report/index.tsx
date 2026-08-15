import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Card, Typography, Button, Space, Spin, message, Tag, Progress } from 'antd'
import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons'
import { apiService } from '@/services/api'

const { Title, Paragraph } = Typography

interface ReportData {
  session_id: string
  position: string
  level: string
  overall_score: number
  summary: string
  expression_metrics: {
    speech_rate: number
    speech_rate_level: string
    total_words: number
    filler_total: number
    filler_top: { word: string; count: number }[]
    repetition_rate: number
  }
  emotion_metrics: {
    tension_score: number
    tension_level: string
    confidence_score: number
    confidence_level: string
  }
  content_metrics: Record<string, any>
  suggestions: {
    short_term: string[]
    mid_term: string[]
  }
  dialogues: { seq: number; role: string; stage: string; text: string }[]
}

export default function Report() {
  const { id } = useParams()
  const nav = useNavigate()
  const [report, setReport] = useState<ReportData | null>(null)
  const [loading, setLoading] = useState(true)
  const [progress, setProgress] = useState(10)
  const [retryCount, setRetryCount] = useState(0)

  useEffect(() => {
    if (!id) return
    setLoading(true)
    setProgress(10)
    // 报告生成需调用 LLM 评分，约 10-30 秒，进度条缓解等待焦虑
    const timer = setInterval(() => {
      setProgress((p) => (p < 90 ? p + Math.random() * 8 : p))
    }, 1000)
    apiService.generateReport(id)
      .then((r) => { setProgress(100); setReport(r) })
      .catch((e) => message.error(`生成报告失败：${e.message}`))
      .finally(() => { clearInterval(timer); setLoading(false) })
    return () => clearInterval(timer)
  }, [id, retryCount])

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 80, maxWidth: 480, margin: '0 auto' }}>
        <Spin size="large" />
        <div style={{ fontSize: 16, marginTop: 24, marginBottom: 16 }}>AI 正在分析你的面试表现…</div>
        <Progress percent={Math.round(progress)} status="active" strokeColor="#534ab7" />
        <div style={{ color: '#888', fontSize: 13, marginTop: 12 }}>
          正在调用 LLM 进行内容评分与建议生成，通常需要 10~30 秒，请勿关闭页面
        </div>
      </div>
    )
  }

  if (!report) {
    return (
      <div style={{ textAlign: 'center', padding: 40 }}>
        <Paragraph>报告生成失败（可能是 LLM 配置问题或网络超时）</Paragraph>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => setRetryCount((c) => c + 1)}>重试</Button>
          <Button onClick={() => nav('/')}>返回首页</Button>
        </Space>
      </div>
    )
  }

  const em = report.expression_metrics || {}
  const emo = report.emotion_metrics || {}
  const cm = report.content_metrics || {}
  const sug = report.suggestions || {}

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <Title level={2}>面试训练报告</Title>
        <Space>
          <Button
            icon={<DownloadOutlined />}
            onClick={() => {
              if (!id) return
              window.open(`/api/v1/reports/${id}/pdf`, '_blank')
            }}
          >
            导出 PDF
          </Button>
          <Button onClick={() => nav('/')}>返回首页</Button>
        </Space>
      </div>

      <Card style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 24, alignItems: 'center' }}>
          <div>
            <div style={{ fontSize: 48, fontWeight: 500, color: '#534ab7' }}>
              {report.overall_score}
            </div>
            <div style={{ color: '#888', fontSize: 13 }}>综合评分</div>
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ marginBottom: 8 }}>
              <Tag color="purple">{report.position}</Tag>
              <Tag>{report.level}</Tag>
            </div>
            <Paragraph style={{ margin: 0 }}>{report.summary}</Paragraph>
          </div>
        </div>
      </Card>

      <Card title="表达维度" style={{ marginBottom: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
          <Metric label="语速" value={`${em.speech_rate ?? 0} 字/分`} sub={em.speech_rate_level} />
          <Metric label="总字数" value={`${em.total_words ?? 0}`} />
          <Metric label="口癖次数" value={`${em.filler_total ?? 0}`} tone="warn" />
          <Metric label="用词重复率" value={((em.repetition_rate ?? 0) * 100).toFixed(0) + '%'} />
        </div>
        {em.filler_top && em.filler_top.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <Paragraph type="secondary" style={{ marginBottom: 4 }}>高频口癖词：</Paragraph>
            <Space wrap>
              {em.filler_top.map((f) => (
                <Tag key={f.word} color="orange">{f.word} × {f.count}</Tag>
              ))}
            </Space>
          </div>
        )}
      </Card>

      <Card title="情绪维度" style={{ marginBottom: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <Metric label="紧张度" value={`${emo.tension_score ?? 0}`} sub={emo.tension_level} tone="bad" />
          <Metric label="自信度" value={`${emo.confidence_score ?? 0}`} sub={emo.confidence_level} tone="good" />
        </div>
      </Card>

      {cm.project_familiarity && (
        <Card title="内容维度" style={{ marginBottom: 16 }}>
          <ContentMetric title="项目熟悉度" data={cm.project_familiarity} />
          <ContentMetric title="逻辑性" data={cm.logicality} />
          <ContentMetric title="回答完整度" data={cm.completeness} />
        </Card>
      )}

      <Card title="强化建议" style={{ marginBottom: 16 }}>
        <div style={{ marginBottom: 16 }}>
          <strong>短期改进（1 周内）</strong>
          <ul>
            {(sug.short_term ?? []).map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
        <div>
          <strong>中期方向（1 个月内）</strong>
          <ul>
            {(sug.mid_term ?? []).map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      </Card>

      <Card title="完整对话记录">
        {report.dialogues?.map((d, i) => (
          <div key={i} style={{ marginBottom: 12, padding: 8, background: d.role === 'ai' ? '#f5f5f5' : '#f0f7ff', borderRadius: 4 }}>
            <div style={{ fontWeight: 500, marginBottom: 4 }}>
              {d.role === 'ai' ? '面试官' : '候选人'}
              <span style={{ color: '#888', fontWeight: 400, marginLeft: 8, fontSize: 12 }}>
                · {d.stage}
              </span>
            </div>
            <div>{d.text}</div>
          </div>
        ))}
      </Card>
    </div>
  )
}

function Metric({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: 'good' | 'warn' | 'bad' }) {
  const color = tone === 'good' ? '#1d9e75' : tone === 'warn' ? '#ba7517' : tone === 'bad' ? '#e24b4a' : '#222'
  return (
    <div>
      <div style={{ fontSize: 12, color: '#888' }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 500, color }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: '#888' }}>{sub}</div>}
    </div>
  )
}

function ContentMetric({ title, data }: { title: string; data: any }) {
  if (!data) return null
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: 'flex', gap: 16 }}>
        <div style={{ minWidth: 100 }}>
          <strong>{title}</strong>
          <div style={{ fontSize: 20, color: '#534ab7', fontWeight: 500 }}>{data.score}</div>
        </div>
        <div style={{ flex: 1 }}>{data.feedback}</div>
      </div>
    </div>
  )
}
