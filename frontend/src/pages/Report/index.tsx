import { useEffect, useState } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import { Button, Progress, Spin, Tag, message } from 'antd'
import {
  ArrowLeftOutlined,
  CopyOutlined,
  DownloadOutlined,
  FileTextOutlined,
  ReloadOutlined,
  RobotOutlined,
} from '@ant-design/icons'
import { apiService } from '@/services/api'
import { DEMO_REPORT } from '@/data/demoReport'
import { SCENARIOS, STAGE_LABELS } from '@/types/interview'
import type { ReportData } from '@/types/report'
import './Report.css'

function stageLabel(stage: string, scenario: string) {
  return SCENARIOS[scenario]?.stageLabels[stage] ?? STAGE_LABELS[stage] ?? '对话环节'
}

export default function Report() {
  const { id } = useParams()
  const nav = useNavigate()
  const [searchParams] = useSearchParams()
  const scenarioQuery = searchParams.get('scenario')
  const scenarioNameLoading = SCENARIOS[scenarioQuery || 'interview']?.name ?? '训练'
  const [report, setReport] = useState<ReportData | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  const [retryCount, setRetryCount] = useState(0)
  const [manualAction, setManualAction] = useState<'copy' | 'download' | 'regenerate' | null>(null)

  useEffect(() => {
    document.body.classList.add('is-operate-surface')
    return () => document.body.classList.remove('is-operate-surface')
  }, [])

  useEffect(() => {
    if (!id) {
      setLoadError(true)
      setLoading(false)
      return
    }
    if (id === 'demo') {
      setReport(DEMO_REPORT)
      setLoadError(false)
      setLoading(false)
      return
    }

    let cancelled = false
    setLoading(true)
    setLoadError(false)
    apiService.generateReport(id)
      .then((result) => {
        if (!cancelled) setReport(result)
      })
      .catch(() => {
        if (!cancelled) {
          setLoadError(true)
          message.error('训练记录暂时无法读取，请确认本地服务正在运行')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [id, retryCount])

  if (loading) {
    return (
      <main className="operate-state">
        <div className="operate-state-inner">
          <Spin size="large" />
          <h1>正在整理{scenarioNameLoading}记录</h1>
          <p>本地表达数据会直接生成；已配置内容模型时，将同时完成场景化评价。</p>
        </div>
      </main>
    )
  }

  if (!report || loadError) {
    return (
      <main className="operate-state">
        <div className="operate-state-inner">
          <ReloadOutlined className="operate-state-icon" />
          <h1>训练记录暂时无法读取</h1>
          <p>请确认本地服务正在运行后重试。是否配置内容分析模型不会影响记录保存。</p>
          <div className="operate-state-actions">
            <Button type="primary" icon={<ReloadOutlined />} onClick={() => setRetryCount((count) => count + 1)}>重新生成</Button>
            <Button onClick={() => nav('/')}>返回首页</Button>
          </div>
        </div>
      </main>
    )
  }

  const metrics = report.expression_metrics || {}
  const voiceReference = report.voice_reference
  const axes = report.axes || []
  const suggestions = report.suggestions || {}
  const scenarioMeta = SCENARIOS[report.scenario] ?? SCENARIOS.interview
  const aiRoleName = report.scenario_name && !SCENARIOS[report.scenario]
    ? `${report.scenario_name}主持`
    : scenarioMeta.role
  const isDemo = report.session_id === 'demo'
  const isScoreComplete = report.overall_score != null && report.score_coverage === 1
  const semanticStatus = report.semantic_status ?? (isScoreComplete ? 'complete' : 'failed')
  const manualAnalysisAvailable = !isDemo && (semanticStatus === 'unconfigured' || semanticStatus === 'failed')
  const coverage = Math.round((report.score_coverage ?? 0) * 100)
  const scoreConstraints = report.score_constraints ?? []
  const interviewCoverage = report.scenario === 'interview' ? report.interview_coverage : null
  const hasPracticeAdvice = (suggestions.short_term?.length ?? 0) > 0 || (suggestions.mid_term?.length ?? 0) > 0

  const handleManualPackage = async (action: 'copy' | 'download') => {
    if (!id) return
    setManualAction(action)
    try {
      const data = await apiService.getManualAnalysis(id)
      if (action === 'copy') {
        await copyText(data.prompt)
        message.success('分析提示词已复制，可以直接粘贴给任意语言模型')
      } else {
        downloadText(data.filename, data.markdown)
        message.success('完整训练记录与分析提示词已下载')
      }
    } catch {
      message.error(action === 'copy' ? '复制失败，请重试' : '下载失败，请重试')
    } finally {
      setManualAction(null)
    }
  }

  const handleFullAnalysis = async () => {
    if (!id) return
    setManualAction('regenerate')
    try {
      const config = await apiService.getConfig()
      if (!config.llm.ready) {
        message.info('先配置内容分析模型，现有训练记录不会丢失')
        nav('/settings')
        return
      }
      const regenerated = await apiService.regenerateReport(id)
      setReport(regenerated)
      if (regenerated.semantic_status === 'complete') {
        message.success('完整内容分析已生成')
      } else {
        message.warning('在线内容分析仍未完成，本地结果和导出材料不受影响')
      }
    } catch {
      message.error('在线内容分析未完成，本地结果和训练记录不受影响')
    } finally {
      setManualAction(null)
    }
  }

  return (
    <main className="operate-page report-page">
      <div className="operate-container">
        <header className="operate-heading report-heading">
          <div>
            <span className="report-eyebrow">训练复盘 · {scenarioMeta.name}</span>
            <h1>{scenarioMeta.name}报告</h1>
            <p>把可复核的表达信号、能力维度和下一步练习放在同一份报告里。</p>
          </div>
          <div className="operate-heading-actions">
            {report.scenario === 'interview' && !isDemo && id && (
              <Button onClick={() => nav(`/training?scenario=interview&mode=weakness&sourceSession=${id}`)}>
                针对薄弱项再练
              </Button>
            )}
            <Button
              icon={<DownloadOutlined />}
              disabled={isDemo}
              title={isDemo ? '示例报告不支持导出' : undefined}
              onClick={() => {
                if (id) window.open(`/api/v1/reports/${id}/pdf`, '_blank')
              }}
            >
              {isDemo ? '示例不可导出' : '导出 PDF'}
            </Button>
            <Button icon={<ArrowLeftOutlined />} onClick={() => nav('/')}>返回首页</Button>
          </div>
        </header>

        {manualAnalysisAvailable && (
          <section className={`report-manual-analysis is-${semanticStatus}`} aria-labelledby="manual-analysis-title">
            <div className="report-manual-copy">
              <span>{semanticStatus === 'unconfigured' ? '本地模式' : '本地结果已保留'}</span>
              <h2 id="manual-analysis-title">
                {semanticStatus === 'unconfigured' ? '不配置模型，也能完成这次训练' : '在线内容分析未完成，不影响本地复盘'}
              </h2>
              <p>
                字幕、口头禅、重复、停顿和完整对话已经保存在本机。你可以复制一份结构化提示词交给任意语言模型，或下载包含训练记录与本地信号的 Markdown 材料。
              </p>
              <small>提交给外部模型前，请检查材料中是否包含简历、公司信息或其他敏感内容。</small>
            </div>
            <div className="report-manual-actions">
              <Button
                type="primary"
                icon={<CopyOutlined />}
                loading={manualAction === 'copy'}
                disabled={manualAction !== null && manualAction !== 'copy'}
                onClick={() => void handleManualPackage('copy')}
              >
                复制分析提示词
              </Button>
              <Button
                icon={<FileTextOutlined />}
                loading={manualAction === 'download'}
                disabled={manualAction !== null && manualAction !== 'download'}
                onClick={() => void handleManualPackage('download')}
              >
                下载完整材料
              </Button>
              <Button
                icon={<RobotOutlined />}
                loading={manualAction === 'regenerate'}
                disabled={manualAction !== null && manualAction !== 'regenerate'}
                onClick={() => void handleFullAnalysis()}
              >
                {semanticStatus === 'unconfigured' ? '配置后生成完整报告' : '重新尝试在线分析'}
              </Button>
            </div>
          </section>
        )}

        <section className="report-overview" aria-labelledby="report-overview-title">
          <div className={`report-score ${isScoreComplete ? scoreBand(report.overall_score) : 'is-pending'}`}>
            <strong>{isScoreComplete ? report.overall_score?.toFixed(1) : '—'}</strong>
            <span id="report-overview-title">
              {isScoreComplete ? '本场景综合评分' : semanticStatus === 'unconfigured' ? '内容评分未生成' : '综合评分未完成'}
            </span>
          </div>
          <div className="report-summary">
            <div className="report-meta">
              {report.position && <Tag>{report.position}</Tag>}
              {report.level && <Tag>{report.level}</Tag>}
              {isDemo && <Tag color="blue">示例数据</Tag>}
            </div>
            <p>{report.summary || '本次训练已完成，详细信号与维度结果如下。'}</p>
            {scoreConstraints.length > 0 && (
              <div className="report-score-constraint" role="status" aria-label="关键任务尚未达标">
                <strong>关键任务尚未达标</strong>
                <ul>
                  {scoreConstraints.map((constraint, index) => <li key={`${constraint.reason}-${index}`}>{constraint.reason}</li>)}
                </ul>
              </div>
            )}
            <div className="report-coverage">
              <span>{semanticStatus === 'complete' ? '评价覆盖' : '本地评价覆盖'} {coverage}%</span>
            </div>
          </div>
        </section>

        {interviewCoverage && (
          <section className="report-interview-coverage" aria-labelledby="interview-coverage-title">
            <div>
              <h2 id="interview-coverage-title">本次面试覆盖</h2>
              <p>{interviewCoverage.mode_label} · {interviewCoverage.intensity_label} · 追问 {interviewCoverage.followups_used}/{interviewCoverage.followup_budget}</p>
            </div>
            <div className="report-coverage-map">
              <span>已练习</span>
              <p>{interviewCoverage.covered_labels.length > 0 ? interviewCoverage.covered_labels.join(' · ') : '有效回答不足'}</p>
              {(interviewCoverage.remaining_labels.length > 0 || interviewCoverage.skipped_labels.length > 0) && (
                <>
                  <span>未评估</span>
                  <p>{[...interviewCoverage.skipped_labels, ...interviewCoverage.remaining_labels].join(' · ')}</p>
                </>
              )}
            </div>
          </section>
        )}

        <section className="report-section report-signal-section" aria-labelledby="expression-signals-title">
          <SectionHeading index="01" title="表达信号" description="先看可核对的数据，再看不参与评分的声音与节奏参考。" id="expression-signals-title" />
          <div className="report-metric-grid">
            <Metric label="语速" value={metrics.speech_rate > 0 ? `${metrics.speech_rate}` : '—'} unit={metrics.speech_rate > 0 ? '字/分' : undefined} sub={metrics.speech_rate > 0 ? metrics.speech_rate_level : '缺少有效发言时长'} />
            <Metric label="有效字数" value={`${metrics.total_words ?? 0}`} />
            <Metric label="明确口癖" value={`${metrics.filler_total ?? 0}`} unit="次" tone={(metrics.filler_total ?? 0) > 8 ? 'warn' : undefined} />
            <Metric label="紧邻用词重复" value={`${((metrics.repetition_rate ?? 0) * 100).toFixed(1)}`} unit="%" />
            <Metric label="表达断裂" value={`${metrics.expression_break_count ?? 0}`} unit="处" tone={(metrics.expression_break_count ?? 0) > 0 ? 'warn' : undefined} sub="句子未说完整便停住、重来或紧邻重复" />
            <Metric label="正文短停顿" value={metrics.short_pause_count == null ? '—' : `${metrics.short_pause_count}`} unit={metrics.short_pause_count == null ? undefined : '处'} sub={metrics.short_pause_rate == null ? '缺少有效声音数据' : `${metrics.short_pause_rate} 处/分钟`} />
            <Metric label="正文长停顿" value={metrics.long_pause_count == null ? '—' : `${metrics.long_pause_count}`} unit={metrics.long_pause_count == null ? undefined : '处'} sub={metrics.long_pause_rate == null ? '已排除录音首尾等待' : `${metrics.long_pause_rate} 处/分钟 · 已排除录音首尾等待`} />
          </div>
          {metrics.filler_top && metrics.filler_top.length > 0 && (
            <div className="report-filler-line">
              <span>识别到的明确口癖</span>
              <div>{metrics.filler_top.map((filler) => <Tag key={filler.word}>{filler.word} × {filler.count}</Tag>)}</div>
            </div>
          )}
          {metrics.expression_break_examples && metrics.expression_break_examples.length > 0 && (
            <div className="report-break-evidence">
              <strong>本次识别到的断裂片段</strong>
              <ul>
                {metrics.expression_break_examples.map((item, index) => (
                  <li key={`${item.excerpt}-${index}`}>
                    <q>{item.excerpt}</q>
                    <span>{item.description}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {voiceReference?.available && (
            <VoiceReferencePanel reference={voiceReference} />
          )}
        </section>

        <section className="report-section" aria-labelledby="ability-title">
          <SectionHeading index="02" title="能力维度" description="按当前训练场景的评价标准拆解，便于定位真正值得练习的部分。" id="ability-title" />
          <div className="report-axis-list">
            {axes.length > 0
              ? axes.map((axis) => <AxisRow key={axis.key} axis={axis} semanticStatus={semanticStatus} />)
              : <p className="report-empty">本次没有足够信息生成能力维度。</p>}
          </div>
        </section>

        {hasPracticeAdvice && (
          <section className="report-section" aria-labelledby="practice-title">
            <SectionHeading index="03" title="下一步练习" description="先解决一周内可执行的问题，再建立一个月的稳定表达习惯。" id="practice-title" />
            <div className="report-advice-grid">
              <AdviceList label="本周优先" items={suggestions.short_term ?? []} />
              <AdviceList label="本月方向" items={suggestions.mid_term ?? []} />
            </div>
          </section>
        )}

        {report.professional_advice?.length > 0 && (
          <section className="report-section" aria-labelledby="professional-title">
            <SectionHeading index="04" title={`${scenarioMeta.name}专项建议`} description="结合场景目标给出的内容与呈现建议。" id="professional-title" />
            <div className="report-professional-list">
              {report.professional_advice.map((advice, index) => (
                <article key={`${advice.topic}-${index}`}>
                  <span>{String(index + 1).padStart(2, '0')}</span>
                  <div><h3>{advice.topic}</h3><p>{advice.detail}</p></div>
                </article>
              ))}
            </div>
          </section>
        )}

        <section className="report-section report-dialogue-section" aria-labelledby="dialogue-title">
          <SectionHeading index="05" title="完整训练记录" description="按需展开，用于回听时核对原始上下文。" id="dialogue-title" />
          <details className="report-dialogues">
            <summary>查看完整记录 <span>{report.dialogues?.length ?? 0} 条</span></summary>
            <div className="report-dialogue-list">
              {report.dialogues?.length > 0
                ? report.dialogues.map((dialogue, index) => (
                  <article className={dialogue.role === 'ai' ? 'is-ai' : 'is-user'} key={`${dialogue.seq}-${index}`}>
                    <header>
                      <strong>{dialogue.role === 'ai' ? aiRoleName : '我'}</strong>
                      <span>{stageLabel(dialogue.stage, report.scenario)}</span>
                    </header>
                    <p>{dialogue.text}</p>
                  </article>
                ))
                : <p className="report-empty">没有可展示的训练记录。</p>}
            </div>
          </details>
        </section>
      </div>
    </main>
  )
}

function SectionHeading({ index, title, description, id }: { index: string; title: string; description: string; id: string }) {
  return (
    <header className="report-section-heading">
      <span>{index}</span>
      <div><h2 id={id}>{title}</h2><p>{description}</p></div>
    </header>
  )
}

function Metric({ label, value, unit, sub, tone }: { label: string; value: string; unit?: string; sub?: string; tone?: 'warn' }) {
  return (
    <div className={`report-metric ${tone ? `is-${tone}` : ''}`}>
      <span>{label}</span>
      <strong>{value}{unit && <small>{unit}</small>}</strong>
      {sub && <p>{sub}</p>}
    </div>
  )
}

function VoiceReferencePanel({ reference }: { reference: NonNullable<ReportData['voice_reference']> }) {
  return (
    <article className="report-voice-reference" aria-labelledby="voice-reference-title">
      <header>
        <div>
          <h3 id="voice-reference-title">声音与节奏参考</h3>
          <p>{reference.summary}</p>
        </div>
        <span>{reference.confidence}可信度 · 不计分</span>
      </header>
      <div className="report-voice-dimensions">
        {reference.dimensions.map((dimension) => (
          <div key={dimension.key}>
            <span>{dimension.label}</span>
            <strong>{dimension.value}</strong>
            <p>{dimension.detail}</p>
          </div>
        ))}
      </div>
      <div className="report-voice-basis">
        <strong>判断依据</strong>
        <ul>{reference.basis.map((item) => <li key={item}>{item}</li>)}</ul>
        <p>{reference.confidence_note}</p>
      </div>
    </article>
  )
}

function AxisRow({ axis, semanticStatus }: { axis: ReportData['axes'][number]; semanticStatus: NonNullable<ReportData['semantic_status']> }) {
  const score = axis.score
  const pendingFeedback = axis.source === 'llm' && score == null
    ? semanticStatus === 'unconfigured'
      ? '该维度需要理解完整对话语义。你可以复制分析提示词，交给任意语言模型完成。'
      : semanticStatus === 'insufficient'
        ? '有效发言不足，暂时无法评价该维度。'
        : '本次在线内容分析未完成，可以稍后重试或使用导出材料。'
    : '该维度由可复核表达信号计算。'
  return (
    <article className="report-axis-row">
      <div className="report-axis-title">
        <span>{axis.weight}% 权重</span>
        <h3>{axis.label}</h3>
        <p>{axis.description}</p>
      </div>
      <div className="report-axis-score">
        <strong>{score == null ? '—' : Math.round(score)}</strong>
        <Progress percent={score ?? 0} showInfo={false} strokeColor={scoreColor(score)} trailColor="#dfe4eb" />
      </div>
      <div className="report-axis-detail">
        <p>{axis.feedback || pendingFeedback}</p>
        {axis.evidence && axis.evidence.length > 0 && (
          <ul>{axis.evidence.map((evidence, index) => <li key={index}>{evidence}</li>)}</ul>
        )}
      </div>
    </article>
  )
}

async function copyText(text: string) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text)
    return
  }
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.select()
  const copied = document.execCommand('copy')
  textarea.remove()
  if (!copied) throw new Error('copy failed')
}

function downloadText(filename: string, text: string) {
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

function AdviceList({ label, items }: { label: string; items: string[] }) {
  return (
    <article className="report-advice-list">
      <h3>{label}</h3>
      {items.length > 0
        ? <ol>{items.map((item, index) => <li key={index}><span>{String(index + 1).padStart(2, '0')}</span><p>{item}</p></li>)}</ol>
        : <p className="report-empty">暂无建议。</p>}
    </article>
  )
}

function scoreColor(score: number | null) {
  if (score == null) return '#9aa6b6'
  if (score >= 85) return '#2e9b72'
  if (score >= 70) return '#477df0'
  return '#e85f50'
}

function scoreBand(score: number | null) {
  if (score == null) return 'is-pending'
  if (score >= 85) return 'is-strong'
  if (score >= 70) return 'is-steady'
  return 'is-developing'
}
