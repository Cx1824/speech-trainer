export interface InterviewSessionOut {
  id: string
  scenario: string
  position: string
  level: string
  style: string
  interview_mode: string
  interview_intensity: string
  interview_progress: InterviewProgress | null
  source_session_id: string
  company: string
  jd_url: string
  jd_content: string
  status: string
  current_stage: string
  has_resume: boolean
  resume_parsed: ResumeStructured | null
  material_file: string
  has_material: boolean
  duration_limit: number
  started_at: string | null
}

export interface InterviewProgress {
  mode: string
  mode_label: string
  intensity: string
  intensity_label: string
  estimated_minutes: { min: number; max: number }
  current_label: string
  current_goal?: string
  covered: number
  total: number
  covered_labels: string[]
  remaining_labels: string[]
  skipped_labels: string[]
  followups_used: number
  followup_budget: number
}

export interface InterviewModeOut {
  key: string
  name: string
  description: string
  recommended: boolean
  estimates: Record<string, { min: number; max: number }>
  question_counts: Record<string, number>
}

export interface InterviewIntensityOut {
  key: string
  name: string
  description: string
  followup_budget: number
}

/** 训练场景（首页卡片）。 */
export interface ScenarioOut {
  key: string
  name: string
  role_name: string
  description: string
  needs_resume: boolean
  needs_material: boolean
  timed: boolean
}

export interface FetchJDOut {
  title: string
  company: string
  content: string
  url: string
  success: boolean
  message: string
}

export interface InterviewProfile {
  id: string
  name: string
  position: string
  level: string
  style: string
  company: string
  jd_url: string
  jd_content: string
  has_resume: boolean
  resume_file: string
  resume_parsed: ResumeStructured | null
}

export interface ResumeStructured {
  basics: Record<string, unknown>
  education: Record<string, unknown>[]
  work: Record<string, unknown>[]
  projects: Record<string, unknown>[]
  skills: string[]
  position_guess?: string
  level_guess?: string
}

export interface DialogueOut {
  id: string
  seq: number
  role: 'ai' | 'user'
  stage: string
  text: string
  audio_url: string
}

export interface InterviewStyle {
  name: string
  label: string
  description: string
}

export const STAGE_LABELS: Record<string, string> = {
  opening: '开场',
  self_intro: '自我介绍',
  project: '项目追问',
  position: '岗位能力题',
  hr_screen: 'HR 基础筛选',
  professional: '专业 / 业务能力',
  behavioral: '行为 / 管理能力',
  weakness: '薄弱项重练',
  qa: '反问环节',
  presenting: '汇报/演讲中',
  ending: '收尾',
  report: '训练结束',
}

/** 场景元信息（与后端场景包对齐，用于前端文案适配）。 */
export const SCENARIOS: Record<string, { name: string; role: string; stageLabels: Record<string, string> }> = {
  interview: {
    name: '模拟面试',
    role: '面试官',
    stageLabels: {},
  },
  presentation: {
    name: '工作汇报',
    role: '评审',
    stageLabels: { qa: '评审质询' },
  },
  speech: {
    name: '演讲训练',
    role: '主持人',
    stageLabels: {},
  },
}
