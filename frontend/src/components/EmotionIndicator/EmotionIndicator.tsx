import './EmotionIndicator.css'

export interface EmotionData {
  /** 后端兼容字段：前端只解释为表达信号相对基线的偏离，不解释为心理状态。 */
  tensionScore: number
  tensionLevel: string
  /** 文本规则得分：前端展示为表达明确度。 */
  confidenceScore: number
  confidenceLevel: string
  /** 声学信号（真实声音特征；false=仅文本用词推断） */
  voiceSignal?: boolean
  pitchJitter?: number   // 基频快速颤抖率（去趋势后，越大声音越"发抖"）
  pauseCount?: number    // 正文长停顿次数（本句，不含录音首尾）
  hesitationCount?: number // 正文短停顿次数（0.2–0.5 秒）
  /** 语速（字/分钟） */
  speechRate?: number
  /** 各可观察信号的贡献明细 */
  factors?: Record<string, number>
  /** 是否按个人校准基线评估 */
  calibrated?: boolean
}

/** 实时指标（说话中滚动刷新，来自 live_metrics） */
export interface LiveMetricsData {
  speechRate: number | null       // 字/分
  speechRateLevel: 'fast' | 'normal' | 'slow' | 'unknown'
  speechSec: number
}

const RATE_LABEL: Record<string, { text: string; color: string }> = {
  fast: { text: '偏快', color: '#fa541c' },
  normal: { text: '适中', color: '#52c41a' },
  slow: { text: '偏慢', color: '#1677ff' },
  unknown: { text: '…', color: '#8c8c8c' },
}

function positiveTone(value: number): string {
  if (value >= 60) return 'good'
  if (value >= 30) return 'warn'
  return 'bad'
}

function clarityAdviceOf(c: number): string {
  if (c >= 60) return '结论和措辞比较明确，继续保持'
  if (c >= 40) return '少用「可能、大概」，给出明确结论'
  return '先给结论再讲理由，减少模糊词'
}

function pauseObservationLabel(count: number | undefined): string {
  if (count === undefined) return '等待声音片段'
  if (count === 0) return '未检测到'
  if (count <= 2) return '少量'
  return '较多'
}

/** 限时计时（驾驶舱模块：与实时指标并排，限时场景显示） */
export interface TimerData {
  elapsedSec: number
  remainSec: number | null
  overtimeSec: number
  timeOver: boolean
  nearEnd: boolean
  fmt: (s: number) => string
  /** false=开场白未播完，计时还没开始 */
  running?: boolean
}

/** 底部状态条：实时语速 + 计时 + 连贯性/明确度 + 可观察事实。 */
export default function EmotionIndicator({
  data,
  live,
  timer,
}: { data: EmotionData | null; live?: LiveMetricsData | null; timer?: TimerData }) {
  const confidence = data?.confidenceScore ?? 0
  const clarityTone = positiveTone(confidence)

  const hasLive = !!live && live.speechRate !== null
  const rateMeta = RATE_LABEL[live?.speechRateLevel ?? 'unknown'] ?? RATE_LABEL.unknown
  const shownRate = live?.speechRate ?? data?.speechRate ?? null

  // 单行明细：声学依据 + 因子贡献 + 未校准提醒
  const detailParts: string[] = []
  if (data?.voiceSignal) {
    detailParts.push(`正文短停顿 ${data.hesitationCount ?? 0} 处`)
    detailParts.push(`正文长停顿 ${data.pauseCount ?? 0} 处`)
    detailParts.push('声音特征已记录，不据此推断紧张')
  }

  return (
    <div className="emotion-bar">
      {/* 第一行：实时数据 / 状态徽标 */}
      <div className="eb-row">
        {hasLive ? (
          <>
            <span className="eb-badge">实时分析</span>
            {shownRate !== null && (
              <span className="eb-item">
                <span className="eb-big" style={{ color: rateMeta.color }}>{Math.round(shownRate)}</span>
                <span className="eb-unit">字/分 · <b style={{ color: rateMeta.color }}>{rateMeta.text}</b></span>
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
        {/* 限时计时（驾驶舱）：等待开场灰 / 正常蓝 / 剩1分钟橙 / 到点红 */}
        {timer && (timer.running === false ? (
          <span className="eb-item">
            <span className="eb-big" style={{ color: '#bfbfbf', fontVariantNumeric: 'tabular-nums' }}>
              {timer.remainSec !== null ? timer.fmt(timer.remainSec) : '--:--'}
            </span>
            <span className="eb-unit">剩余<br /><span style={{ color: '#d9d9d9' }}>等开场结束</span></span>
          </span>
        ) : (
          <span className="eb-item" style={{ marginLeft: hasLive ? undefined : 0 }}>
            <span
              className="eb-big"
              style={{
                color: timer.timeOver ? '#ff4d4f' : timer.nearEnd ? '#fa8c16' : '#1677ff',
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {timer.timeOver
                ? `+${timer.fmt(timer.overtimeSec)}`
                : timer.remainSec !== null
                  ? timer.fmt(timer.remainSec)
                  : timer.fmt(timer.elapsedSec)}
            </span>
            <span className="eb-unit">
              {timer.timeOver ? (
                <>已超时<br /><span style={{ color: '#ffaaa0' }}>仍在录音</span></>
              ) : timer.remainSec !== null ? (
                <>
                  剩余<br />
                  <span style={{ color: '#bfbfbf' }}>已用 {timer.fmt(timer.elapsedSec)}</span>
                </>
              ) : (
                <>已用时间</>
              )}
            </span>
          </span>
        ))}
        <span className="eb-tags">
          {data?.voiceSignal && <span className="eb-tag eb-tag-green">声学信号</span>}
          {data?.calibrated && <span className="eb-tag eb-tag-blue">个人基线</span>}
        </span>
      </div>

      {/* 第二行：连贯性 / 表达明确度 */}
      <div className="eb-metrics">
        <div className="eb-metric">
          <div className="eb-metric-head">
            <span className="eb-name">短停顿观察</span>
            <span className={`eb-level ${(data?.hesitationCount ?? 0) > 2 ? 'warn' : 'good'}`}>
              {pauseObservationLabel(data?.hesitationCount)}
            </span>
            <span className={`eb-score ${(data?.hesitationCount ?? 0) > 2 ? 'warn' : 'good'}`}>
              {data?.hesitationCount === undefined ? '—' : `${data.hesitationCount} 处`}
            </span>
          </div>
          <div className="eb-sub">
            可能是卡顿，也可能是修辞停顿；单独观察，不直接评分
          </div>
        </div>
        <div className="eb-metric">
          <div className="eb-metric-head">
            <span className="eb-name">表达明确度</span>
            <span className={`eb-level ${clarityTone}`}>{data ? (confidence >= 60 ? '明确' : confidence >= 40 ? '可以更直接' : '模糊措辞较多') : '待检测'}</span>
            <span className={`eb-score ${clarityTone}`}>{data ? `${Math.round(confidence)}%` : '—'}</span>
          </div>
          <div className="eb-bar">
            <div
              className={`eb-fill ${clarityTone}`}
              style={{ transform: `scaleX(${confidence / 100})` }}
            />
          </div>
          <div className="eb-sub">
            {data ? clarityAdviceOf(confidence) : '根据模糊词、口癖和重复表达给出训练提示'}
          </div>
        </div>
      </div>

      {/* 第三行：单行明细 */}
      {detailParts.length > 0 && (
        <div className="eb-detail" title={detailParts.join('　·　')}>{detailParts.join('　·　')}</div>
      )}
    </div>
  )
}
