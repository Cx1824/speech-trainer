/**
 * 语音对话模式 Hook（豆包/GPT-voice 式全双工体验）。
 *
 * 链路：
 *   麦克风 getUserMedia → AudioWorklet(16k PCM+RMS) → /ws/voice/{sid} 二进制帧
 *   → 后端 DashScope Paraformer 流式识别 → speech_partial/final 实时字幕
 *   → 每句定稿即收到 analysis_update（口癖/重复/情绪实时反馈）
 *   → 本地 VAD：说话后静音 1.2s 判定"说完" → commit_answer
 *   → 后端自动生成下一题（文字先行）+ TTS 分句流式播放（队列接续）
 *   → 播完继续听 → 无限循环，零按钮操作
 */

import { useCallback, useEffect, useRef, useState } from 'react'

/** 单条实时反馈（问题表述标注） */
export interface FeedbackItem {
  id: string
  kind: 'filler' | 'repeat' | 'hedge' | 'uncertain' | 'long_sentence' | 'silence' | 'no_breath' | 'fast_run'
  word?: string
  count?: number
  sentence: string
  advice?: string           // 改进建议
  ts: number
  live?: boolean            // true=说话中即时反馈（⚡），false=句子定稿分析
}

/** 实时指标（说话中滚动刷新，不等句子定稿） */
export interface LiveMetrics {
  speechRate: number | null      // 字/分
  speechRateLevel: 'fast' | 'normal' | 'slow' | 'unknown'
  tensionScore: number | null    // 实时紧张度（EMA 平滑）
  speechSec: number              // 本句已发音秒数
}

export interface VoiceSessionState {
  status: 'idle' | 'connecting' | 'listening' | 'ai_speaking' | 'thinking' | 'ended' | 'error'
  partialText: string   // 实时字幕（增量）
  finalText: string     // 已定稿的完整回答文本
  aiQuestion: string | null
  error: string | null
  connected: boolean
  feedbacks: FeedbackItem[]        // 实时问题反馈列表
  highlightWords: Map<string, 'filler' | 'repeat' | 'hedge'>  // 当前轮需高亮的词
  fillerTotals: Record<string, number>  // 口头禅累计次数（每词每句只加一次的准确计数）
  issueCount: number                     // 本轮问题总数
  timeUp: boolean                        // 限时场景到点
  liveMetrics: LiveMetrics | null        // 实时指标（语速/紧张度，说话中滚动）
}

const SILENCE_MS = 1200
const RMS_SPEECH_THRESHOLD = 0.012
const RMS_SILENCE_THRESHOLD = 0.006

