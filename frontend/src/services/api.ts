import type { ApiConfigOut, ApiConfigIn } from '@/types/api'
import type {
  InterviewSessionOut,
  DialogueOut,
  InterviewStyle,
  FetchJDOut,
  InterviewProfile,
  InterviewModeOut,
  InterviewIntensityOut,
  ScenarioOut,
} from '@/types/interview'
import type { ReportData } from '@/types/report'

const BASE = '/api/v1'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try {
      const body = await res.json()
      msg = body.message ?? msg
    } catch {
      // ignore
    }
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

// React 开发模式会重复执行 effect；同一报告生成中的请求应复用同一 Promise。
const inFlightReportRequests = new Map<string, Promise<ReportData>>()

function generateReportOnce(sid: string): Promise<ReportData> {
  const existing = inFlightReportRequests.get(sid)
  if (existing) return existing

  const pending = request<ReportData>(`/reports/${sid}`, { method: 'POST' })
  inFlightReportRequests.set(sid, pending)
  void pending.finally(() => {
    if (inFlightReportRequests.get(sid) === pending) {
      inFlightReportRequests.delete(sid)
    }
  }).catch(() => undefined)
  return pending
}

export const apiService = {
  getConfig: () => request<ApiConfigOut>('/config'),
  updateConfig: (data: ApiConfigIn) =>
    request<ApiConfigOut>('/config', { method: 'PUT', body: JSON.stringify(data) }),
  testProvider: (kind: 'llm' | 'asr' | 'tts') =>
    request<{ ok: boolean; message: string }>(`/config/test/${kind}`, { method: 'POST' }),
  getVoiceCalibration: () =>
    request<{
      text: string
      char_count: number
      estimated_sec: number
      calibrated: boolean
      baseline: Record<string, number | string> | null
    }>('/config/voice-calibration'),
  resetVoiceCalibration: () =>
    request<{ ok: boolean; message: string }>('/config/voice-calibration', { method: 'DELETE' }),

  createInterview: (data: {
    scenario?: string
    position?: string
    level?: string
    style?: string
    interview_mode?: string
    interview_intensity?: string
    source_session_id?: string
    company?: string
    jd_url?: string
    jd_content?: string
    duration_limit?: number
  }) =>
    request<InterviewSessionOut>('/interviews', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  listScenarios: () => request<{ scenarios: ScenarioOut[] }>('/interviews/scenarios'),
  updateInterview: (sid: string, data: Record<string, unknown>) =>
    request<InterviewSessionOut>(`/interviews/${sid}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  getInterview: (sid: string) => request<InterviewSessionOut>(`/interviews/${sid}`),
  listInterviews: () => request<InterviewSessionOut[]>('/interviews'),
  listStyles: () => request<{ styles: InterviewStyle[] }>('/interviews/styles'),
  listInterviewModes: () => request<{ modes: InterviewModeOut[]; intensities: InterviewIntensityOut[] }>('/interviews/modes'),
  fetchJD: (url: string) =>
    request<FetchJDOut>('/interviews/jd/fetch', {
      method: 'POST',
      body: JSON.stringify({ url }),
    }),
  uploadResume: (sid: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return fetch(`${BASE}/interviews/${sid}/resume`, { method: 'POST', body: fd }).then(async (r) => {
      if (!r.ok) {
        let msg = `HTTP ${r.status}`
        try {
          const body = await r.json()
          msg = body.message ?? msg
        } catch {
          // ignore
        }
        throw new Error(msg)
      }
      return r.json() as Promise<InterviewSessionOut>
    })
  },
  uploadMaterial: (sid: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return fetch(`${BASE}/interviews/${sid}/material`, { method: 'POST', body: fd }).then(async (r) => {
      if (!r.ok) {
        let msg = `HTTP ${r.status}`
        try {
          const body = await r.json()
          msg = body.message ?? msg
        } catch {
          // ignore
        }
        throw new Error(msg)
      }
      return r.json() as Promise<InterviewSessionOut>
    })
  },
  startInterview: (sid: string) =>
    request<InterviewSessionOut>(`/interviews/${sid}/start`, { method: 'POST' }),

  endInterview: (sid: string) =>
    request<InterviewSessionOut>(`/interviews/${sid}/end`, { method: 'POST' }),
  getDialogues: (sid: string) => request<DialogueOut[]>(`/interviews/${sid}/dialogues`),
  getReport: (sid: string) => request<ReportData>(`/reports/${sid}`),
  generateReport: generateReportOnce,
  regenerateReport: (sid: string) =>
    request<ReportData>(`/reports/${sid}/regenerate`, { method: 'POST' }),

  // 面试档案
  listProfiles: () => request<InterviewProfile[]>('/profiles'),
  createProfile: (data: Record<string, unknown>) =>
    request<InterviewProfile>('/profiles', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  deleteProfile: (pid: string) =>
    request<{ ok: boolean }>(`/profiles/${pid}`, { method: 'DELETE' }),
}
