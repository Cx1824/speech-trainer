export interface InterviewSessionOut {
  id: string
  position: string
  level: string
  style: string
  company: string
  jd_url: string
  jd_content: string
  status: string
  current_stage: string
  has_resume: boolean
  resume_parsed: ResumeStructured | null
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
  qa: '反问环节',
  report: '面试结束',
}
