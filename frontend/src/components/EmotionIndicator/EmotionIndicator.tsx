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
  fast: { text: '偏快', color: '#d46b08' },
  normal: { text: '适中', color: '#52c41a' },
  slow: { text: '偏慢', color: '#1677ff' },
  unknown: { text: '…', color: '#999' },
}

/** 紧张度因子中文名 */
const FACTOR_LABELS: Record<string, string> = {
  jitter: '声音颤抖',
  speech_rate: '语速偏离',
  pause: '停顿异常',
  energy: '能量起伏',
  hedge: '用词犹豫',
}

/** 通俗解读 + 行动建议 */
function interpret(tension: number, confidence: number) {
  // 紧张度解读
  let tensionTip = ''
  let tensionAdvice = ''
  if (tension >= 70) {
    tensionTip = '听起来你比较紧张'
    tensionAdvice = '试试深呼吸，放慢语速，停顿是正常的'
  } else if (tension >= 40) {
    tensionTip = '有些许紧张，属正常范围'
    tensionAdvice = '保持当前节奏，多说几句会越来越顺'
  } else {
    tensionTip = '状态放松平稳'
    tensionAdvice = '很好，保持这个状态'
  }

  // 自信度解读
  let confTip = ''
  let confAdvice = ''
  if (confidence >= 60) {
    confTip = '表达果断，语气笃定'
    confAdvice = '继续保持，注意别把话说太满'
  } else if (confidence >= 40) {
    confTip = '自信适中，个别用词有犹豫'
    confAdvice = '少用"可能、大概、好像"，给出明确结论'
  } else {
    confTip = '表达中犹豫较多'
    confAdvice = '先给结论再讲理由，减少模糊词'
  }

  return { tensionTip, tensionAdvice, confTip, confAdvice }
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

export default function EmotionIndicator({ data, live }: { data: EmotionData | null; live?: LiveMetricsData | null }) {
  const tension = data?.tensionScore ?? 0
  const confidence = data?.confidenceScore ?? 0
  const { tensionTip, tensionAdvice, confTip, confAdvice } = interpret(tension, confidence)

  return (
    <div className="emotion-panel">
      <h4>
        我的状态{' '}
        <span style={{ fontSize: 12, fontWeight: 400, color: '#999' }}>
          （用词 + 声音实时判断
          {data?.voiceSignal && <span style={{ color: '#52c41a' }}> · 已接入声学信号</span>}
          {data?.calibrated && <span style={{ color: '#1677ff' }}> · 按个人基线</span>}
          ）
        </span>
      </h4>

      {/* 实时条：说话中滚动刷新（不等句子定稿） */}
      {live && (live.speechRate !== null || live.tensionScore !== null) && (
        <div
          style={{
            display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
            padding: '6px 10px', marginBottom: 10, borderRadius: 6,
            background: '#f0f7ff', fontSize: 13,
          }}
        >
          <span style={{ color: '#1677ff' }}>⚡ 实时</span>
          {live.speechRate !== null && (
            <span>
              语速 <b>{Math.round(live.speechRate)}</b> 字/分
              <span style={{ color: RATE_LABEL[live.speechRateLevel]?.color, marginLeft: 4, fontSize: 12 }}>
                （{RATE_LABEL[live.speechRateLevel]?.text}）
              </span>
            </span>
          )}
          {live.tensionScore !== null && (
            <span>
              紧张度 <b>{Math.round(live.tensionScore)}</b>
              <span style={{ fontSize: 11, color: '#999', marginLeft: 2 }}>/100</span>
            </span>
          )}
          {live.speechSec > 0 && (
            <span style={{ color: '#999', fontSize: 11 }}>{live.speechSec.toFixed(0)}s</span>
          )}
        </div>
      )}

      <div className="metric">
        <div className="metric-label">
          <span>紧张度</span>
          <span className={`metric-level ${tone(tension)}`}>{data?.tensionLevel ?? '待检测'}</span>
        </div>
        <div className="bar">
          <div className={`bar-fill ${tone(tension)}`} style={{ width: `${tension}%` }} />
        </div>
        <div className="metric-value">{tension.toFixed(0)}%</div>
        <div className="metric-tip">💡 {tensionTip} — {tensionAdvice}</div>
        {data?.voiceSignal && (
          <div className="metric-tip" style={{ color: '#888' }}>
            声学依据：颤抖 {(data.pitchJitter ?? 0).toFixed(3)}
            {tension >= 55 && (data.factors?.jitter ?? 0) >= 10 ? '（声音发抖）' : '（平稳）'}
            {data.speechRate ? ` · 语速 ${Math.round(data.speechRate)} 字/分` : ''}
            {data.pauseCount ? ` · 本句停顿 ${data.pauseCount} 次` : ''}
          </div>
        )}
        {data?.factors && Object.keys(data.factors).length > 0 && (
          <div className="metric-tip" style={{ color: '#aaa', fontSize: 12 }}>
            {explainFactors(data.factors, tension)}
          </div>
        )}
        {data && !data.calibrated && data.voiceSignal && (
          <div className="metric-tip" style={{ color: '#faad14', fontSize: 12 }}>
            ⚠ 尚未声音校准，当前按人群默认基线评估；到「设置」做一次朗读校准会更准
          </div>
        )}
      </div>

      <div className="metric">
        <div className="metric-label">
          <span>自信度</span>
          <span className={`metric-level ${tone(100 - confidence)}`}>{data?.confidenceLevel ?? '待检测'}</span>
        </div>
        <div className="bar">
          <div className={`bar-fill ${tone(100 - confidence)}`} style={{ width: `${confidence}%` }} />
        </div>
        <div className="metric-value">{confidence.toFixed(0)}%</div>
        <div className="metric-tip">💡 {confTip} — {confAdvice}</div>
      </div>

      {!data && (
        <div style={{ fontSize: 12, color: '#aaa', marginTop: 8 }}>
          开始回答后，这里会结合你的用词（模糊词、口头禅）与声音特征（颤抖、语速、停顿节奏）实时评估状态。
          紧张度按「偏离你平时说话习惯多少」评判（设置页可做朗读校准）。
        </div>
      )}
    </div>
  )
}

function tone(value: number): string {
  if (value < 40) return 'good'
  if (value < 70) return 'warn'
  return 'bad'
}
