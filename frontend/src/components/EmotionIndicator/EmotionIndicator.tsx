import './EmotionIndicator.css'

export interface EmotionData {
  tensionScore: number
  tensionLevel: string
  confidenceScore: number
  confidenceLevel: string
  /** 声学信号（真实声音特征；false=仅文本用词推断） */
  voiceSignal?: boolean
  pitchJitter?: number   // 基频快速颤抖率（去趋势后，越大声音越"发抖"）
  pauseCount?: number    // 停顿次数（本句）
  /** 语速（字/分钟） */
  speechRate?: number
  /** 各信号对紧张度的贡献明细 */
  factors?: Record<string, number>
  /** 是否按个人校准基线评估 */
  calibrated?: boolean
}

/** 实时指标（说话中滚动刷新，来自 live_metrics） */
export interface LiveMetricsData {
  speechRate: number | null       // 字/分
  speechRateLevel: 'fast' | 'normal' | 'slow' | 'unknown'
  tensionScore: number | null
  speechSec: number
}

const RATE_LABEL: Record<string, { text: string; color: string }> = {
  fast: { text: '偏快', color: '#fa541c' },
  normal: { text: '适中', color: '#52c41a' },
  slow: { text: '偏慢', color: '#1677ff' },
  unknown: { text: '…', color: '#8c8c8c' },
}

const TONE_COLOR: Record<string, string> = {
  good: '#52c41a',
  warn: '#faad14',
  bad: '#ff4d4f',
}

/** 紧张度因子中文名 */
const FACTOR_LABELS: Record<string, string> = {
  jitter: '声音颤抖',
  speech_rate: '语速偏离',
  pause: '停顿异常',
  energy: '能量起伏',
  hedge: '用词犹豫',
}

function tone(value: number): string {
  if (value < 40) return 'good'
  if (value < 70) return 'warn'
  return 'bad'
}

/** 一句话建议 */
function tensionAdviceOf(t: number): string {
  if (t >= 70) return '深呼吸、放慢语速，停顿是正常的'
  if (t >= 40) return '有些许紧张属正常，保持当前节奏'
  return '状态放松平稳，保持住'
}

function confidenceAdviceOf(c: number): string {
  if (c >= 60) return '表达果断，继续保持'
  if (c >= 40) return '少用「可能、大概」，给出明确结论'
  return '先给结论再讲理由，减少模糊词'
}

/** 把 factors 明细转成一句人话（为什么紧张） */
function explainFactors(factors: Record<string, number> | undefined, tension: number): string {
  if (!factors) return ''
  const parts: string[] = []
  for (const [k, label] of Object.entries(FACTOR_LABELS)) {
    const v = factors[k]
    if (typeof v === 'number' && v >= 5) {
      parts.push(`${label} ${Math.round(v)}`)
    }
  }
  if (!parts.length) return tension >= 40 ? '各信号接近你的平时水平' : '各信号均在你的基线范围内'
  return `主要贡献：${parts.join(' · ')}（分值越大影响越大）`
}

/** 限时计时（驾驶舱模块：与实时指标并排，限时场景显示） */
export interface TimerData {
  elapsedSec: number
  remainSec: number | null
  timeOver: boolean
  nearEnd: boolean
  fmt: (s: number) => string
}

