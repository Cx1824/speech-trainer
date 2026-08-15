import { useEffect, useState } from 'react'
import { Card, Select, Button, Input, Form, Space, message, Empty, Popconfirm } from 'antd'
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons'

const { TextArea } = Input

const POSITIONS = ['产品经理', '前端工程师', '后端工程师', '数据分析师', '设计师', '运营']

interface Question {
  id?: string
  content: string
  intent: string
  difficulty: string
}

export default function QuestionBank() {
  const [position, setPosition] = useState(POSITIONS[0])
  const [questions, setQuestions] = useState<Question[]>([])
  const [form] = Form.useForm<Question>()

  const load = (p: string) => {
    fetch(`/api/v1/question_bank/${p}`)
      .then((r) => r.json())
      .then((d) => setQuestions(d.questions || []))
  }

  useEffect(() => { load(position) }, [position])

  const save = async () => {
    try {
      const v = await form.validateFields()
      const next = [...questions, { ...v, id: undefined }]
      await fetch(`/api/v1/question_bank/${position}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ questions: next }),
      })
      setQuestions(next)
      form.resetFields()
      message.success('已添加')
    } catch {
      // validation
    }
  }

  const remove = async (idx: number = 0) => {
    const next = questions.filter((_, i) => i !== idx)
    await fetch(`/api/v1/question_bank/${position}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ questions: next }),
    })
    setQuestions(next)
    message.success('已删除')
  }

  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <Card title="题库管理" extra={
        <Select value={position} onChange={setPosition} style={{ width: 180 }} options={POSITIONS.map((p) => ({ value: p, label: p }))} />
      }>
        <Form form={form} layout="inline" style={{ marginBottom: 16 }}>
          <Form.Item name="content" rules={[{ required: true, message: '请输入题目' }]} style={{ flex: 1 }}>
            <TextArea placeholder="题目内容" autoSize={{ minRows: 1, maxRows: 3 }} />
          </Form.Item>
          <Form.Item name="intent">
            <Input placeholder="考察点" style={{ width: 150 }} />
          </Form.Item>
          <Form.Item name="difficulty" initialValue="medium">
            <Select style={{ width: 100 }} options={[
              { value: 'easy', label: '简单' },
              { value: 'medium', label: '中等' },
              { value: 'hard', label: '困难' },
            ]} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" icon={<PlusOutlined />} onClick={save}>添加</Button>
          </Form.Item>
        </Form>

        {questions.length === 0 ? (
          <Empty description={`「${position}」岗位暂未录入题库，将由 LLM 自由生成`} />
        ) : (
          questions.map((q, i) => (
            <Card key={i} size="small" style={{ marginBottom: 8 }} title={
              <Space>
                <span>{q.content}</span>
              </Space>
            } extra={
              <Popconfirm title="确认删除？" onConfirm={() => remove(i)}>
                <Button danger size="small" icon={<DeleteOutlined />} />
              </Popconfirm>
            }>
              {q.intent && <span style={{ color: '#888', marginRight: 16 }}>考察：{q.intent}</span>}
              <span style={{ color: '#888' }}>难度：{q.difficulty}</span>
            </Card>
          ))
        )}
      </Card>
    </div>
  )
}
