/**
 * 浏览器语音识别 Hook（Web Speech API）。
 *
 * 优势：Chrome/Edge 中文识别质量良好，无需后端 ASR 配置，零延迟本地处理。
 * 限制：仅 Chrome/Edge/Safari 较新版本支持；Firefox 不支持。
 *
 * 兜底：如果不支持，组件应提示用户改用文字输入。
 */

import { useCallback, useEffect, useRef, useState } from 'react'

// TypeScript 没有 SpeechRecognition 类型，简化的最小声明
interface SpeechRecognitionResultLike {
  0: { transcript: string }
  isFinal: boolean
}
interface SpeechRecognitionEventLike {
  resultIndex: number
  results: { length: number; [i: number]: SpeechRecognitionResultLike }
}
interface SpeechRecognitionLike {
  lang: string
  continuous: boolean
  interimResults: boolean
  start(): void
  stop(): void
  abort(): void
  onresult: ((e: SpeechRecognitionEventLike) => void) | null
  onerror: ((e: { error: string }) => void) | null
  onend: (() => void) | null
}

function getRecognitionCtor(): (new () => SpeechRecognitionLike) | null {
  if (typeof window === 'undefined') return null
  const w = window as any
  return w.SpeechRecognition || w.webkitSpeechRecognition || null
}

export interface SpeechRecognitionState {
  supported: boolean
  listening: boolean
  interimText: string
  finalText: string
  error: string | null
}

export function useSpeechRecognition(lang = 'zh-CN') {
  const [state, setState] = useState<SpeechRecognitionState>({
    supported: !!getRecognitionCtor(),
    listening: false,
    interimText: '',
    finalText: '',
    error: null,
  })
  const recRef = useRef<SpeechRecognitionLike | null>(null)
  const finalRef = useRef<string>('')

  useEffect(() => {
    const Ctor = getRecognitionCtor()
    if (!Ctor) return
    const rec = new Ctor()
    rec.lang = lang
    rec.continuous = true
    rec.interimResults = true

    rec.onresult = (e) => {
      let interim = ''
      let appended = ''
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const seg = e.results[i][0].transcript
        if (e.results[i].isFinal) {
          appended += seg
        } else {
          interim += seg
        }
      }
      if (appended) {
        finalRef.current += appended
        setState((s) => ({ ...s, finalText: finalRef.current, interimText: '' }))
      } else {
        setState((s) => ({ ...s, interimText: interim }))
      }
    }
    rec.onerror = (e) => {
      const kind = e.error || '识别错误'
      // not-allowed: 用户拒绝了麦克风权限；no-speech: 超时没检测到语音（正常现象）
      // network: Chrome Web Speech 需要联网（识别在云端做）
      const friendly =
        kind === 'not-allowed' || kind === 'service-not-allowed'
          ? '麦克风权限被拒绝，请在浏览器地址栏 🔒 图标里允许麦克风访问后刷新页面'
          : kind === 'network'
            ? '语音识别服务连接失败（Web Speech 需要联网），请检查网络或科学上网'
            : kind === 'no-speech'
              ? '没有检测到语音，请靠近麦克风重试'
              : `语音识别错误: ${kind}`
      setState((s) => ({ ...s, error: friendly, listening: false }))
    }
    rec.onend = () => {
      setState((s) => ({ ...s, listening: false }))
    }
    recRef.current = rec
    return () => {
      try {
        rec.abort()
      } catch {
        // ignore
      }
    }
  }, [lang])

  const start = useCallback(() => {
    if (!recRef.current) return
    finalRef.current = ''
    setState((s) => ({ ...s, finalText: '', interimText: '', error: null, listening: true }))
    try {
      recRef.current.start()
    } catch {
      // already started
    }
  }, [])

  const stop = useCallback(() => {
    if (!recRef.current) return
    try {
      recRef.current.stop()
    } catch {
      // ignore
    }
    setState((s) => ({ ...s, listening: false }))
  }, [])

  const reset = useCallback(() => {
    finalRef.current = ''
    setState((s) => ({ ...s, finalText: '', interimText: '' }))
  }, [])

  return { state, start, stop, reset }
}
