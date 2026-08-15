import { Card, Button, Typography, Row, Col, Tag } from 'antd'
import { useNavigate } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { apiService } from '@/services/api'
import type { ScenarioOut } from '@/types/interview'

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
  }, [])

  return (
    <div style={{ maxWidth: 1080, margin: '0 auto' }}>
      <Title level={2}>表达能力训练平台</Title>
      <Paragraph type="secondary">
        实时语音转写 + 表达多维分析 + 场景化专业报告。收音、实时反馈、报告核心链路三场景共用，仅训练侧重不同。
      </Paragraph>

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
