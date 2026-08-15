import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Card, Form, Select, Upload, Button, Alert, Typography, Space, Tag, message, Input, Radio,
} from 'antd'
import { UploadOutlined } from '@ant-design/icons'
import { apiService } from '@/services/api'
import { useVoiceSession } from '@/hooks/useVoiceSession'
import EmotionIndicator from '@/components/EmotionIndicator'
import type { EmotionData } from '@/components/EmotionIndicator'
import { type InterviewStyle, SCENARIOS } from '@/types/interview'

const { Title, Paragraph, Text } = Typography

const LEVELS = ['实习', '初级', '中级', '高级', '资深']

/** 时长选项（分钟）：限时场景自选 */
const DURATION_OPTIONS = [1, 2, 3, 5, 8, 10, 15, 20].map((m) => ({ value: m, label: `${m} 分钟` }))

/** 面试档案（本地类型） */
interface ProfileItem {
  id: string
  name: string
  position?: string
  level?: string
  style?: string
  company?: string
  jd_url?: string
  jd_content?: string
  resume_parsed_json?: string
  has_resume?: boolean
  resume_parsed?: { position_guess?: string; level_guess?: string } | null
}

export default function Interview() {
  const nav = useNavigate()
  const [searchParams] = useSearchParams()
  // 场景：interview（默认）/ presentation / speech
  const scenario = searchParams.get('scenario') || 'interview'
  const isInterview = scenario === 'interview'
  const scenarioMeta = SCENARIOS[scenario] ?? SCENARIOS.interview
  const isTimed = scenario === 'presentation' || scenario === 'speech'

  const [phase, setPhase] = useState<'config' | 'running'>('config')
  const [sid, setSid] = useState<string | null>(null)
  const [form] = Form.useForm()
  const [busy, setBusy] = useState(false)
  const [styles, setStyles] = useState<InterviewStyle[]>([])
  const [resumeParsed, setResumeParsed] = useState<{ position_guess?: string; level_guess?: string } | null>(null)
  const [jdUrl, setJdUrl] = useState('')
  const [fetchingJd, setFetchingJd] = useState(false)
  // 简历已上传解析的会话 sid（复用避免重复解析）
  const [resumeSid, setResumeSid] = useState<string | null>(null)
  // 材料上传状态（汇报/演讲）
  const [materialFile, setMaterialFile] = useState<File | null>(null)
  const [materialUploaded, setMaterialUploaded] = useState(false)
  // 档案
  const [profiles, setProfiles] = useState<ProfileItem[]>([])
  const [saveName, setSaveName] = useState('')
  const [savingProfile, setSavingProfile] = useState(false)

  const [emotion, setEmotion] = useState<EmotionData | null>(null)
  // 限时计时器
  const [startedAt, setStartedAt] = useState<Date | null>(null)
  const [durationLimit, setDurationLimit] = useState(0)
  const [, setTick] = useState(0)  // 秒级刷新计时器显示
  useEffect(() => {
    if (phase !== 'running') return
    const t = setInterval(() => setTick((x) => x + 1), 1000)
    return () => clearInterval(t)
  }, [phase])
  const elapsedSecLocal = startedAt ? Math.max(0, Math.floor((Date.now() - startedAt.getTime()) / 1000)) : 0
  const timeOverLocal = isTimed && durationLimit > 0 && elapsedSecLocal >= durationLimit * 60
  // 面试模式：true=语音对话（自动 VAD 提交）/ false=手动（按钮提交）。
  // 两种模式统一走 /ws/voice 语音链路（同一套字幕/分析/播报），仅提交方式不同。
  const [voiceMode, setVoiceMode] = useState(true)
  const voice = useVoiceSession(phase === 'running' ? sid : null, (p) => {
    setEmotion({
      tensionScore: p.tension_score as number,
      tensionLevel: p.tension_level as string,
      confidenceScore: p.confidence_score as number,
      confidenceLevel: p.confidence_level as string,
    })
  }, { manual: !voiceMode || isTimed, autoResume: isTimed })  // 限时：持续采集+按钮推进；手动面试：挂起+按钮恢复

  // 加载风格列表 + 档案列表（仅面试场景加载档案）
  useEffect(() => {
    if (isInterview) {
      apiService.listStyles().then((r) => setStyles(r.styles)).catch(() => {})
      loadProfiles()
    }
  }, [scenario])

  const loadProfiles = () => {
    apiService.listProfiles().then(setProfiles).catch(() => {})
  }

  // 提前上传简历解析岗位（在创建会话之前）
  const handleResumeUpload = async (file: File) => {
    setBusy(true)
    try {
      // 先创建一个临时会话用于上传简历
      const session = await apiService.createInterview({ style: 'professional' })
      message.info('正在解析简历...')
      const updated = await apiService.uploadResume(session.id, file)
      if (updated.resume_parsed?.position_guess) {
        // 仅在用户未填时填入
        const cur = form.getFieldValue('position')
        if (!cur) {
          form.setFieldsValue({ position: updated.resume_parsed.position_guess })
        }
        const curLevel = form.getFieldValue('level')
        if (!curLevel) {
          form.setFieldsValue({ level: updated.resume_parsed.level_guess || '中级' })
        }
        setResumeParsed({
          position_guess: updated.resume_parsed.position_guess,
          level_guess: updated.resume_parsed.level_guess,
        })
        message.success(`简历解析完成（推荐岗位：${updated.resume_parsed.position_guess}）`)
      } else {
        message.success('简历解析完成')
      }
      // 记录简历会话 sid
      setResumeSid(session.id)
    } catch (e: any) {
      message.error(`简历解析失败：${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  // 保存当前配置为档案
  const saveProfile = async () => {
    const values = form.getFieldsValue()
    if (!saveName.trim()) {
      message.warning('请输入档案名')
      return
    }
    setSavingProfile(true)
    try {
      // 若已上传简历，取会话里的简历文件与解析结果
      let resume_file = ''
      let resume_parsed_json = ''
      if (resumeSid) {
        const s = await apiService.getInterview(resumeSid)
        resume_file = (s.resume_parsed as any)?.resume_file || ''
        resume_parsed_json = JSON.stringify(s.resume_parsed || {})
      }
      await apiService.createProfile({
        name: saveName.trim(),
        position: values.position || '',
        level: values.level || '',
        style: values.style || 'professional',
        company: values.company || '',
        jd_url: jdUrl,
        jd_content: values.jd_content || '',
        resume_file,
        resume_parsed_json,
      })
      message.success('档案已保存')
      setSaveName('')
      loadProfiles()
    } catch (e: any) {
      message.error(`保存失败：${e.message}`)
    } finally {
      setSavingProfile(false)
    }
  }

  // 加载档案到表单
  const applyProfile = async (p: ProfileItem) => {
    form.setFieldsValue({
      position: p.position,
      level: p.level || undefined,
      style: p.style,
      company: p.company,
      jd_content: p.jd_content,
    })
    setJdUrl(p.jd_url || '')
    setResumeParsed(
      p.resume_parsed
        ? { position_guess: p.resume_parsed.position_guess, level_guess: p.resume_parsed.level_guess }
        : null
    )
    // 有简历的档案：创建新会话挂上简历，便于直接开练
    if (p.resume_parsed_json && p.has_resume) {
      try {
        setBusy(true)
        const session = await apiService.createInterview({
          position: p.position,
          level: p.level,
          style: p.style,
          company: p.company,
          jd_url: p.jd_url,
          jd_content: p.jd_content,
        })
        // 直接把档案里的解析结果同步到新会话（用 update 不行，简历要走 upload；
        // 简化：直接复用 resume_parsed 信息开练，重新上传时才再解析）
        setResumeSid(session.id)
        message.success(`已加载档案「${p.name}」`)
      } finally {
        setBusy(false)
      }
    } else {
      setResumeSid(null)
      message.success(`已加载档案「${p.name}」（无简历）`)
    }
  }

  const deleteProfile = async (pid: string) => {
    try {
      await apiService.deleteProfile(pid)
      message.success('已删除')
      loadProfiles()
    } catch (e: any) {
      message.error(`删除失败：${e.message}`)
    }
  }

  // 抓取 JD 链接
  const handleFetchJd = async () => {
    if (!jdUrl.trim()) {
      message.warning('请输入 JD 链接')
      return
    }
    setFetchingJd(true)
    try {
      const r = await apiService.fetchJD(jdUrl.trim())
      if (!r.success) {
        message.error(`抓取失败：${r.message}，请手动粘贴 JD`)
        return
      }
      // 填入表单
      form.setFieldsValue({
        jd_content: r.content,
        company: r.company || form.getFieldValue('company'),
      })
      // 若岗位为空，尝试用 title 提示（不强行填，让用户决定）
      const curPos = form.getFieldValue('position')
      if (!curPos && r.title) {
        message.success(`已抓取：${r.title}（可在上方岗位栏填写）`)
      } else {
        message.success(`已抓取：${r.title || 'JD'}`)
      }
    } catch (e: any) {
      message.error(`抓取失败：${e.message}`)
    } finally {
      setFetchingJd(false)
    }
  }

  // 上传材料（汇报/演讲）：需先创建会话
  const ensureSession = async (): Promise<string> => {
    if (resumeSid) return resumeSid
    const session = await apiService.createInterview({ scenario, style: 'professional' })
    setResumeSid(session.id)
    return session.id
  }

  const handleMaterialUpload = async (file: File) => {
    setBusy(true)
    try {
      const sessionId = await ensureSession()
      message.info('正在解析材料...')
      await apiService.uploadMaterial(sessionId, file)
      setMaterialFile(file)
      setMaterialUploaded(true)
      message.success('材料上传成功，AI 将基于材料提问/点评')
    } catch (e: any) {
      message.error(`材料上传失败：${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  // 配置阶段开始流程
  const startFlow = async () => {
    const values = await form.validateFields()
    setBusy(true)
    try {
      // 复用上传简历/材料时创建的 sid，否则新建
      let sessionId: string
      const commonFields = {
        position: values.position,
        company: values.company,
        jd_url: jdUrl,
        jd_content: values.jd_content,
      }
      if (resumeSid) {
        sessionId = resumeSid
        await apiService.updateInterview(sessionId, {
          ...commonFields,
          ...(isInterview ? { level: values.level, style: values.style } : {}),
          ...(isTimed ? { duration_limit: values.duration_limit ?? 0 } : {}),
        })
      } else {
        const session = await apiService.createInterview({
          ...commonFields,
          scenario,
          ...(isInterview ? { level: values.level, style: values.style } : {}),
          ...(isTimed ? { duration_limit: values.duration_limit ?? 0 } : {}),
        })
        sessionId = session.id
      }
      await apiService.startInterview(sessionId)
      setSid(sessionId)
      setPhase('running')
      message.success(`${scenarioMeta.name}开始`)
      // 两种模式统一走语音链路，由下方 voiceStartRef effect 在 sid 生效后自动启动
    } catch (e: any) {
      message.error(`启动失败：${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  // 面试自动启动（自动/手动模式共用 /ws/voice 链路）：等 sid 就绪 + phase=running 后拉起
  const voiceStartRef = useRef(false)
  const endedHandledRef = useRef(false)
  useEffect(() => {
    if (phase !== 'running' || !sid || voiceStartRef.current) return
    voiceStartRef.current = true
    endedHandledRef.current = false
    // 计时基准：会话 started_at（后端 start 时写入），本地时钟偏差可接受
    apiService.getInterview(sid).then((s) => {
      if (s.started_at) setStartedAt(new Date(s.started_at))
      if (s.duration_limit) setDurationLimit(s.duration_limit)
    }).catch(() => {})
    ;(async () => {
      try {
        await voice.start(sid)
        voice.requestFirstQuestion()
      } catch (e: any) {
        message.error(`语音会话启动失败：${e?.message || e}（检查麦克风权限）`)
      }
    })()
  }, [phase, sid])

  // 到点自动收尾：time_up 或本地倒计时归零 → 自动 finishStage（一次性）
  const autoFinishedRef = useRef(false)
  useEffect(() => {
    if (phase !== 'running' || !isTimed || autoFinishedRef.current) return
    if (voice.state.timeUp || timeOverLocal) {
      autoFinishedRef.current = true
      message.warning('时间到，自动进入下一环节')
      voice.finishStage()
    }
  }, [voice.state.timeUp, timeOverLocal, phase])

  // 监听 ANALYSIS_UPDATE 消息 - 已经在前面通过 ws.subscribe 处理

  // ===== 配置阶段 UI =====
  if (phase === 'config') {
    return (
      <div style={{ maxWidth: 760, margin: '0 auto' }}>
        <Title level={2}>{scenarioMeta.name}配置</Title>

        {isInterview && profiles.length > 0 && (
          <Card title="我的档案（点击一键开练）" style={{ marginBottom: 16 }} size="small">
            {profiles.map((p) => (
              <div
                key={p.id}
                style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  padding: '8px 12px', border: '1px solid #e5e5e5', borderRadius: 6,
                  marginBottom: 8, cursor: 'pointer',
                }}
                onClick={() => applyProfile(p)}
              >
                <div>
                  <Text strong>{p.name}</Text>
                  <div style={{ fontSize: 12, color: '#888' }}>
                    {p.position || '未指定岗位'}
                    {p.company ? ` · ${p.company}` : ''}
                    {p.has_resume ? ' · 含简历' : ''}
                  </div>
                </div>
                <Button
                  danger size="small" type="text"
                  onClick={(e) => { e.stopPropagation(); deleteProfile(p.id) }}
                >
                  删除
                </Button>
              </div>
            ))}
          </Card>
        )}

        <Card>
          <Form
            form={form}
            layout="vertical"
            initialValues={{
              position: '',
              level: '中级',
              style: 'professional',
              company: '',
              jd_content: '',
              duration_limit: isTimed ? 5 : 0,
            }}
          >
            {isInterview ? (
              <>
                <Form.Item
                  label="上传简历（选填，自动识别岗位）"
                  extra={resumeParsed ? (
                    <Text type="success">已识别：{resumeParsed.position_guess} / {resumeParsed.level_guess}</Text>
                  ) : (
                    <Text type="secondary">支持 PDF/DOCX/TXT，未上传则手动选择岗位</Text>
                  )}
                >
                  <Upload
                    maxCount={1}
                    beforeUpload={(file) => {
                      handleResumeUpload(file)
                      return false
                    }}
                    onRemove={() => { setResumeParsed(null) }}
                  >
                    <Button icon={<UploadOutlined />} loading={busy}>选择文件</Button>
                  </Upload>
                </Form.Item>

                <Form.Item
                  label="岗位（选填）"
                  name="position"
                  extra={<Text type="secondary">留空时由简历或 JD 自动推断；可手动输入</Text>}
                >
                  <Input
                    placeholder={resumeParsed ? '已从简历识别（可修改）' : '如：产品经理、前端工程师'}
                  />
                </Form.Item>

                <Form.Item label="职级（选填）" name="level">
                  <Select
                    allowClear
                    placeholder="留空时由简历自动推断"
                    options={LEVELS.map((l) => ({ value: l, label: l }))}
                  />
                </Form.Item>

                <Form.Item label="目标公司（选填）" name="company">
                  <Input placeholder="如：字节跳动、阿里巴巴" />
                </Form.Item>

                <Form.Item
                  label="JD 链接（选填）"
                  extra={<Text type="secondary">粘贴招聘网页 URL，点「抓取」自动填充下方 JD 内容</Text>}
                >
                  <Space.Compact style={{ width: '100%' }}>
                    <Input
                      placeholder="https://..."
                      value={jdUrl}
                      onChange={(e) => setJdUrl(e.target.value)}
                    />
                    <Button onClick={handleFetchJd} loading={fetchingJd}>抓取</Button>
                  </Space.Compact>
                </Form.Item>

                <Form.Item
                  label="JD 内容（选填）"
                  name="jd_content"
                  extra={<Text type="secondary">可直接粘贴或编辑抓取结果。AI 会基于 JD 出题</Text>}
                >
                  <Input.TextArea rows={4} placeholder="粘贴岗位描述、能力要求、加分项等" />
                </Form.Item>

                <Form.Item
                  label="面试官风格"
                  name="style"
                  rules={[{ required: true }]}
                  extra={<Text type="secondary">不同风格对应不同的提问方式与压力程度</Text>}
                >
                  <Radio.Group>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 8, width: '100%' }}>
                      {styles.map((s) => (
                        <Radio.Button
                          key={s.name}
                          value={s.name}
                          style={{ height: 'auto', padding: '8px 12px', whiteSpace: 'normal', textAlign: 'left' }}
                        >
                          <div style={{ fontWeight: 500 }}>{s.label}</div>
                          <div style={{ fontSize: 11, color: '#888', marginTop: 2 }}>{s.description}</div>
                        </Radio.Button>
                      ))}
                    </div>
                  </Radio.Group>
                </Form.Item>
              </>
            ) : (
              <>
                <Form.Item
                  label={scenario === 'presentation' ? '汇报主题（必填）' : '演讲主题（选填）'}
                  name="position"
                  rules={scenario === 'presentation' ? [{ required: true, message: '请输入汇报主题' }] : []}
                >
                  <Input
                    placeholder={scenario === 'presentation' ? '如：Q2 季度工作汇报、项目进展述职' : '如：产品发布、团队动员、技术分享'}
                  />
                </Form.Item>

                {isTimed && (
                  <Form.Item
                    label="训练时长"
                    name="duration_limit"
                    rules={[{ required: true, message: '请选择时长' }]}
                    extra={<Text type="secondary">倒计时结束会提醒并收尾；也可提前讲完手动结束</Text>}
                  >
                    <Radio.Group>
                      {DURATION_OPTIONS.map((d) => (
                        <Radio.Button key={d.value} value={d.value}>{d.label}</Radio.Button>
                      ))}
                    </Radio.Group>
                  </Form.Item>
                )}

                <Form.Item
                  label="上传材料（选填）"
                  extra={materialUploaded ? (
                    <Text type="success">已上传：{materialFile?.name}（AI{scenarioMeta.role}会基于材料质询/点评）</Text>
                  ) : (
                    <Text type="secondary">支持 PDF/DOCX/TXT，如汇报 PPT 大纲、演讲稿</Text>
                  )}
                >
                  <Upload
                    maxCount={1}
                    beforeUpload={(file) => {
                      handleMaterialUpload(file)
                      return false
                    }}
                    onRemove={() => { setMaterialFile(null); setMaterialUploaded(false) }}
                  >
                    <Button icon={<UploadOutlined />} loading={busy}>选择文件</Button>
                  </Upload>
                </Form.Item>
              </>
            )}

            {isInterview && (
              <Form.Item label="面试模式">
                <Radio.Group value={voiceMode ? 'voice' : 'manual'} onChange={(e) => setVoiceMode(e.target.value === 'voice')}>
                  <Radio.Button value="voice">🎤 自动对话（说完自动提交，像真实面试）</Radio.Button>
                  <Radio.Button value="manual">⏸️ 手动对话（按钮控制提交节奏）</Radio.Button>
                </Radio.Group>
                <div style={{ fontSize: 12, color: '#888', marginTop: 4 }}>
                  {voiceMode
                    ? '说话停顿 1 秒后自动提交回答，AI 自动追问并语音播报，全程免点击。'
                    : '面试官语音播报后，点「开始回答」说话，说完点「完成回答」提交。界面与分析与自动模式完全一致。'}
                </div>
              </Form.Item>
            )}

            <Space>
              <Button type="primary" loading={busy} onClick={startFlow}>
                开始{scenarioMeta.name}
              </Button>
              <Button onClick={() => nav('/settings')}>先去配置 AI API</Button>
            </Space>

            {isInterview && (
              <div style={{ marginTop: 20, paddingTop: 16, borderTop: '1px dashed #e5e5e5' }}>
                <Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>
                  保存为档案（保存当前配置，下次一键开练）
                </Text>
                <Space.Compact style={{ width: 360 }}>
                  <Input
                    placeholder="档案名，如：产品经理-字节"
                    value={saveName}
                    onChange={(e) => setSaveName(e.target.value)}
                  />
                  <Button onClick={saveProfile} loading={savingProfile}>保存档案</Button>
                </Space.Compact>
              </div>
            )}
          </Form>
          <Alert
            type="info"
            showIcon
            style={{ marginTop: 16 }}
            message={`使用前请在「设置」中配置 LLM（必需）与 TTS（AI${scenarioMeta.role}语音）。`}
            description="实时识别使用阿里云 Paraformer（复用 TTS 的阿里云 key 即可）；实时反馈、语音播报、报告链路三场景完全一致。"
          />
        </Card>
      </div>
    )
  }

  // ===== 运行阶段 UI（自动/手动模式共用语音链路界面） =====
  if (phase === 'running') {
    const vs = voice.state
    // 限时场景：正计时 + 倒计时（started_at 为计时基准）
    const elapsedSec = elapsedSecLocal
    const remainSec = durationLimit > 0 ? Math.max(0, durationLimit * 60 - elapsedSec) : null
    const fmtTime = (s: number) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
    const nearEnd = remainSec !== null && remainSec > 0 && remainSec <= 60  // 剩 1 分钟橙色提醒
    const timeOver = remainSec === 0

    const statusMeta: Record<string, { color: string; label: string }> = voiceMode
      ? {
          idle: { color: 'default', label: '未开始' },
          connecting: { color: 'orange', label: '连接中' },
          listening: { color: 'green', label: isInterview ? '🎧 请回答（我在听）' : '🎙️ 请开始你的表达（我在听）' },
          ai_speaking: { color: 'blue', label: `🔊 AI${scenarioMeta.role}发言中` },
          thinking: { color: 'purple', label: '🤔 思考中' },
          ended: { color: 'default', label: '已结束' },
          error: { color: 'red', label: '出错' },
        }
      : {
          idle: { color: 'default', label: '未开始' },
          connecting: { color: 'orange', label: '连接中' },
          listening: { color: 'green', label: '🎙️ 录音中（点「完成回答」提交）' },
          ai_speaking: { color: 'blue', label: `🔊 AI${scenarioMeta.role}发言中` },
          thinking: { color: 'purple', label: '✋ 待你作答（点「开始回答」）' },
          ended: { color: 'default', label: '已结束' },
          error: { color: 'red', label: '出错' },
        }
    const meta = statusMeta[vs.status] || statusMeta.idle

    // 字幕高亮渲染：把口癖/重复词/模糊词标黄红紫
    const renderHighlighted = (text: string) => {
      if (!text) return null
      const words = [...vs.highlightWords.keys()].sort((a, b) => b.length - a.length)
      if (words.length === 0) return <span>{text}</span>
      const re = new RegExp(`(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'g')
      return text.split(re).map((seg, i) => {
        const level = vs.highlightWords.get(seg)
        if (!level) return <span key={i}>{seg}</span>
        const styleMap: Record<string, React.CSSProperties> = {
          filler: { background: '#fff1b8', color: '#874d00' },
          repeat: { background: '#ffd6d6', color: '#a8071a' },
          hedge: { background: '#efdbff', color: '#531dab' },
        }
        return (
          <span key={i} style={{ ...styleMap[level], borderRadius: 3, padding: '0 3px', fontWeight: 600 }}>
            {seg}
          </span>
        )
      })
    }

    const feedbackMeta: Record<string, { label: string; color: string; bg: string }> = {
      filler: { label: '口头禅', color: '#d48806', bg: '#fffbe6' },
      repeat: { label: '重复用词', color: '#cf1322', bg: '#fff1f0' },
      hedge: { label: '模糊表述', color: '#7c6fbb', bg: '#f9f0ff' },
      uncertain: { label: '不自信', color: '#d4380d', bg: '#fff2e8' },
      long_sentence: { label: '句子过长', color: '#5b8c00', bg: '#f6ffed' },
    }
    const topFillers = Object.entries(vs.fillerTotals)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 8)
    const kindCounts = vs.feedbacks.reduce<Record<string, number>>((acc, f) => {
      acc[f.kind] = (acc[f.kind] || 0) + 1
      return acc
    }, {})

    return (
      <div style={{ maxWidth: 1100, margin: '0 auto' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
          <Space>
            <Tag color={vs.connected ? 'green' : 'orange'}>{vs.connected ? '语音通道已连接' : '连接中'}</Tag>
            <Tag color={meta.color}>{meta.label}</Tag>
            {isTimed && startedAt && (
              <Tag
                color={timeOver ? 'red' : nearEnd ? 'orange' : 'blue'}
                style={{ fontFamily: 'monospace', fontSize: 14 }}
              >
                ⏱ {fmtTime(elapsedSec)}{remainSec !== null ? ` / 剩 ${fmtTime(remainSec)}` : ''}
              </Tag>
            )}
          </Space>
          <Space>
            {isTimed && (
              <Button
                type="primary"
                onClick={() => { autoFinishedRef.current = true; voice.finishStage() }}
              >
                讲完了，下一环节
              </Button>
            )}
            <Button onClick={() => { voice.stop(); voiceStartRef.current = false; setPhase('config') }}>
              {isInterview ? (voiceMode ? '退出语音模式' : '返回配置') : '返回配置'}
            </Button>
            <Button danger onClick={() => {
              voice.endInterview()
              if (sid) {
                apiService.endInterview(sid).catch(() => {}).finally(() => {
                  setTimeout(() => nav(`/report/${sid}`), 800)
                })
              }
            }}>结束训练</Button>
          </Space>
        </div>

        {(vs.timeUp || timeOver) && isTimed && (
          <Alert type={timeOver ? 'error' : 'warning'} showIcon style={{ marginBottom: 12 }}
            message="⏰ 时间到！系统将自动收尾，也可点「讲完了，下一环节」继续。" />
        )}

        {vs.error && (
          <Alert type="error" showIcon style={{ marginBottom: 12 }} message={vs.error} />
        )}

        <Card title={`AI ${scenarioMeta.role}`} style={{ marginBottom: 12 }}>
          <Paragraph style={{ minHeight: 48, fontSize: 16 }}>
            {vs.aiQuestion || (isInterview ? '等待面试官提问...' : `等待AI${scenarioMeta.role}发言...`)}
          </Paragraph>
        </Card>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 400px', gap: 12 }}>
          <Card title="实时字幕（你正在说的话）">
            <div style={{ minHeight: 140, fontSize: 17, lineHeight: 2 }}>
              {vs.finalText && renderHighlighted(vs.finalText)}
              {vs.partialText && <span style={{ color: '#999' }}>{vs.partialText}</span>}
              {!vs.finalText && !vs.partialText && (
                <span style={{ color: '#bbb' }}>
                  {isInterview
                    ? (voiceMode ? '开始说话吧，说完停顿一下，面试官会自动接话…' : '点「开始回答」后说话，说完点「完成回答」提交')
                    : '开始你的表达吧，全程实时反馈；讲完点右上角「讲完了，下一环节」'}
                </span>
              )}
            </div>
            {voiceMode ? (
              vs.status === 'listening' && (
                <div style={{ fontSize: 12, color: '#888' }}>
                  检测到说话后，静音 1.2 秒即视为回答完毕，自动提交。
                  <span style={{ background: '#fff1b8', padding: '0 3px' }}>黄</span>＝口头禅
                  <span style={{ background: '#ffd6d6', padding: '0 3px', marginLeft: 4 }}>红</span>＝重复
                  <span style={{ background: '#efdbff', padding: '0 3px', marginLeft: 4 }}>紫</span>＝模糊
                </div>
              )
            ) : (
              <Space style={{ marginTop: 8 }}>
                <Button
                  type="primary"
                  disabled={vs.status === 'listening'}
                  onClick={() => voice.beginAnswer()}
                >
                  开始回答
                </Button>
                <Button
                  danger
                  disabled={vs.status !== 'listening'}
                  onClick={() => {
                    if (!vs.finalText && !vs.partialText) {
                      message.warning('还没检测到说话内容，请先回答再提交')
                      return
                    }
                    voice.commitAnswer()
                  }}
                >
                  完成回答
                </Button>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  <span style={{ background: '#fff1b8', padding: '0 3px' }}>黄</span>＝口头禅
                  <span style={{ background: '#ffd6d6', padding: '0 3px', marginLeft: 4 }}>红</span>＝重复
                  <span style={{ background: '#efdbff', padding: '0 3px', marginLeft: 4 }}>紫</span>＝模糊
                </Text>
              </Space>
            )}
          </Card>

          <Card
            title={`实时表达反馈${vs.issueCount > 0 ? `（本轮 ${vs.issueCount} 处）` : ''}`}
            size="small"
            bodyStyle={{ maxHeight: 480, overflowY: 'auto' }}
          >
            {/* 汇总：口头禅 Top + 分维度统计 */}
            {(topFillers.length > 0 || vs.feedbacks.length > 0) && (
              <div style={{ marginBottom: 10, paddingBottom: 8, borderBottom: '1px dashed #eee' }}>
                {topFillers.length > 0 && (
                  <div style={{ marginBottom: 6 }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>本问题高频词</Text>
                    <div style={{ marginTop: 4 }}>
                      {topFillers.map(([w, c]) => (
                        <Tag key={w} color="orange" style={{ marginBottom: 4 }}>{w} ×{c}</Tag>
                      ))}
                    </div>
                  </div>
                )}
                <Space size={4} wrap>
                  {Object.entries(kindCounts).map(([k, c]) => {
                    const fm = feedbackMeta[k]
                    return fm ? <Tag key={k} style={{ fontSize: 11, color: fm.color, borderColor: fm.color }}>{fm.label} {c}</Tag> : null
                  })}
                </Space>
              </div>
            )}
            {vs.feedbacks.length === 0 ? (
              <Text type="secondary" style={{ fontSize: 12 }}>暂无问题——口头禅、重复用词、模糊表述、不自信语气、句子过长都会在这里实时标注</Text>
            ) : (
              [...vs.feedbacks].reverse().map((f) => {
                const fm = feedbackMeta[f.kind] || feedbackMeta.filler
                return (
                  <div
                    key={f.id}
                    style={{
                      marginBottom: 8, padding: '6px 8px', borderRadius: 6,
                      background: fm.bg, fontSize: 12,
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <Tag color={fm.color} style={{ fontSize: 11, marginInlineEnd: 0 }}>{fm.label}</Tag>
                      {f.word && <Text strong>「{f.word}」{f.count && f.count > 1 ? `×${f.count}` : ''}</Text>}
                    </div>
                    <div style={{ color: '#666', marginTop: 4, lineHeight: 1.5, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                      {f.sentence}
                    </div>
                    {f.advice && (
                      <div style={{ color: '#0e7490', marginTop: 3, fontSize: 11 }}>
                        💡 {f.advice}
                      </div>
                    )}
                  </div>
                )
              })
            )}
          </Card>
        </div>

        <div style={{ marginTop: 12 }}>
          <EmotionIndicator data={emotion} />
        </div>
      </div>
    )
  }
}
