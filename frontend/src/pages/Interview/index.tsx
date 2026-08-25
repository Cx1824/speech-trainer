import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  Card, Form, Select, Upload, Button, Alert, Typography, Space, message, Input, Modal, Radio,
} from 'antd'
import { UploadOutlined } from '@ant-design/icons'
import { apiService } from '@/services/api'
import { useVoiceSession } from '@/hooks/useVoiceSession'
import { getDisplayedFillerTotals } from '@/utils/liveFillerTotals'
import EmotionIndicator, { type EmotionData } from '@/components/EmotionIndicator'
import {
  type InterviewIntensityOut,
  type InterviewModeOut,
  type InterviewProgress,
  type InterviewStyle,
  SCENARIOS,
} from '@/types/interview'
import './Interview.css'

const { Title, Text } = Typography

const LEVELS = ['实习', '初级', '中级', '高级', '资深']

/** 时长选项（分钟）：限时场景自选 */
const DURATION_OPTIONS = [1, 2, 3, 5, 8, 10, 15, 20].map((m) => ({ value: m, label: `${m} 分钟` }))
const OVERTIME_GRACE_SECONDS = 10 * 60

const INTERVIEW_MODE_KEYS = new Set(['full', 'hr', 'professional', 'project', 'behavioral', 'weakness'])

const FALLBACK_INTERVIEW_MODES: InterviewModeOut[] = [
  { key: 'full', name: '全流程模拟', description: 'HR、项目、专业能力、行为问题和候选人反问。', recommended: true, estimates: { quick: { min: 12, max: 18 }, standard: { min: 25, max: 35 }, deep: { min: 40, max: 55 } }, question_counts: { quick: 6, standard: 12, deep: 18 } },
  { key: 'hr', name: 'HR 初面', description: '基础筛选、动机、离职、稳定性、自我认知和现实条件。', recommended: false, estimates: { quick: { min: 6, max: 8 }, standard: { min: 10, max: 15 }, deep: { min: 18, max: 25 } }, question_counts: { quick: 4, standard: 8, deep: 11 } },
  { key: 'professional', name: '专业 / 业务面', description: '根据岗位、JD 和经历覆盖多个专业能力维度。', recommended: false, estimates: { quick: { min: 10, max: 15 }, standard: { min: 18, max: 25 }, deep: { min: 30, max: 40 } }, question_counts: { quick: 5, standard: 8, deep: 12 } },
  { key: 'project', name: '项目深挖', description: '最多两个项目，练角色、决策、结果和失败复盘。', recommended: false, estimates: { quick: { min: 8, max: 12 }, standard: { min: 12, max: 18 }, deep: { min: 20, max: 30 } }, question_counts: { quick: 5, standard: 7, deep: 10 } },
  { key: 'behavioral', name: '行为 / 管理面', description: '冲突、失败、反馈、领导力与职业判断。', recommended: false, estimates: { quick: { min: 8, max: 12 }, standard: { min: 12, max: 18 }, deep: { min: 20, max: 25 } }, question_counts: { quick: 5, standard: 7, deep: 10 } },
  { key: 'weakness', name: '上次报告补弱', description: '根据最近一次报告，针对薄弱维度短时重练。', recommended: false, estimates: { quick: { min: 6, max: 8 }, standard: { min: 8, max: 12 }, deep: { min: 15, max: 20 } }, question_counts: { quick: 4, standard: 5, deep: 7 } },
]

