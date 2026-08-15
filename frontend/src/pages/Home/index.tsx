import { Button, Card, Col, Row, Tag, Typography } from 'antd'
import { SoundOutlined, DownOutlined, UpOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { apiService } from '@/services/api'
import type { ScenarioOut } from '@/types/interview'
import VoiceCalibration from '@/components/VoiceCalibration'

const { Title, Paragraph, Text } = Typography

/** 场景卡片图标与主色（静态配置，与后端 scenario key 对齐）。 */
const CARD_META: Record<string, { icon: string; color: string }> = {
  interview: { icon: '🎯', color: '#1677ff' },
  presentation: { icon: '📊', color: '#722ed1' },
  speech: { icon: '🎤', color: '#13c2c2' },
}

export default function Home() {
  const nav = useNavigate()
  const [scenarios, setScenarios] = useState<ScenarioOut[]>([])
  const [calibrated, setCalibrated] = useState<boolean | null>(null)  // null=查询中
  const [calibOpen, setCalibOpen] = useState(false)                   // 校准卡片展开

  useEffect(() => {
    apiService
      .listScenarios()
      .then((r) => setScenarios(r.scenarios))
      .catch(() => {
        // 后端不可达时回落到静态三场景
        setScenarios([
          { key: 'interview', name: '模拟面试', role_name: '面试官', description: 'AI 语音面试官全流程模拟。', needs_resume: true, needs_material: false, timed: false },
          { key: 'presentation', name: '工作汇报', role_name: '评审', description: '向上汇报/述职模拟。', needs_resume: false, needs_material: true, timed: true },
          { key: 'speech', name: '演讲训练', role_name: '主持人', description: '限时演讲实战训练。', needs_resume: false, needs_material: true, timed: true },
        ])
      })
    // 校准状态（null 时显示引导）
    apiService
      .getVoiceCalibration()
      .then((r) => setCalibrated(r.calibrated))
      .catch(() => setCalibrated(null))
  }, [])

  return (
    <div style={{ maxWidth: 1080, margin: '0 auto' }}>
      <Title level={2}>表达能力训练平台</Title>
      <Paragraph type="secondary">
        实时语音转写 + 表达多维分析 + 场景化专业报告。收音、实时反馈、报告核心链路三场景共用，仅训练侧重不同。
      </Paragraph>

      {/* 声音校准引导：未校准时醒目提示；已校准提供管理入口 */}
      {calibrated === false && (
        <Card
          size="small"
          style={{ marginBottom: 16, borderColor: '#1677ff', background: '#f0f7ff' }}
          styles={{ body: { padding: '12px 16px' } }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <SoundOutlined style={{ fontSize: 20, color: '#1677ff' }} />
            <div style={{ flex: 1, minWidth: 260 }}>
              <Text strong>先花 30 秒做个声音校准</Text>
              <div style={{ fontSize: 13, color: '#666' }}>
                朗读一小段文字，系统会记住你的语速、音调与停顿习惯——之后训练中的「紧张度」按你自己的基准评估，准得多。换人使用时重新校准即可。
              </div>
            </div>
            <Button type="primary" onClick={() => setCalibOpen(true)}>
              开始校准
            </Button>
          </div>
        </Card>
      )}
      {calibrated === true && (
        <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'flex-end' }}>
          <Button
            size="small"
            type="text"
            icon={<SoundOutlined />}
            onClick={() => setCalibOpen((v) => !v)}
          >
            声音校准（已就绪）{calibOpen ? <UpOutlined /> : <DownOutlined />}
          </Button>
        </div>
      )}
      {calibOpen && (
        <VoiceCalibration
          onChanged={(ok) => {
            setCalibrated(ok)
            if (ok) setCalibOpen(false)  // 校准成功收起卡片，回到引导收起态
          }}
        />
      )}

      <Row gutter={[16, 16]}>
        {scenarios.map((s) => {
          const meta = CARD_META[s.key] ?? { icon: '⭐', color: '#595959' }
          return (
            <Col xs={24} sm={12} md={8} key={s.key}>
              <Card
                hoverable
                style={{ height: '100%', borderTop: `3px solid ${meta.color}` }}
                onClick={() => nav(`/interview?scenario=${s.key}`)}
              >
                <div style={{ fontSize: 32, marginBottom: 8 }}>{meta.icon}</div>
                <Title level={4} style={{ marginBottom: 4 }}>
                  {s.name}
                </Title>
                <Paragraph type="secondary" style={{ minHeight: 44 }}>
                  {s.description}
                </Paragraph>
                <div style={{ marginBottom: 12 }}>
                  <Tag color={meta.color}>AI{s.role_name}</Tag>
                  {s.timed && <Tag color="orange">限时+计时</Tag>}
                  {s.needs_material && <Tag>可传材料</Tag>}
                  {s.needs_resume && <Tag>简历</Tag>}
                </div>
                <Button type="primary" block onClick={() => nav(`/interview?scenario=${s.key}`)}>
                  开始训练
                </Button>
              </Card>
            </Col>
          )
        })}
      </Row>

      <Paragraph type="secondary" style={{ marginTop: 24 }}>
        <Text type="secondary">训练记录可在对应场景的报告页查看历史会话。</Text>
      </Paragraph>
    </div>
  )
}
