import { Button } from 'antd'
import {
  AimOutlined,
  AudioOutlined,
  BarChartOutlined,
  DownOutlined,
  FileTextOutlined,
  SoundOutlined,
  UpOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useEffect, useState, type ReactNode } from 'react'
import { apiService } from '@/services/api'
import type { ScenarioOut } from '@/types/interview'
import VoiceCalibration from '@/components/VoiceCalibration'
import './Home.css'

/** 场景卡片图标与主色（静态配置，与后端 scenario key 对齐）。 */
const CARD_META: Record<string, { icon: ReactNode; color: string }> = {
  interview: { icon: <AimOutlined />, color: '#1677ff' },
  presentation: { icon: <BarChartOutlined />, color: '#722ed1' },
  speech: { icon: <AudioOutlined />, color: '#13c2c2' },
}

export default function Home() {
  const nav = useNavigate()
  const [scenarios, setScenarios] = useState<ScenarioOut[]>([])
  const [calibrated, setCalibrated] = useState<boolean | null>(null)  // null=查询中
  const [calibOpen, setCalibOpen] = useState(false)                   // 校准卡片展开

  useEffect(() => {
    document.body.classList.add('is-home-showcase')
    return () => document.body.classList.remove('is-home-showcase')
  }, [])

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
    <main className="home-showcase">
      <header className="home-showcase-header">
        <button type="button" className="home-brand" onClick={() => nav('/')}>表达能力训练器</button>
        <div className="home-header-actions">
          <button type="button" onClick={() => nav('/report/demo')}><FileTextOutlined /> 示例报告</button>
          <button type="button" onClick={() => nav('/settings')}>设置</button>
        </div>
      </header>

      <section className="home-hero" aria-labelledby="home-hero-title">
        <div className="home-hero-orbit home-hero-orbit-top" aria-hidden="true" />
        <p className="home-kicker">REAL-TIME SPEECH COACH / 03 CORE SIGNALS</p>
        <h1 id="home-hero-title">让每一句话<br /><span>都有分量。</span></h1>
        <p className="home-hero-copy">把口癖、重复和节奏变成看得见的即时反馈。先听见自己，再让观点真正被记住。</p>
        <div className="home-signal-preview" aria-label="实时表达反馈能力">
          <span><i className="is-coral" />口癖 <b>更干净</b></span>
          <span><i className="is-lime" />重复 <b>更精准</b></span>
          <span><i className="is-blue" />节奏 <b>更有力</b></span>
        </div>
        <div className="home-hero-orbit home-hero-orbit-bottom" aria-hidden="true" />
      </section>

      <section className="home-scenario-section" aria-labelledby="scenario-title">
        <div className="home-section-heading">
          <div>
            <p>CHOOSE YOUR STAGE</p>
            <h2 id="scenario-title">选择一个真实场景，开始表达。</h2>
          </div>
          <span>实时字幕 · 核心提示 · 复盘报告</span>
        </div>

        <div className="home-scenario-grid">
          {scenarios.map((s, index) => {
            const meta = CARD_META[s.key] ?? { icon: <SoundOutlined />, color: '#8dbaff' }
            return (
              <button
                type="button"
                className="home-scenario-card"
                key={s.key}
                onClick={() => nav(`/training?scenario=${s.key}`)}
              >
                <span className="home-scenario-index">0{index + 1}</span>
                <span className="home-scenario-icon" style={{ color: meta.color }} aria-hidden="true">{meta.icon}</span>
                <strong>{s.name}</strong>
                <span className="home-scenario-description">{s.description}</span>
                <span className="home-scenario-meta">AI {s.role_name}{s.timed ? ' · 限时训练' : ' · 实时追问'}</span>
                <span className="home-scenario-enter">进入训练 <b>→</b></span>
              </button>
            )
          })}
        </div>
      </section>

      {calibrated !== true && (
        <section className="home-calibration" aria-label="声音校准">
          <div><SoundOutlined aria-hidden="true" /><span><b>30 秒声音校准</b>建立你的个人语速与停顿基线，只比较你的变化。</span></div>
          <Button type="primary" onClick={() => setCalibOpen(true)}>开始校准</Button>
        </section>
      )}
      {calibrated === true && (
        <button type="button" className="home-calibration-ready" onClick={() => setCalibOpen((v) => !v)}>
          <SoundOutlined /> 声音校准已就绪 {calibOpen ? <UpOutlined /> : <DownOutlined />}
        </button>
      )}
      {calibOpen && (
        <div className="home-calibration-panel">
          <VoiceCalibration
            onChanged={(ok) => {
              setCalibrated(ok)
              if (ok) setCalibOpen(false)
            }}
          />
        </div>
      )}
    </main>
  )
}