export function useVoiceSession(
  sid: string | null,
  onAnalysis?: (payload: Record<string, unknown>) => void,
  opts?: { manual?: boolean; autoResume?: boolean; onTimerStarted?: () => void },  // manual=true：不自动提交；autoResume=true：manual 模式下 TTS 播完自动恢复采集（限时场景）
) {
  const [state, setState] = useState<VoiceSessionState>({
    status: 'idle',
    partialText: '',
    finalText: '',
    aiQuestion: null,
    error: null,
    connected: false,
    feedbacks: [],
    highlightWords: new Map(),
    fillerTotals: {},
    issueCount: 0,
    timeUp: false,
    liveMetrics: null,
  })

  const wsRef = useRef<WebSocket | null>(null)
  const ctxRef = useRef<AudioContext | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const nodeRef = useRef<AudioWorkletNode | null>(null)

  // TTS 分句播放队列
  const ttsQueueRef = useRef<{ b64: string; fmt: string }[]>([])
  const ttsPlayingRef = useRef(false)
  const audioCtxPlayRef = useRef<AudioContext | null>(null)

  // VAD 状态
  const hasSpeechRef = useRef(false)
  const lastSpeechAtRef = useRef(0)
  const committingRef = useRef(false)
  const aiSpeakingRef = useRef(false)
  const finalTextRef = useRef('')
  const partialTextRef = useRef('')
  const ttsSpokenRef = useRef('')     // TTS 实际播报的累积文字
  const forceEndedRef = useRef(false) // 收到 interview_completed 强制结束
  const manualStopRef = useRef(false) // 用户主动退出（不触发跳转）
  const manualPausedRef = useRef(false) // 手动模式：采集挂起（未点"开始回答"）
  const onAnalysisRef = useRef(onAnalysis)
  onAnalysisRef.current = onAnalysis
  const onTimerStartedRef = useRef(opts?.onTimerStarted)
  onTimerStartedRef.current = opts?.onTimerStarted
  const manualRef = useRef(Boolean(opts?.manual))
  manualRef.current = Boolean(opts?.manual)
  const autoResumeRef = useRef(Boolean(opts?.autoResume))
  autoResumeRef.current = Boolean(opts?.autoResume)

  const setStatus = useCallback((patch: Partial<VoiceSessionState>) => {
    setState((s) => ({ ...s, ...patch }))
  }, [])

  /** 启动语音会话（sid 可显式传入，避免闭包拿到旧值） */
  const start = useCallback(async (sidToUse?: string) => {
    const sidVal = sidToUse || sid
    if (!sidVal || wsRef.current) return
    manualStopRef.current = false
    setStatus({ status: 'connecting', error: null })

    // 1. WebSocket
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${proto}//${location.host}/ws/voice/${sidVal}`)
    ws.binaryType = 'arraybuffer'
    wsRef.current = ws

    ws.onmessage = (e) => {
      if (typeof e.data !== 'string') return
      try {
        const msg = JSON.parse(e.data)
        handleServerMsg(msg)
      } catch {
        // ignore
      }
    }
    ws.onclose = () => {
      setStatus({ connected: false })
      // 主动退出（退出语音模式）不触发 ended 跳转；其他断开都置 ended 确保跳报告
      if (!manualStopRef.current) setStatus({ status: 'ended' })
    }
    ws.onerror = () => setStatus({ error: '语音通道连接失败' })

    // 2. 麦克风
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    })
    streamRef.current = stream

    const ctx = new AudioContext({ sampleRate: 48000 })
    ctxRef.current = ctx
    await ctx.audioWorklet.addModule('/worklets/recorder.js')
    const node = new AudioWorkletNode(ctx, 'recorder-processor')
    nodeRef.current = node
    const src = ctx.createMediaStreamSource(stream)
    src.connect(node)

    node.port.onmessage = (e: MessageEvent) => {
      const { pcm, rms } = e.data as { pcm: ArrayBuffer; rms: number }
      if (ws.readyState !== WebSocket.OPEN) return
      if (aiSpeakingRef.current) return
      // 手动模式：挂起时不采集（按钮控制）
      if (manualRef.current && manualPausedRef.current) return

      ws.send(pcm)
      const now = performance.now()

      if (!hasSpeechRef.current) {
        if (rms > RMS_SPEECH_THRESHOLD) {
          hasSpeechRef.current = true
          lastSpeechAtRef.current = now
          if (!committingRef.current) setStatus({ status: 'listening' })
        }
      } else {
        if (rms > RMS_SILENCE_THRESHOLD) {
          lastSpeechAtRef.current = now
        }
        // 自动模式才 VAD 自动提交
        if (!manualRef.current && now - lastSpeechAtRef.current > SILENCE_MS && !committingRef.current) {
          commitTurn()
        }
      }
    }

    setStatus({ status: 'listening', connected: true })
  }, [sid])

  /** 提交本轮回答，进入 thinking */
  const commitTurn = useCallback(() => {
    const text = (finalTextRef.current + partialTextRef.current).trim()
    if (!text) {
      hasSpeechRef.current = false
      return
    }
    committingRef.current = true
    setStatus({ status: 'thinking' })
    wsRef.current?.send(JSON.stringify({
      type: 'commit_answer',
      payload: { text },
    }))
  }, [])

  /** 处理服务端消息 */
  const handleServerMsg = useCallback((msg: { type: string; payload: Record<string, unknown> }) => {
    const { type, payload } = msg
    if (type === 'speech_recognized') {
      const text = (payload.text as string) || ''
      if (payload.is_final) {
        finalTextRef.current = finalTextRef.current + text
        partialTextRef.current = ''
        setStatus({ finalText: finalTextRef.current, partialText: '' })
      } else {
        partialTextRef.current = text
        setStatus({ partialText: text })
      }
    } else if (type === 'analysis_update') {
      // 句子级实时分析：更新反馈列表 + 高亮词 + 情绪
      // partial_check 消息不带情绪字段，不更新情绪（避免覆盖完整分析的值）
      if (!payload.partial_check) {
        onAnalysisRef.current?.(payload)
      }
      applyAnalysis(payload)
    } else if (type === 'live_metrics') {
      // 实时指标：说话中滚动刷新（语速/紧张度），不定稿
      setStatus({
        liveMetrics: {
          speechRate: (payload.speech_rate as number | null) ?? null,
          speechRateLevel: (payload.speech_rate_level as LiveMetrics['speechRateLevel']) || 'unknown',
          tensionScore: (payload.tension_score as number | null) ?? null,
          speechSec: (payload.speech_sec as number) || 0,
        },
      })
    } else if (type === 'live_feedback') {
      // 即时反馈：词级（口癖/模糊/重复/超长句）+ 节奏（快说/换气/冷场），⚡ 标记
      const now = Date.now()
      const fb: FeedbackItem = {
        id: `lf-${now}-${Math.random().toString(36).slice(2, 7)}`,
        kind: (payload.kind as FeedbackItem['kind']) || 'filler',
        word: payload.word as string | undefined,
        sentence: payload.advice as string,
        advice: payload.advice as string,
        ts: now,
        live: true,
      }
      setState((s) => ({
        ...s,
        feedbacks: [...s.feedbacks, fb].slice(-50),
        // 高亮词即时更新（口癖/模糊词）
        highlightWords: (() => {
          const hl = new Map(s.highlightWords)
          if (fb.kind === 'filler' || fb.kind === 'hedge') hl.set(fb.word || '', fb.kind)
          if (fb.kind === 'repeat') hl.set(fb.word || '', 'repeat')
          return hl
        })(),
      }))
    } else if (type === 'ai_question') {
      // 问题文字先行显示（TTS 播报内容可能被 Qwen-Audio 微调，
      // 真正朗读的文字以 tts_audio 逐句 text 为准，见 enqueueTTS 的同步替换）
      ttsSpokenRef.current = ''
      setStatus({ aiQuestion: payload.text as string })
    } else if (type === 'tts_audio') {
      // 分句 TTS：用实际合成的句子同步替换显示文字（语音文字一致）
      const sentText = (payload.text as string) || ''
      if (sentText) {
        ttsSpokenRef.current += sentText
        setStatus({ aiQuestion: ttsSpokenRef.current })
      }
      enqueueTTS(payload.audio as string, payload.format as string)
    } else if (type === 'interview_completed') {
      forceEndedRef.current = true
      setStatus({ status: 'ended' })
    } else if (type === 'timer_started') {
      // 计时零点确认：开场白播完，从此刻起计时（页面据此设 startedAt）
      onTimerStartedRef.current?.()
    } else if (type === 'time_up') {
      // 限时场景到点：提示用户（由页面决定是否 finishStage）
      setStatus({ timeUp: true })
    } else if (type === 'error') {
      setStatus({ error: payload.message as string, status: 'error' })
    }
  }, [])

  /** 句子级分析 → 反馈列表 + 高亮词 + 口头禅累计
   * 计数原则：partial（增量）提示不计数；final（定稿）才计数累加，
   * 且以该句内的真实出现次数为准（后端已按句分析，无跨 partial 重复）。
   */
  const applyAnalysis = useCallback((p: Record<string, unknown>) => {
    const sentence = (p.sentence as string) || ''
    if (!sentence) return
    const isPartial = Boolean(p.partial_check)

    const fillerHits = (p.filler_hits as Array<{ word: string; count: number }>) || []
    const hedgeHits = (p.hedge_hits as Array<{ word: string; count: number }>) || []
    const uncertainHits = (p.uncertain_hits as Array<{ word: string; count: number }>) || []
    const repeated = (p.repeated_words as Array<{ word: string; count: number }>) || []
    const longSentences = (p.long_sentences as string[]) || []

    const newFeedbacks: FeedbackItem[] = []
    const now = Date.now()
    const seq = Math.random().toString(36).slice(2, 7)

    // partial 提示：只高亮，不进反馈列表（避免与 final 重复刷屏）
    if (isPartial) {
      setState((s) => {
        const hl = new Map(s.highlightWords)
        for (const h of fillerHits) {
          const kind = hedgeHits.some((x) => x.word === h.word) ? 'hedge' : 'filler'
          hl.set(h.word, kind as 'filler' | 'hedge')
        }
        return { ...s, highlightWords: hl }
      })
      return
    }

    // final：完整反馈
    for (const h of fillerHits) {
      const isHedge = hedgeHits.some((x) => x.word === h.word)
      newFeedbacks.push({
        id: `fb-${now}-${seq}-f-${h.word}`,
        kind: isHedge ? 'hedge' : 'filler',
        word: h.word, count: h.count, sentence, ts: now,
        advice: isHedge ? '表述不够确定，面试中尽量给出明确判断' : '连接词口头禅，停顿一下再组织语言',
      })
    }
    for (const h of hedgeHits) {
      if (fillerHits.some((x) => x.word === h.word)) continue // 已在 filler 循环里按 hedge 展示
      newFeedbacks.push({
        id: `fb-${now}-${seq}-h-${h.word}`,
        kind: 'hedge', word: h.word, count: h.count, sentence, ts: now,
        advice: '模糊表述削弱说服力，给出具体数据和结论更可信',
      })
    }
    for (const u of uncertainHits) {
      newFeedbacks.push({
        id: `fb-${now}-${seq}-u-${u.word}`,
        kind: 'uncertain', word: u.word, count: u.count, sentence, ts: now,
        advice: '不自信表述，先想清楚再开口，或直接说"我补充一点"',
      })
    }
    for (const r of repeated) {
      newFeedbacks.push({
        id: `fb-${now}-${seq}-r-${r.word}`,
        kind: 'repeat', word: r.word, count: r.count, sentence, ts: now,
        advice: '同一用词反复出现，尝试换同义词或直接说重点',
      })
    }
    for (const ls of longSentences) {
      newFeedbacks.push({
        id: `fb-${now}-${seq}-ls`,
        kind: 'long_sentence', sentence: ls, ts: now,
        advice: '句子超过 60 字，面试官难以抓住重点，说完一句停顿一下',
      })
    }

    setState((s) => {
      // 更新高亮词集合
      const hl = new Map(s.highlightWords)
      for (const h of fillerHits) hl.set(h.word, 'filler')
      for (const h of hedgeHits) hl.set(h.word, 'hedge')
      for (const r of repeated) hl.set(r.word, 'repeat')
      // 口头禅累计：final 的 count 是该句内真实次数（partial 不计数，不再虚高）
      const totals = { ...s.fillerTotals }
      for (const h of fillerHits) totals[h.word] = (totals[h.word] || 0) + h.count
      for (const h of hedgeHits) totals[h.word] = (totals[h.word] || 0) + h.count
      return {
        ...s,
        feedbacks: [...s.feedbacks, ...newFeedbacks].slice(-50),
        highlightWords: hl,
        fillerTotals: totals,
        issueCount: s.issueCount + newFeedbacks.length,
      }
    })
  }, [])

  /** TTS 分句队列：顺序播放，接续无缝 */
  const enqueueTTS = useCallback((b64: string, fmt: string) => {
    aiSpeakingRef.current = true
    setStatus({ status: 'ai_speaking' })
    ttsQueueRef.current.push({ b64, fmt })
    if (!ttsPlayingRef.current) playNextTTS()
  }, [])

  const playNextTTS = useCallback(() => {
    const item = ttsQueueRef.current.shift()
    if (!item) {
      // 队列播完：恢复监听；限时场景通知后端计时零点（幂等，仅首次生效）
      ttsPlayingRef.current = false
      aiSpeakingRef.current = false
      hasSpeechRef.current = false
      finalTextRef.current = ''
      partialTextRef.current = ''
      wsRef.current?.send(JSON.stringify({ type: 'begin_timer' }))
      if (manualRef.current) {
        // 手动模式：挂起采集，等用户点"开始回答"（限时场景 autoResume 除外）
        if (autoResumeRef.current) {
          committingRef.current = false
          setStatus({ status: 'listening', partialText: '', finalText: '' })
        } else {
          manualPausedRef.current = true
          setStatus({ status: 'thinking', partialText: '', finalText: '' })
        }
      } else {
        committingRef.current = false
        setStatus({ status: 'listening', partialText: '', finalText: '' })
      }
      return
    }
    ttsPlayingRef.current = true
    const url = `data:audio/${item.fmt || 'wav'};base64,${item.b64}`
    const el = new Audio(url)
    el.onended = () => playNextTTS()
    el.onerror = () => playNextTTS()
    el.play().catch(() => playNextTTS())
  }, [])

  /** 主动请求第一题：先置 thinking（AI 准备中），收到 ai_question + TTS 播报 */
  const requestFirstQuestion = useCallback(() => {
    setStatus({ status: 'thinking' })
    wsRef.current?.send(JSON.stringify({ type: 'start_stage' }))
  }, [])

  /** 手动模式：开始回答（恢复采集，从 thinking/ai_speaking 回到 listening） */
  const beginAnswer = useCallback(() => {
    manualPausedRef.current = false
    committingRef.current = false
    hasSpeechRef.current = false
    setStatus({ status: 'listening' })
  }, [])

  /** 手动模式：提交回答（按钮触发，无 VAD） */
  const commitAnswer = useCallback(() => {
    commitTurn()
  }, [commitTurn])

  /** 限时场景：讲完本阶段（落库已说内容，推进阶段） */
  const finishStage = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ type: 'finish_stage' }))
    // 阶段切换后清空字幕，反馈保留（跨阶段累计可见）
    finalTextRef.current = ''
    partialTextRef.current = ''
    hasSpeechRef.current = false
    committingRef.current = false
    setStatus({ finalText: '', partialText: '', timeUp: false })
  }, [])

  /** 结束面试 */
  const endInterview = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ type: 'end_interview' }))
  }, [])

  /** 完全停止（离开页面） */
  const stop = useCallback(() => {
    nodeRef.current?.disconnect()
    nodeRef.current = null
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    ctxRef.current?.close()
    ctxRef.current = null
    audioCtxPlayRef.current?.close()
    audioCtxPlayRef.current = null
    ttsQueueRef.current = []
    ttsPlayingRef.current = false
    aiSpeakingRef.current = false
    manualStopRef.current = true   // 标记主动停止，避免 onclose 误触发跳转
    wsRef.current?.close()
    wsRef.current = null
    setStatus({ status: 'idle', connected: false })
  }, [])

  /** 清空本轮反馈（进入新题目时） */
  const resetFeedbacks = useCallback(() => {
    setState((s) => ({ ...s, feedbacks: [], highlightWords: new Map(), finalText: '', partialText: '', fillerTotals: {}, issueCount: 0 }))
  }, [])

  useEffect(() => stop, [])

  return { state, start, stop, requestFirstQuestion, endInterview, resetFeedbacks, beginAnswer, commitAnswer, finishStage }
}
