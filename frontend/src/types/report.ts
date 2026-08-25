export interface ReportData {
  session_id: string
  scenario: string
  scenario_name: string
  position: string
  level: string
  overall_score: number | null
  /**
   * 场景关键任务未达标时的评分约束；可选以兼容旧报告与固定示例。
   * 后台阈值字段只用于计算和审计，普通报告页面只展示 reason。
   */
  score_constraints?: {
    axis_key: string
    axis_score: number
    below: number
    max_overall: number
    reason: string
  }[]
  score_coverage: number
  interview_coverage?: InterviewCoverage | null
  sample_state: 'insufficient' | 'text_only' | 'voice_uncalibrated' | 'voice_calibrated'
  summary: string
  axes: {
    key: string
    label: string
    description: string
    score: number | null
    weight: number
    source: 'signal' | 'llm'
    feedback?: string
    evidence?: string[]
  }[]
  expression_metrics: {
    speech_rate: number
    speech_rate_level: string
    speech_duration_sec: number | null
    duration_source: 'voice' | 'unavailable'
    total_words: number
    filler_total: number
    filler_top: { word: string; count: number }[]
    repetition_rate: number
    expression_break_count?: number
    expression_break_examples?: {
      excerpt: string
      description: string
    }[]
    short_pause_count: number | null
    short_pause_rate: number | null
    long_pause_count: number | null
    long_pause_rate?: number | null
  }
  delivery_metrics: {
    stability_score: number | null
    calibrated: boolean
    voice_signal: boolean
    pitch_jitter: number | null
    note: string
  }
  /** 非评分的声音与节奏参考；旧报告可能没有该字段。 */
  voice_reference?: {
    version: string
    available: boolean
    summary: string
    confidence: '较低' | '中等'
    confidence_note: string
    dimensions: {
      key: 'variation' | 'fluency' | 'pacing' | 'tension'
      label: string
      value: string
      detail: string
    }[]
    basis: string[]
    is_scored: false
  }
  suggestions: {
    short_term: string[]
    mid_term: string[]
  }
  professional_advice: {
    topic: string
    detail: string
  }[]
  dialogues: { seq: number; role: string; stage: string; text: string }[]
}

export interface InterviewCoverage {
  mode: string
  mode_label: string
  intensity: string
  intensity_label: string
  estimated_minutes: { min: number; max: number }
  current_label: string
  covered: number
  total: number
  covered_labels: string[]
  remaining_labels: string[]
  skipped_labels: string[]
  followups_used: number
  followup_budget: number
}