const FALLBACK_INTENSITIES: InterviewIntensityOut[] = [
  { key: 'quick', name: '快速', description: '核心问题，基本不追问。', followup_budget: 1 },
  { key: 'standard', name: '标准', description: '重要维度各问一次，必要时追问。', followup_budget: 4 },
  { key: 'deep', name: '深度', description: '增加情境与证据追问，同时守住覆盖边界。', followup_budget: 7 },
]

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
  const replayAudioUrl = searchParams.get('replayAudio') || undefined
  const isRecordingReplay = Boolean(replayAudioUrl)
  const isInterview = scenario === 'interview'
  const scenarioMeta = SCENARIOS[scenario] ?? SCENARIOS.interview
  const isTimed = scenario === 'presentation' || scenario === 'speech'
  const requestedModeParam = searchParams.get('mode') || 'full'
  const requestedInterviewMode = INTERVIEW_MODE_KEYS.has(requestedModeParam) ? requestedModeParam : 'full'
  const sourceSessionId = searchParams.get('sourceSession') || ''

  const [phase, setPhase] = useState<'config' | 'running'>('config')
  const [sid, setSid] = useState<string | null>(null)
  const [form] = Form.useForm()
  const [busy, setBusy] = useState(false)
  const [styles, setStyles] = useState<InterviewStyle[]>([])
  const [interviewModes, setInterviewModes] = useState<InterviewModeOut[]>(FALLBACK_INTERVIEW_MODES)
  const [interviewIntensities, setInterviewIntensities] = useState<InterviewIntensityOut[]>(FALLBACK_INTENSITIES)
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
  const selectedInterviewMode = Form.useWatch('interview_mode', form) || requestedInterviewMode
  const selectedInterviewIntensity = Form.useWatch('interview_intensity', form) || 'standard'
  const selectedModeDefinition = interviewModes.find((mode) => mode.key === selectedInterviewMode) ?? interviewModes[0]
  const selectedEstimate = selectedModeDefinition?.estimates[selectedInterviewIntensity]
  const selectedQuestionCount = selectedModeDefinition?.question_counts[selectedInterviewIntensity] ?? 0
  const selectedIntensityDefinition = interviewIntensities.find((item) => item.key === selectedInterviewIntensity)

  useEffect(() => {
    document.body.classList.add('is-training-experience')
    return () => document.body.classList.remove('is-training-experience')
  }, [])

  useEffect(() => {
    document.body.classList.toggle('is-live-training', phase === 'running')
    return () => document.body.classList.remove('is-live-training')
  }, [phase])
  // 限时计时器：零点=开场白播完（timer_started 回执），非点「开始」时刻——
  // LLM 生成开场白 + TTS 播报的时长不应吃掉用户表达的限时
  const [startedAt, setStartedAt] = useState<Date | null>(null)
  const [durationLimit, setDurationLimit] = useState(0)
  const autoFinishedRef = useRef(false)
  const [, setTick] = useState(0)  // 秒级刷新计时器显示
  useEffect(() => {
    if (phase !== 'running') return
    const t = setInterval(() => setTick((x) => x + 1), 1000)
    return () => clearInterval(t)
  }, [phase])
  const elapsedSecLocal = startedAt ? Math.max(0, Math.floor((Date.now() - startedAt.getTime()) / 1000)) : 0
  // 面试模式：true=语音对话（自动 VAD 提交）/ false=手动（按钮提交）。
  // 两种模式统一走 /ws/voice 语音链路（同一套字幕/分析/播报），仅提交方式不同。
  const [voiceMode, setVoiceMode] = useState(!isRecordingReplay)
  const voice = useVoiceSession(phase === 'running' ? sid : null, (p) => {
    setEmotion({
      tensionScore: p.tension_score as number,
      tensionLevel: p.tension_level as string,
      confidenceScore: p.confidence_score as number,
      confidenceLevel: p.confidence_level as string,
      voiceSignal: Boolean(p.voice_signal),
      pitchJitter: p.pitch_jitter as number | undefined,
      pauseCount: p.pause_count as number | undefined,
      hesitationCount: p.hesitation_count as number | undefined,
      speechRate: p.speech_rate_estimate as number | undefined,
      factors: p.factors as Record<string, number> | undefined,
      calibrated: Boolean(p.calibrated),
    })
  }, {
    manual: !voiceMode || isTimed,
    autoResume: isTimed,
    replayAudioUrl,
    onAnswerStarted: () => setStartedAt((current) => current ?? new Date()),
    // 开场白播完的后端回执：此刻才是计时零点
    onTimerStarted: () => setStartedAt(new Date()),
  })  // 限时：持续采集+按钮推进；手动面试：挂起+按钮恢复
  const {
    start: startVoice,
    requestFirstQuestion,
    beginSoloPractice,
    finishStage: finishVoiceStage,
  } = voice

  const loadProfiles = useCallback(() => {
    apiService.listProfiles().then(setProfiles).catch(() => {})
  }, [])

  // 加载风格列表 + 档案列表（仅面试场景加载档案）
  useEffect(() => {
    if (isInterview) {
      apiService.listStyles().then((r) => setStyles(r.styles)).catch(() => {})
      apiService.listInterviewModes().then((result) => {
        setInterviewModes(result.modes)
        setInterviewIntensities(result.intensities)
      }).catch(() => {})
      loadProfiles()
    }
  }, [isInterview, loadProfiles])

  // 提前上传简历解析岗位（在创建会话之前）
  const handleResumeUpload = async (file: File) => {
    setBusy(true)
    try {
      // 先创建一个临时会话用于上传简历
      const session = await apiService.createInterview({ style: 'professional' })
      message.info('正在解析简历…')
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

  const confirmDeleteProfile = (profile: ProfileItem) => {
    Modal.confirm({
      title: `删除「${profile.name}」？`,
      content: '删除后无法恢复。',
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => deleteProfile(profile.id),
    })
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
      message.info('正在解析材料…')
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
        ...(isInterview ? {
          interview_mode: values.interview_mode,
          interview_intensity: values.interview_intensity,
          source_session_id: sourceSessionId,
        } : {}),
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
      setStartedAt(null)
      autoFinishedRef.current = false
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
  // 字幕滚动区：文字增长时自动滚到底部
  const subtitleRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = subtitleRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [voice.state.segments, voice.state.partialText])

  useEffect(() => {
    if (phase !== 'running' || !sid || voiceStartRef.current) return
    voiceStartRef.current = true
    endedHandledRef.current = false
    // 只读时长上限；计时零点由 timer_started 回执设置（开场白播完才开始）
    apiService.getInterview(sid).then((s) => {
      if (s.duration_limit) setDurationLimit(s.duration_limit)
    }).catch(() => {})
    ;(async () => {
      try {
        await startVoice(sid)
        if (isInterview) {
          requestFirstQuestion()
        } else {
          beginSoloPractice()
        }
      } catch (e: any) {
        message.error(`语音会话启动失败：${e?.message || e}（检查麦克风权限）`)
      }
    })()
  }, [beginSoloPractice, isInterview, phase, requestFirstQuestion, sid, startVoice])

  // 到点只提示并继续收音；本地或服务端确认超时 10 分钟后才强制收尾。
  useEffect(() => {
    if (phase !== 'running' || !isTimed || autoFinishedRef.current) return
    const overtimeSec = durationLimit > 0
      ? Math.max(0, elapsedSecLocal - durationLimit * 60)
      : 0
    if (voice.state.hardTimeUp || overtimeSec >= OVERTIME_GRACE_SECONDS) {
      autoFinishedRef.current = true
      message.warning('已超时 10 分钟，系统正在自动结束并保存本次内容')
      finishVoiceStage()
    }
  }, [voice.state.hardTimeUp, elapsedSecLocal, durationLimit, finishVoiceStage, isTimed, phase])

  // 监听 ANALYSIS_UPDATE 消息 - 已经在前面通过 ws.subscribe 处理

  // ===== 配置阶段 UI =====
  if (phase === 'config') {
    return (
      <div className="training-config-page" style={{ maxWidth: 760, margin: '0 auto' }}>
        <section className="training-config-intro">
          <p>SET THE STAGE / {scenarioMeta.role.toUpperCase()}</p>
          <Title level={1}>{scenarioMeta.name}，<span>开始说。</span></Title>
          <div>进入后，你的字幕始终在中央；口癖、重复和节奏提示始终可见。</div>
        </section>

        {isInterview && profiles.length > 0 && (
          <Card title="我的档案" style={{ marginBottom: 16 }} size="small">
            {profiles.map((p) => (
              <div
                key={p.id}
                style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  padding: '8px 12px', border: '1px solid #e5e5e5', borderRadius: 6,
                  marginBottom: 8, gap: 8,
                }}
              >
                <Button
                  type="text"
                  onClick={() => applyProfile(p)}
                  style={{ flex: 1, height: 'auto', minWidth: 0, padding: 0, textAlign: 'left' }}
                >
                  <span style={{ display: 'block' }}><Text strong>{p.name}</Text></span>
                  <span style={{ display: 'block', fontSize: 12, color: '#888', whiteSpace: 'normal' }}>
                    {p.position || '未指定岗位'}
                    {p.company ? ` · ${p.company}` : ''}
                    {p.has_resume ? ' · 含简历' : ''}
                  </span>
                </Button>
                <Button
                  danger size="small" type="text"
                  onClick={() => confirmDeleteProfile(p)}
                >
                  删除
                </Button>
              </div>
            ))}
          </Card>
        )}

        <Card>
          {isRecordingReplay && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 20 }}
              message="通话面试复盘 · 仅分析本人声音"
              description="录音中的招聘方声音已经分离，不会计入口癖、语速、停顿或声音表现。开始后点“开始回答”，即可按原速查看字幕与实时提示。"
            />
          )}
          <Form
            form={form}
            layout="vertical"
            initialValues={{
              position: '',
              level: '中级',
              style: 'professional',
              interview_mode: requestedInterviewMode,
              interview_intensity: 'standard',
              company: '',
              jd_content: '',
              duration_limit: isTimed ? 5 : 0,
            }}
          >
            {isInterview ? (
              <>
                <section className="interview-plan-picker" aria-labelledby="interview-plan-title">
                  <div className="interview-plan-heading">
                    <div>
                      <h2 id="interview-plan-title">这次重点练什么？</h2>
                      <p>面试类型决定覆盖范围；面试官风格只改变语气和追问力度。</p>
                    </div>
                    {selectedEstimate && (
                      <div className="interview-plan-estimate" aria-live="polite">
                        <strong>{selectedEstimate.min}–{selectedEstimate.max}<small> 分钟</small></strong>
                        <span>约 {selectedQuestionCount} 个主问题 · 最多 {selectedIntensityDefinition?.followup_budget ?? 0} 次追问</span>
                      </div>
                    )}
                  </div>

                  <Form.Item name="interview_mode" rules={[{ required: true }]}>
                    <Radio.Group className="interview-mode-list">
                      {interviewModes.map((mode) => {
                        const estimate = mode.estimates[selectedInterviewIntensity]
                        return (
                          <Radio key={mode.key} value={mode.key} className="interview-mode-option">
                            <span className="interview-mode-copy">
                              <span className="interview-mode-name">
                                {mode.name}
                                {mode.recommended && <em>推荐</em>}
                              </span>
                              <span>{mode.description}</span>
                            </span>
                            {estimate && <span className="interview-mode-time">约 {estimate.min}–{estimate.max} 分钟</span>}
                          </Radio>
                        )
                      })}
                    </Radio.Group>
                  </Form.Item>

                  <Form.Item label="训练强度" name="interview_intensity" rules={[{ required: true }]}>
                    <Radio.Group className="interview-intensity-choice">
                      {interviewIntensities.map((intensity) => (
                        <Radio.Button key={intensity.key} value={intensity.key}>
                          <strong>{intensity.name}</strong>
                          <span>{intensity.description}</span>
                        </Radio.Button>
                      ))}
                    </Radio.Group>
                  </Form.Item>
                  {selectedInterviewMode === 'weakness' && !sourceSessionId && (
                    <p className="interview-plan-note">未指定历史报告时，系统会自动使用最近一次面试报告；首次使用则从回答结构、案例证据和岗位匹配开始。</p>
                  )}
                </section>

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
                    <Button icon={<UploadOutlined aria-hidden="true" />} loading={busy}>选择文件</Button>
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
                  label="招聘网页链接（选填）"
                  extra={<Text type="secondary">粘贴招聘网页地址，点「抓取」自动填充下方职位描述</Text>}
                >
                  <Space.Compact className="training-compact" style={{ width: '100%' }}>
                    <Input
                      placeholder="https://…"
                      value={jdUrl}
                      onChange={(e) => setJdUrl(e.target.value)}
                    />
                    <Button onClick={handleFetchJd} loading={fetchingJd}>抓取</Button>
                  </Space.Compact>
                </Form.Item>

                <Form.Item
                  label="职位描述（选填）"
                  name="jd_content"
                  extra={<Text type="secondary">可直接粘贴或编辑抓取结果。AI 会根据职位描述提问</Text>}
                >
                  <Input.TextArea rows={4} placeholder="粘贴岗位描述、能力要求、加分项等" />
                </Form.Item>

                <Form.Item
                  label="面试官风格"
                  name="style"
                  rules={[{ required: true }]}
                  extra={<Text type="secondary">不同风格对应不同的提问方式与压力程度</Text>}
                >
                  <Radio.Group className="training-style-choice">
                    <div className="training-style-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 8, width: '100%' }}>
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
                    extra={<Text type="secondary">到达设定时长后会提醒，但继续录音；超时 10 分钟自动结束，也可提前讲完</Text>}
                  >
                    <Radio.Group className="training-duration-choice">
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
                    <Button icon={<UploadOutlined aria-hidden="true" />} loading={busy}>选择文件</Button>
                  </Upload>
                </Form.Item>
              </>
            )}

            {isInterview && (
              <Form.Item label="面试模式">
                <Radio.Group
                  className="training-mode-choice"
                  value={voiceMode ? 'voice' : 'manual'}
                  onChange={(e) => setVoiceMode(e.target.value === 'voice')}
                >
                  <Radio.Button value="voice">自动对话（说完自动提交）</Radio.Button>
                  <Radio.Button value="manual">手动对话（按钮控制提交）</Radio.Button>
                </Radio.Group>
                <div style={{ fontSize: 12, color: '#888', marginTop: 4 }}>
                  {isRecordingReplay
                    ? '录音复盘固定使用手动模式，避免对话间隔被误判为回答结束。'
                    : voiceMode
                    ? '系统会在你连续停顿约 3 秒后提交回答；回答很短时会多等待片刻，避免把句间停顿误判为说完。'
                    : '面试官语音播报后，点「开始回答」说话，说完点「完成回答」提交。界面与分析与自动模式完全一致。'}
                </div>
              </Form.Item>
            )}

            <Space className="training-config-actions" wrap>
              <Button type="primary" loading={busy} onClick={startFlow}>
                开始{scenarioMeta.name}
              </Button>
              <Link className="training-settings-link ant-btn ant-btn-default" to="/settings">
                {isInterview ? '配置 AI 服务' : '配置语音识别'}
              </Link>
            </Space>

            {isInterview && (
              <div style={{ marginTop: 20, paddingTop: 16, borderTop: '1px dashed #e5e5e5' }}>
                <Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>
                  保存为档案（保存当前配置，下次一键开练）
                </Text>
                <Space.Compact className="training-compact" style={{ width: '100%', maxWidth: 360 }}>
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
            message={isInterview
              ? `开始前，请在「设置」中完成 AI 与语音服务的连接。`
              : '点击开始，直接进入收音与倒计时。'}
            description={isInterview
              ? `系统会把你的语音转成文字，给出实时提示，并播报 AI${scenarioMeta.role}的问题。`
              : '首次使用，请在「设置」中完成语音识别连接；训练结束后会提供文字点评。'}
          />
        </Card>
      </div>
    )
  }

  // ===== 运行阶段 UI（自动/手动模式共用语音链路界面） =====
  if (phase === 'running') {
    const vs = voice.state
    const interviewProgress = isInterview ? vs.interviewProgress : null
    // 限时场景：正计时 + 倒计时（started_at 为计时基准）
    const elapsedSec = elapsedSecLocal
    const remainSec = durationLimit > 0 ? Math.max(0, durationLimit * 60 - elapsedSec) : null
    const overtimeSec = durationLimit > 0 ? Math.max(0, elapsedSec - durationLimit * 60) : 0
    const fmtTime = (s: number) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
    const nearEnd = remainSec !== null && remainSec > 0 && remainSec <= 60  // 剩 1 分钟橙色提醒
    const timeOver = remainSec === 0

    const statusMeta: Record<string, { color: string; label: string }> = voiceMode
      ? {
          idle: { color: 'default', label: '未开始' },
          connecting: { color: 'orange', label: '连接中' },
          listening: { color: 'green', label: isInterview ? '请回答（正在收音）' : '请开始表达（正在收音）' },
          ai_speaking: { color: 'blue', label: `AI${scenarioMeta.role}发言中` },
          thinking: { color: 'purple', label: '正在准备' },
          ended: { color: 'default', label: '已结束' },
          error: { color: 'red', label: '出错' },
        }
      : {
          idle: { color: 'default', label: '待你作答（点「开始回答」）' },
          connecting: { color: 'orange', label: '连接中' },
          listening: { color: 'green', label: '录音中（点「完成回答」提交）' },
          ai_speaking: { color: 'blue', label: `AI${scenarioMeta.role}发言中` },
          thinking: { color: 'purple', label: '待你作答（点「开始回答」）' },
          ended: { color: 'default', label: '已结束' },
          error: { color: 'red', label: '出错' },
        }
    const meta = statusMeta[vs.status] || statusMeta.idle

    // partial 仍在变化，只按即时命中词高亮；定稿字幕只使用该段自己的分析范围。
    const renderPartialHighlights = (text: string) => {
      if (!text) return null
      const words = [...vs.highlightWords.keys()].filter(Boolean).sort((a, b) => b.length - a.length)
      if (words.length === 0) return <span>{text}</span>
      const re = new RegExp(`(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'g')
      return text.split(re).map((seg, i) => {
        const level = vs.highlightWords.get(seg)
        if (!level) return <span key={i}>{seg}</span>
        if (level === 'filler' || level === 'repeat') {
          return (
            <MarkedSubtitleToken
              key={i}
              kind={level}
              label={level === 'filler' ? '口头禅' : '连续重复'}
            >
              {seg}
            </MarkedSubtitleToken>
          )
        }
        return (
          <span key={i} className={`live-highlight live-highlight-${level}`}>
            {seg}
          </span>
        )
      })
    }

    const renderSegmentHighlights = (
      text: string,
      annotations: ReadonlyArray<{ start: number; end: number; kind: string }>,
      showMarkers: boolean,
      semanticText?: string,
    ) => {
      if (!text) return null
      const ranges = annotations
        .filter((annotation) => Number.isInteger(annotation.start) && Number.isInteger(annotation.end))
        .map((annotation) => ({
          ...annotation,
          start: Math.max(0, Math.min(text.length, annotation.start)),
          end: Math.max(0, Math.min(text.length, annotation.end)),
        }))
        .filter((annotation) => annotation.end > annotation.start)
        .sort((a, b) => a.start - b.start || a.end - b.end)

      const renderRange = (rangeStart: number, rangeEnd: number, keyPrefix: string) => {
        const fragments: ReactNode[] = []
        let cursor = rangeStart
        ranges.forEach((annotation, index) => {
          if (annotation.end <= rangeStart || annotation.start >= rangeEnd) return
          const start = Math.max(cursor, rangeStart, annotation.start)
          const end = Math.min(rangeEnd, annotation.end)
          if (start > cursor) {
            fragments.push(<span key={`${keyPrefix}-text-${cursor}`}>{text.slice(cursor, start)}</span>)
          }
          if (end > start) {
            const content = text.slice(start, end)
            const canPinMarker = showMarkers && (annotation.kind === 'filler' || annotation.kind === 'repeat')
            fragments.push(canPinMarker ? (
              <MarkedSubtitleToken
                key={`${keyPrefix}-annotation-${index}-${start}`}
                kind={annotation.kind as 'filler' | 'repeat'}
                label={annotation.kind === 'filler' ? '口头禅' : '连续重复'}
              >
                {content}
              </MarkedSubtitleToken>
            ) : (
              <span
                key={`${keyPrefix}-annotation-${index}-${start}`}
                className={`live-highlight live-highlight-${annotation.kind}`}
              >
                {content}
              </span>
            ))
            cursor = end
          }
        })
        if (cursor < rangeEnd) {
          fragments.push(<span key={`${keyPrefix}-text-${cursor}`}>{text.slice(cursor, rangeEnd)}</span>)
        }
        return fragments
      }

      const repeatedSentence = showMarkers ? semanticText?.trim() : ''
      const semanticStart = repeatedSentence ? text.lastIndexOf(repeatedSentence) : -1
      if (semanticStart < 0 || !repeatedSentence) {
        return renderRange(0, text.length, 'full')
      }
      const semanticEnd = semanticStart + repeatedSentence.length
      return (
        <>
          {renderRange(0, semanticStart, 'before-semantic')}
          <span className="live-semantic-phrase">
            {renderRange(semanticStart, semanticEnd, 'semantic')}
            <span className="live-semantic-anchor" aria-hidden="true">
              <span className="live-inline-label is-semantic">重复意思</span>
            </span>
          </span>
          {renderRange(semanticEnd, text.length, 'after-semantic')}
        </>
      )
    }

    const liveRate = vs.liveMetrics?.speechRate ?? emotion?.speechRate ?? null
    const liveRateLabel = vs.liveMetrics?.speechRateLevel === 'fast'
      ? '语速偏快'
      : vs.liveMetrics?.speechRateLevel === 'slow'
        ? '语速偏慢'
        : vs.liveMetrics?.speechRateLevel === 'normal'
        ? '语速适中'
          : '等待节奏数据'

    const isTranscriptEmpty = vs.segments.length === 0 && !vs.partialText
    const latestAnalyzedSegment = [...vs.segments].reverse().find((segment) => segment.analyzed)
    const coachTextByKind: Record<string, { issue: string; next: string; formula: string; severity: number }> = {
      silence: { issue: '停顿偏长', next: '想好后直接重启一句，别急着用填充词抢时间。', formula: '“我的结论是……，接下来有两点。”', severity: 2 },
      fast: { issue: '语速偏快', next: '每个重点后停半秒，让结论真正落下来。', formula: '一句结论。停半秒。再补一个依据。', severity: 2 },
      fast_run: { issue: '语速偏快', next: '每个重点后停半秒，让结论真正落下来。', formula: '一句结论。停半秒。再补一个依据。', severity: 2 },
      no_breath: { issue: '建议换气', next: '把长句拆成“结论”和“依据”两句。', formula: '“结果是……。”换气后再说“原因是……。”', severity: 2 },
      filler: { issue: '口头填充词', next: '删掉填充词，直接说后面的判断。', formula: '不要说“就是……”，直接说“我的判断是……”。', severity: 1 },
      repeat: { issue: '连续重复', next: '先停半秒，再从一个完整词重新开始。', formula: '停顿。吸气。重新说完整的“我的结论是……”。', severity: 1 },
      hedge: { issue: '表达不够明确', next: '把模糊判断改成明确结论，并补充依据。', formula: '“我的判断是……，依据是……。”', severity: 1 },
      uncertain: { issue: '结论不够确定', next: '给出明确判断，或主动说清适用条件。', formula: '“在……条件下，我的结论是……。”', severity: 1 },
      long_sentence: { issue: '句子偏长', next: '一个重点说完就停，再开始下一件事。', formula: '先说“第一点……”。停顿后再说“第二点……”。', severity: 1 },
    }
    type CoachCandidate = { issue: string; next: string; formula: string; severity: number; evidence: string; ts: number }
    const coachCandidates: CoachCandidate[] = []
    for (const annotation of latestAnalyzedSegment?.annotations ?? []) {
      const meta = coachTextByKind[annotation.kind]
      if (!meta) continue
      coachCandidates.push({
        ...meta,
        evidence: annotation.word
          ? `“${annotation.word}”`
          : `“${latestAnalyzedSegment?.text.slice(annotation.start, annotation.end) || '本句'}”`,
        ts: latestAnalyzedSegment?.ts ?? 0,
      })
    }
    for (const feedback of vs.feedbacks) {
      // 尚未真正开口时不把环境静音当作“停顿过久”，避免一开始就批评用户。
      if (feedback.kind === 'silence' && isTranscriptEmpty) continue
      const meta = coachTextByKind[feedback.kind]
      if (!meta) continue
      coachCandidates.push({
        ...meta,
        evidence: feedback.word ? `“${feedback.word}”` : '本句表达',
        ts: feedback.ts,
      })
    }
    // 提示只在问题仍然可操作的时间窗内保留；过时建议自动退回结构提示。
    const recentCoachCandidates = coachCandidates.filter((candidate) => Date.now() - candidate.ts <= 10_000)
    const latestCandidateTs = recentCoachCandidates.reduce((latest, candidate) => Math.max(latest, candidate.ts), 0)
    const priorityCandidate = recentCoachCandidates
      .filter((candidate) => candidate.ts >= latestCandidateTs - 1500)
      .sort((a, b) => b.severity - a.severity || b.ts - a.ts)[0]
    const supportingCandidates = recentCoachCandidates
      .filter((candidate) => candidate.ts >= latestCandidateTs - 8000 && candidate.issue !== priorityCandidate?.issue)
      .sort((a, b) => b.ts - a.ts)
      .filter((candidate, index, candidates) => candidates.findIndex((item) => item.issue === candidate.issue) === index)
      .slice(0, 2)
    const defaultCoach = isInterview
      ? {
          next: '先直接回答，再用一个真实经历证明。',
          formula: '“我的结论是……，具体例子是……。”',
          detail: '同步检测：口癖 · 重复 · 语速 · 停顿',
        }
      : scenario === 'presentation'
        ? {
            next: '先抛结论，再补数据、过程和下一步。',
            formula: '“本次结果是……，依据有两点。”',
            detail: '同步检测：口癖 · 重复 · 语速 · 停顿',
          }
        : {
            next: '一句只打一个观点，再用事实把它站住。',
            formula: '“我的观点是……，因为……。”',
            detail: '同步检测：口癖 · 重复 · 语速 · 停顿',
          }
    const latestSegmentIsClear = Boolean(latestAnalyzedSegment && latestAnalyzedSegment.annotations.length === 0)
    const latestSemanticRepeat = vs.semanticRepeats.length
      ? vs.semanticRepeats[vs.semanticRepeats.length - 1]
      : null
    const coachPanel = priorityCandidate
      ? {
          label: `当前建议 · ${priorityCandidate.issue}`,
          next: priorityCandidate.next,
          evidenceLabel: '触发证据',
          evidence: priorityCandidate.evidence,
          formula: priorityCandidate.formula,
          detail: supportingCandidates.length
            ? `同时关注：${supportingCandidates.map((candidate) => `${candidate.issue} ${candidate.evidence}`).join(' · ')}`
            : '同步观察：口癖 · 重复 · 节奏',
        }
      : latestSemanticRepeat
        ? {
            label: '当前建议 · 合并重复意思',
            next: '不要再次复述结论，补充新的动作、数据或影响。',
            evidenceLabel: '重复意思',
            evidence: `“${latestSemanticRepeat.first}” ↔ “${latestSemanticRepeat.second}”`,
            formula: '“具体带来的结果是……，我负责的动作是……”',
            detail: '两句表达相近，下一句请增加新信息。',
          }
        : {
          label: latestSegmentIsClear ? '当前建议 · 表达清楚' : isTranscriptEmpty ? '当前建议 · 准备开口' : '当前建议 · 继续表达',
          next: latestSegmentIsClear ? '保持一句一重点，再补一个可以核对的事实。' : defaultCoach.next,
          evidenceLabel: latestSegmentIsClear ? '本句状态' : '检测范围',
          evidence: latestSegmentIsClear ? '没有发现明显口癖或重复' : defaultCoach.detail.replace('同步检测：', ''),
          formula: latestSegmentIsClear ? '下一句继续用“结论 + 依据”的结构。' : defaultCoach.formula,
          detail: defaultCoach.detail,
        }
    const displayedFillerTotals = getDisplayedFillerTotals(
      vs.fillerTotals,
      vs.highlightWords,
      vs.partialText,
    )
    const fillerEntries = Object.entries(displayedFillerTotals).sort((a, b) => b[1] - a[1])
    const latestFiller = fillerEntries[0]
    const stutterEntries = Object.entries(vs.stutterTotals).sort((a, b) => b[1] - a[1])
    const latestStutter = stutterEntries[0]
    const semanticRepeatCount = vs.semanticRepeatTotal
    const fillerCount = fillerEntries.reduce((sum, [, count]) => sum + count, 0)
    const stutterCount = stutterEntries.reduce((sum, [, count]) => sum + count, 0)
    const aiOverlayVisible = vs.status === 'thinking' || vs.status === 'ai_speaking'
    const arcQuestion = interviewProgress?.current_label || scenarioMeta.name
    const arcGoal = interviewProgress?.current_goal
      || (isInterview ? '动作 → 结果' : scenario === 'presentation' ? '结论 → 依据 → 下一步' : '观点 → 例证 → 收束')
    const arcCoverage = interviewProgress
      ? `${interviewProgress.covered} / ${interviewProgress.total}`
      : '本轮实时分析'
    const emptyTranscript = isInterview
      ? { first: '把经历，', second: '说成答案。', detail: '开口后，你的回答会在这里出现。' }
      : scenario === 'presentation'
        ? { first: '先说结论，', second: '再讲过程。', detail: '开口后，你的汇报会在这里出现。' }
        : { first: '现在开口，', second: '让观点站住。', detail: '开口后，你的演讲会在这里出现。' }

    const confirmEndTraining = () => {
      const coverageIncomplete = Boolean(
        interviewProgress && interviewProgress.total > 0 && interviewProgress.covered < interviewProgress.total,
      )
      Modal.confirm({
        title: '结束本次训练？',
        content: coverageIncomplete
          ? `目前已覆盖 ${interviewProgress?.covered}/${interviewProgress?.total} 个练习维度，未练到的部分会在报告中标记为“未评估”。仍可结束并生成报告。`
          : '结束后将生成本次训练报告。',
        okText: '结束并查看报告',
        okButtonProps: { danger: true },
        cancelText: '继续训练',
        onOk: async () => {
          voice.endInterview()
          if (!sid) return
          try {
            await apiService.endInterview(sid)
          } catch {
            // 保持既有结束路径：接口异常时仍进入已生成的报告页。
          } finally {
            window.setTimeout(() => nav(`/report/${sid}?scenario=${scenario}`), 800)
          }
        },
      })
    }

    const confirmReturnToConfig = () => {
      Modal.confirm({
        title: isInterview && voiceMode ? '退出语音模式？' : '返回配置？',
        content: '当前收音会停止，本次内容不会生成训练报告。',
        okText: isInterview && voiceMode ? '退出语音模式' : '返回配置',
        cancelText: '继续训练',
        onOk: () => {
          voice.stop()
          voiceStartRef.current = false
          setPhase('config')
        },
      })
    }

    return (
      <div className="training-run-page">
        {/* 顶部工具栏：保留所有真实控制，弱化常规后台感。 */}
        <div className="training-toolbar">
          <Space wrap>
            <span className={`live-status-dot ${vs.connected ? 'is-ready' : ''}`} />
            <span className="live-brand">表达能力训练器</span>
            <span className="live-session-meta">
              {isRecordingReplay ? '通话面试复盘 / 仅分析本人声音' : `${scenarioMeta.name} / ${isInterview ? `AI ${scenarioMeta.role}` : '实时教练'}`}
            </span>
            <span className="live-status-copy">{meta.label}</span>
          </Space>
          <Space className="training-toolbar-actions" wrap>
            {isTimed && (
              <Button
                type="primary"
                onClick={() => { autoFinishedRef.current = true; voice.finishStage() }}
              >
                讲完了，下一环节
              </Button>
            )}
            {isInterview && interviewProgress && !['自我介绍', '候选人反问'].includes(interviewProgress.current_label) && (
              <Button
                onClick={() => voice.skipQuestionDirection()}
                disabled={vs.status !== 'listening' || Boolean(vs.partialText || vs.segments.length)}
                title={vs.status !== 'listening' ? '面试官说完后即可换题' : vs.partialText || vs.segments.length ? '已经开始回答，请先完成本题' : '跳过当前能力方向，进入下一个维度'}
              >
                换个方向
              </Button>
            )}
            <Button onClick={confirmReturnToConfig}>
              {isInterview ? (voiceMode ? '退出语音模式' : '返回配置') : '返回配置'}
            </Button>
            <Button danger onClick={confirmEndTraining}>结束训练</Button>
          </Space>
        </div>

        {!autoFinishedRef.current && (vs.timeUp || timeOver) && isTimed && (
          <aside className="live-overtime-alert" role="status" aria-live="assertive" aria-atomic="true">
            <span className="live-overtime-mark" aria-hidden="true" />
            <div className="live-overtime-copy">
              <strong>已超时 {fmtTime(overtimeSec)}</strong>
              <span>录音仍在继续 · 超时 10:00 后自动结束</span>
            </div>
            <Button type="text" onClick={() => { autoFinishedRef.current = true; voice.finishStage() }}>
              现在结束
            </Button>
          </aside>
        )}

        {vs.error && (
          <Alert className="live-alert" type="error" showIcon message={vs.error} />
        )}

        <CoachRail question={arcQuestion} goal={arcGoal} coverage={arcCoverage} />

        {aiOverlayVisible && (
          <aside className={`live-ai-overlay ${vs.status === 'ai_speaking' ? 'is-speaking' : 'is-thinking'}`} aria-live="polite" aria-atomic="true">
            <span className="live-ai-overlay-beacon" aria-hidden="true" />
            <div>
              <span className="live-ai-overlay-label">
                {vs.status === 'ai_speaking'
                  ? `AI${scenarioMeta.role}正在提问`
                  : vs.aiQuestion
                    ? '问题已生成 · 正在准备语音'
                    : 'AI 正在组织下一问'}
              </span>
              <strong>{vs.aiQuestion || '正在组织下一问…'}</strong>
            </div>
          </aside>
        )}

        {/* 主区：字幕保持独立安全区，反馈始终固定在可见范围。 */}
        <div className={`training-main-grid${isTranscriptEmpty ? ' is-transcript-empty' : ''}`}>
          {/* 左列：flex 纵向，字幕区吃掉剩余高度 */}
          <div className="training-left-column">
            <section className="training-subtitle-stage" aria-label="实时字幕">
              <div
                ref={subtitleRef}
                className={`subtitle-scroll${isTranscriptEmpty ? ' sub-scroll-empty' : ''}`}
              >
                <div className="transcript-stream">
                  {vs.segments.slice(-3).map((segment, index, visibleSegments) => {
                    const isLatestSettled = !vs.partialText && index === visibleSegments.length - 1
                    const historyDepth = visibleSegments.length - index - 1
                    return (
                      <div key={segment.id} className={`transcript-segment ${isLatestSettled ? 'is-live' : `is-history history-depth-${historyDepth}`}`}>
                        {renderSegmentHighlights(
                          segment.text,
                          segment.annotations,
                          isLatestSettled,
                          isLatestSettled ? latestSemanticRepeat?.second : undefined,
                        )}
                      </div>
                    )
                  })}
                  {vs.partialText && (
                    <div key={`partial-${vs.segments.length}`} className="transcript-segment is-live is-partial">
                      {renderPartialHighlights(vs.partialText)}
                    </div>
                  )}
                  {vs.segments.length === 0 && !vs.partialText && (
                    <div className="sub-empty-stage">
                      <strong>{emptyTranscript.first}<br /><span>{emptyTranscript.second}</span></strong>
                      <p>{emptyTranscript.detail}</p>
                      {!isInterview && (
                        <div className="live-start-cue" aria-live="polite">
                          <span aria-hidden="true" />
                          {vs.connected ? '正在收音，开始表达' : '正在连接收音…'}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
              {!voiceMode && (
                <Space className="live-manual-controls" wrap>
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
                      if (vs.segments.length === 0 && !vs.partialText) {
                        message.warning('还没检测到说话内容，请先回答再提交')
                        return
                      }
                      voice.commitAnswer()
                    }}
                  >
                    完成回答
                  </Button>
                  <Text className="live-manual-hint" type="secondary">
                    完成回答后，AI 会继续提问。
                  </Text>
                </Space>
              )}
            </section>
          </div>
        </div>

        <section className="live-feedback-strip" aria-label="本次训练累计实时反馈">
          <div className="live-action-block">
            <span className="live-feedback-kicker">当前建议 + 下一句</span>
            <strong>{coachPanel.next}</strong>
            <p><span>下一句</span>{coachPanel.formula}</p>
            <span className="live-feedback-a11y" role="status" aria-live="polite">
              {coachPanel.label}。{coachPanel.next}。下一句怎么说：{coachPanel.formula}
            </span>
          </div>
          <div className="live-metric-block live-metric-repeat">
            <div className="live-metric-heading">
              <span>重复意思 · 累计</span>
              <MetricCount value={semanticRepeatCount} suffix="次" />
            </div>
            {latestSemanticRepeat ? (
              <div className="live-semantic-pair" title="最近识别到的前后相似表达">
                <span>{latestSemanticRepeat.first}</span>
                <i aria-hidden="true">↔</i>
                <span>{latestSemanticRepeat.second}</span>
              </div>
            ) : <span className="live-metric-empty">完整说完两句后检测</span>}
          </div>
          <div className="live-metric-block">
            <div className="live-metric-heading">
              <span>口头禅 · 累计</span>
              <MetricCount value={fillerCount} suffix="次" />
            </div>
            {latestFiller
              ? <strong className="live-metric-word">{`${latestFiller[0]} × ${latestFiller[1]}`}</strong>
              : <span className="live-metric-empty">停顿定稿后检测</span>}
          </div>
          <div className="live-metric-block">
            <div className="live-metric-heading">
              <span>连续重复 · 累计</span>
              <MetricCount value={stutterCount} suffix="次" />
            </div>
            {latestStutter
              ? <strong className="live-metric-word live-metric-word-stutter">{`${latestStutter[0]} × ${latestStutter[1]}`}</strong>
              : <span className="live-metric-empty">同字词连续出现后检测</span>}
          </div>
        </section>

        <footer className="live-footer">
          <div className="live-recording" aria-label={vs.status === 'listening' ? '正在收音' : meta.label}>
            <span aria-hidden="true" />
          </div>
          <div className="live-time-readout">
            <strong className={timeOver ? 'is-overtime' : ''}>
              {isTimed && remainSec !== null
                ? timeOver ? `+${fmtTime(overtimeSec)}` : fmtTime(remainSec)
                : fmtTime(elapsedSec)}
            </strong>
            <span>{isTimed && remainSec !== null ? timeOver ? '已超时' : '剩余时间' : '本轮已用'}</span>
          </div>
          {interviewProgress ? <InterviewPlanProgress progress={interviewProgress} /> : <LiveWave />}
          <div className="live-telemetry">
            <EmotionIndicator
              data={emotion}
              live={vs.liveMetrics}
              timer={
                isTimed && durationLimit > 0
                  ? { elapsedSec, remainSec, overtimeSec, timeOver, nearEnd, fmt: fmtTime, running: !!startedAt }
                  : undefined
              }
            />
            <span className="live-rate-copy">
              {liveRate !== null ? `${Math.round(liveRate)} 字/分 · ${liveRateLabel}` : '正在等待声音数据'}
            </span>
          </div>
        </footer>
      </div>
    )
  }
}

function MarkedSubtitleToken({
  children,
  kind,
  label,
}: {
  children: ReactNode
  kind: 'filler' | 'repeat'
  label: string
}) {
  return (
    <span className={`live-highlight live-highlight-${kind} live-marked-token is-${kind}`}>
      <span className={`live-inline-label is-${kind}`} aria-hidden="true">{label}</span>
      {children}
      <svg
        className="live-token-underline"
        viewBox="0 0 100 12"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <path
          d="M 1 8 C 24 3, 63 11, 99 3 L 99 7 C 63 13, 24 6, 1 10 Z"
          fill="currentColor"
        />
      </svg>
    </span>
  )
}

function InterviewPlanProgress({ progress }: { progress: InterviewProgress }) {
  const ratio = progress.total > 0 ? Math.min(1, progress.covered / progress.total) : 0
  return (
    <div
      className="live-interview-progress"
      aria-label={`${progress.mode_label}，已覆盖 ${progress.covered}/${progress.total}，当前 ${progress.current_label}`}
    >
      <div>
        <span>{progress.mode_label} · {progress.intensity_label}</span>
        <span>{progress.covered}/{progress.total} 已覆盖</span>
      </div>
      <strong>当前：{progress.current_label}</strong>
      <span className="live-progress-line" aria-hidden="true"><i style={{ transform: `scaleX(${ratio})` }} /></span>
    </div>
  )
}

function CoachRail({ question, goal, coverage }: { question: string; goal: string; coverage: string }) {
  const pathId = 'live-coach-rail-top'
  const path = 'M -40 10 Q 640 116 1320 10'

  return (
    <div className="live-coach-rail live-coach-rail-top" aria-label={`当前题 ${question}，回答目标 ${goal}，覆盖 ${coverage}`}>
      <svg viewBox="0 0 1280 92" preserveAspectRatio="none" aria-hidden="true">
        <defs><path id={pathId} d={path} /></defs>
        <use href={`#${pathId}`} className="live-rail-line" />
        <use href={`#${pathId}`} className="live-rail-line live-rail-line-echo" transform="translate(0 8)" />
      </svg>
      <div className="live-rail-items">
        <span><i className="is-coral" aria-hidden="true" />当前题 · <strong>{question}</strong></span>
        <span><i className="is-blue" aria-hidden="true" />回答目标 · <strong>{goal}</strong></span>
        <span><i className="is-lime" aria-hidden="true" />覆盖 · <strong>{coverage}</strong></span>
      </div>
    </div>
  )
}

function MetricCount({ value, suffix }: { value: number; suffix: string }) {
  const previousValue = useRef(value)
  const [isChanged, setIsChanged] = useState(false)

  useEffect(() => {
    if (value <= previousValue.current) {
      previousValue.current = value
      return
    }
    previousValue.current = value
    setIsChanged(true)
    const timer = window.setTimeout(() => setIsChanged(false), 480)
    return () => window.clearTimeout(timer)
  }, [value])

  return <strong className={`live-metric-count${isChanged ? ' is-changed' : ''}`} aria-label={`${value}${suffix}`}>{value}<small>{suffix}</small></strong>
}

const WAVE_LEVELS = [13, 28, 18, 44, 24, 15, 36, 52, 28, 17, 38, 21, 48, 65, 32, 18, 45, 28, 14, 34, 58, 72, 47, 29, 52, 36, 18, 40, 61, 31, 16, 38, 55, 24, 13, 29, 47, 66, 36, 19, 42, 24, 12]

function LiveWave() {
  return (
    <div className="live-wave" aria-hidden="true">
      {WAVE_LEVELS.map((height, index) => (
        <span
          key={index}
          className={index >= 14 && index <= 18 ? 'is-coral' : index >= 30 && index <= 35 ? 'is-lime' : undefined}
          style={{ height: `${height}%` }}
        />
      ))}
    </div>
  )
}