/** 底部状态条：⚡实时大数字 + 计时 + 紧张/自信双指标 + 单行明细 */
export default function EmotionIndicator({
  data,
  live,
  timer,
}: { data: EmotionData | null; live?: LiveMetricsData | null; timer?: TimerData }) {
  const tension = data?.tensionScore ?? 0
  const confidence = data?.confidenceScore ?? 0
  const tTone = tone(tension)
  const cTone = tone(100 - confidence)

  const hasLive = !!live && (live.speechRate !== null || live.tensionScore !== null)
  const liveTension = live?.tensionScore ?? null
  const shownTension = liveTension ?? (data ? tension : null)
  const shownTensionTone = shownTension !== null ? tone(shownTension) : 'good'
  const rateMeta = RATE_LABEL[live?.speechRateLevel ?? 'unknown'] ?? RATE_LABEL.unknown
  const shownRate = live?.speechRate ?? data?.speechRate ?? null

  // 单行明细：声学依据 + 因子贡献 + 未校准提醒
  const detailParts: string[] = []
  if (data?.voiceSignal) {
    detailParts.push(
      `颤抖 ${(data.pitchJitter ?? 0).toFixed(3)}${tension >= 55 && (data.factors?.jitter ?? 0) >= 10 ? '（发抖）' : ''}`
    )
    if (data.pauseCount) detailParts.push(`本句停顿 ${data.pauseCount} 次`)
  }
  if (data?.factors && Object.keys(data.factors).length > 0) {
    detailParts.push(explainFactors(data.factors, tension))
  }
  if (data && !data.calibrated && data.voiceSignal) {
    detailParts.push('⚠ 未做朗读校准，按人群默认基线评估（设置页可校准）')
  }

  return (
    <div className="emotion-bar">
      {/* 第一行：⚡实时大数字 / 状态徽标 */}
      <div className="eb-row">
        {hasLive ? (
          <>
            <span className="eb-badge">⚡ 实时</span>
            {shownRate !== null && (
              <span className="eb-item">
                <span className="eb-big" style={{ color: rateMeta.color }}>{Math.round(shownRate)}</span>
                <span className="eb-unit">字/分 · <b style={{ color: rateMeta.color }}>{rateMeta.text}</b></span>
              </span>
            )}
            {shownTension !== null && (
              <span className="eb-item">
                <span className="eb-big" style={{ color: TONE_COLOR[shownTensionTone] }}>{Math.round(shownTension)}</span>
                <span className="eb-unit">紧张度 /100</span>
              </span>
            )}
            {!!live && live.speechSec > 0 && (
              <span className="eb-item">
                <span className="eb-big" style={{ color: '#595959' }}>{live.speechSec.toFixed(0)}</span>
                <span className="eb-unit">秒 · 连续发声</span>
              </span>
            )}
          </>
        ) : (
          <span className="eb-badge eb-badge-dim">我的状态</span>
        )}
        {/* 限时计时（驾驶舱）：正常蓝 / 剩1分钟橙 / 到点红 */}
        {timer && (
          <span className="eb-item" style={{ marginLeft: hasLive ? undefined : 0 }}>
            <span
              className="eb-big"
              style={{
                color: timer.timeOver ? '#ff4d4f' : timer.nearEnd ? '#fa8c16' : '#1677ff',
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {timer.remainSec !== null ? timer.fmt(timer.remainSec) : timer.fmt(timer.elapsedSec)}
            </span>
            <span className="eb-unit">
              {timer.remainSec !== null ? (
                <>
                  剩余<br />
                  <span style={{ color: '#bfbfbf' }}>已用 {timer.fmt(timer.elapsedSec)}</span>
                </>
              ) : (
                <>已用时间</>
              )}
            </span>
          </span>
        )}
        <span className="eb-tags">
          {data?.voiceSignal && <span className="eb-tag eb-tag-green">声学信号</span>}
          {data?.calibrated && <span className="eb-tag eb-tag-blue">个人基线</span>}
        </span>
      </div>

      {/* 第二行：紧张度 / 自信度 双指标 */}
      <div className="eb-metrics">
        <div className="eb-metric">
          <div className="eb-metric-head">
            <span className="eb-name">紧张度</span>
            <span className={`eb-level ${tTone}`}>{data?.tensionLevel ?? '待检测'}</span>
            <span className={`eb-score ${tTone}`}>{data ? `${Math.round(tension)}%` : '--'}</span>
          </div>
          <div className="eb-bar">
            <div className={`eb-fill ${tTone}`} style={{ width: `${tension}%` }} />
          </div>
          <div className="eb-sub">💡 {data ? tensionAdviceOf(tension) : '开口后实时评估，按偏离你平时习惯的程度评判'}</div>
        </div>
        <div className="eb-metric">
          <div className="eb-metric-head">
            <span className="eb-name">自信度</span>
            <span className={`eb-level ${cTone}`}>{data?.confidenceLevel ?? '待检测'}</span>
            <span className={`eb-score ${cTone}`}>{data ? `${Math.round(confidence)}%` : '--'}</span>
          </div>
          <div className="eb-bar">
            <div className={`eb-fill ${cTone}`} style={{ width: `${confidence}%` }} />
          </div>
          <div className="eb-sub">💡 {data ? confidenceAdviceOf(confidence) : '果断用词与明确结论会拉高自信度'}</div>
        </div>
      </div>

      {/* 第三行：单行明细 */}
      {detailParts.length > 0 && (
        <div className="eb-detail" title={detailParts.join('　·　')}>{detailParts.join('　·　')}</div>
      )}
    </div>
  )
}
