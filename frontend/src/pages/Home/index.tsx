import { Card, Button, Typography } from 'antd'
import { useNavigate } from 'react-router-dom'

const { Title, Paragraph } = Typography

export default function Home() {
  const nav = useNavigate()
  return (
    <div style={{ maxWidth: 960, margin: '0 auto' }}>
      <Title level={2}>表达能力训练平台</Title>
      <Paragraph type="secondary">通过实时语音转写、AI 对练、情绪分析，系统性提升面试表现。</Paragraph>
      <Card title="面试训练" style={{ marginBottom: 16 }}>
        <Paragraph>AI 语音面试官，基于简历深度提问，完整模拟面试流程。</Paragraph>
        <Button type="primary" onClick={() => nav('/interview')}>开始面试</Button>
      </Card>
    </div>
  )
}
