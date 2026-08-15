import './EmotionIndicator.css'

export interface EmotionData {
  tensionScore: number
  tensionLevel: string
  confidenceScore: number
  confidenceLevel: string
  /** 声学信号（真实声音特征；false=仅文本用词推断） */
  voiceSignal?: boolean
  pitchJitter?: number   // 基频抖动（越大声音越"发抖"）
  pauseCount?: number    // 停顿次数（本句）
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

export default function EmotionIndicator({ data }: { data: EmotionData | null }) {
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
          ）
        </span>
      </h4>

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
            声学依据：基频抖动 {(data.pitchJitter ?? 0).toFixed(3)}
            {tension >= 55 && (data.pitchJitter ?? 0) > 0.05 ? '（声音发抖）' : '（平稳）'}
            {data.pauseCount ? ` · 本句停顿 ${data.pauseCount} 次` : ''}
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
          开始回答后，这里会结合你的用词（模糊词、口头禅）与声音特征（音调抖动、停顿节奏）实时评估状态。
          紧张度高 = 声音发抖或用词犹豫；自信度低 = 表达不够果断。
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
