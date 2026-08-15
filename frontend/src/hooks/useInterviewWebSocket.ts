import { useCallback, useEffect, useRef, useState } from 'react'

export type ServerMsgType =
  | 'ai_question'
  | 'tts_audio'
  | 'stage_changed'
  | 'interview_completed'
  | 'speech_recognized'
  | 'analysis_update'
  | 'emotion_update'
  | 'error'

export interface ServerMessage {
  type: ServerMsgType
  payload: Record<string, unknown>
}

export interface InterviewWSState {
  connected: boolean
  currentStage: string
  aiQuestion: string | null
  lastAudioUrl: string | null
  completed: boolean
  error: string | null
  lastAnalysis: Record<string, unknown> | null
}

export function useInterviewWebSocket(sid: string | null) {
  const [state, setState] = useState<InterviewWSState>({
    connected: false,
    currentStage: '',
    aiQuestion: null,
    lastAudioUrl: null,
    completed: false,
    error: null,
    lastAnalysis: null,
  })
  const wsRef = useRef<WebSocket | null>(null)
  const listenersRef = useRef<Array<(msg: ServerMessage) => void>>([])

  useEffect(() => {
    if (!sid) return
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${proto}//${location.host}/ws/interview/${sid}`
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => setState((s) => ({ ...s, connected: true }))
    ws.onclose = () => setState((s) => ({ ...s, connected: false }))
    ws.onerror = () => setState((s) => ({ ...s, error: 'WebSocket 连接错误' }))
    ws.onmessage = (e) => {
      try {
        const msg: ServerMessage = JSON.parse(e.data)
        // 分发到监听器
        listenersRef.current.forEach((fn) => fn(msg))

        if (msg.type === 'ai_question') {
          setState((s) => ({
            ...s,
            currentStage: (msg.payload.stage as string) || s.currentStage,
            aiQuestion: msg.payload.text as string,
          }))
        } else if (msg.type === 'tts_audio') {
          const b64 = msg.payload.audio as string
          const fmt = (msg.payload.format as string) || 'mp3'
          const url = `data:audio/${fmt};base64,${b64}`
          setState((s) => ({ ...s, lastAudioUrl: url }))
        } else if (msg.type === 'stage_changed') {
          setState((s) => ({ ...s, currentStage: msg.payload.stage as string }))
        } else if (msg.type === 'interview_completed') {
          setState((s) => ({ ...s, completed: true }))
        } else if (msg.type === 'analysis_update') {
          setState((s) => ({ ...s, lastAnalysis: msg.payload }))
        } else if (msg.type === 'error') {
          setState((s) => ({ ...s, error: msg.payload.message as string }))
        }
      } catch {
        // ignore
      }
    }

    return () => ws.close()
  }, [sid])

  const send = useCallback((type: string, payload: Record<string, unknown> = {}) => {
    wsRef.current?.send(JSON.stringify({ type, payload }))
  }, [])

  const subscribe = useCallback((fn: (msg: ServerMessage) => void) => {
    listenersRef.current.push(fn)
    return () => {
      listenersRef.current = listenersRef.current.filter((f) => f !== fn)
    }
  }, [])

  return { state, send, subscribe }
}
