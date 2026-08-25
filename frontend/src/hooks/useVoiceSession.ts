/**
 * 语音对话模式 Hook（豆包/GPT-voice 式全双工体验）。
 *
 * 链路：
 *   麦克风 getUserMedia → AudioWorklet(16k PCM+RMS) → /ws/voice/{sid} 二进制帧
 *   → 当前所选 ASR 流式识别 → speech_partial/final 实时字幕
 *   → 每句定稿即收到 analysis_update（口癖/重复/声音事实）
 *   → 本地 VAD：说话后静音 1.2s 判定"说完" → commit_answer
 *   → 后端自动生成下一题（文字先行）+ TTS 分句流式播放（队列接续）
 *   → 播完继续听 → 无限循环，零按钮操作
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { InterviewProgress } from '@/types/interview'

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
  speechSec: number              // 本句已发音秒数
}

export interface TranscriptAnnotation {
  start: number
  end: number
  kind: 'filler' | 'repeat' | 'hedge'
  word: string
}

export interface TranscriptSegment {
  id: string
  text: string
  ts: number
  annotations: TranscriptAnnotation[]
  analyzed: boolean
}

/** 两句在语义上重复、但不一定复用了相同词语的句对。 */
export interface SemanticRepeatPair {
  first: string
  second: string
  similarity?: number
}

export interface VoiceSessionState {
  status: 'idle' | 'connecting' | 'listening' | 'ai_speaking' | 'thinking' | 'ended' | 'error'
  partialText: string   // 实时字幕（增量）
  finalText: string     // 已定稿的完整回答文本
  segments: TranscriptSegment[]  // 已定稿字幕；逐句保存，分析结果直接绑定到句子
  aiQuestion: string | null
  aiQuestionDelivery: 'voice' | 'text' | null
  error: string | null
  connected: boolean
  feedbacks: FeedbackItem[]        // 实时问题反馈列表
  highlightWords: Map<string, 'filler' | 'repeat' | 'hedge'>  // 仅用于当前 partial 的兼容高亮
  fillerTotals: Record<string, number>  // 口头禅累计次数（每词每句只加一次的准确计数）
  stutterTotals: Record<string, number> // 紧邻连续重复的累计次数（只在定稿时计数）
  semanticRepeats: SemanticRepeatPair[]  // 本轮已识别的重复意思句对
  semanticRepeatTotal: number            // 本次训练累计命中次数，不随换题清零
  issueCount: number                     // 本轮问题总数
  timeUp: boolean                        // 限时场景到点
  hardTimeUp: boolean                    // 到点后宽限 10 分钟仍未结束
  liveMetrics: LiveMetrics | null        // 实时指标（语速/发声时长，说话中滚动）
  interviewProgress: InterviewProgress | null
}

interface VoiceSessionOptions {
  manual?: boolean
  autoResume?: boolean
  onTimerStarted?: () => void
  onAnswerStarted?: () => void
  /** 本地录音复盘：用同源音频替代麦克风，仍走完整实时分析链路。 */
  replayAudioUrl?: string
}

const AUTO_COMMIT_SILENCE_MS = 2600
const SHORT_ANSWER_SILENCE_MS = 4500
const SHORT_ANSWER_CHAR_LIMIT = 18
const MIN_AUTO_TURN_MS = 3000
const RMS_SPEECH_THRESHOLD = 0.012
const RMS_SILENCE_THRESHOLD = 0.006

export function useVoiceSession(
  sid: string | null,
  onAnalysis?: (payload: Record<string, unknown>) => void,
  opts?: VoiceSessionOptions,  // manual=true：不自动提交；autoResume=true：manual 模式下 TTS 播完自动恢复采集（限时场景）
) {
  const [state, setState] = useState<VoiceSessionState>({
    status: 'idle',
    partialText: '',
    finalText: '',
    segments: [],
    aiQuestion: null,
    aiQuestionDelivery: null,
    error: null,
    connected: false,
    feedbacks: [],
    highlightWords: new Map(),
    fillerTotals: {},
    stutterTotals: {},
    semanticRepeats: [],
    semanticRepeatTotal: 0,
    issueCount: 0,
    timeUp: false,
    hardTimeUp: false,
    liveMetrics: null,
    interviewProgress: null,
  })

  const wsRef = useRef<WebSocket | null>(null)
  const ctxRef = useRef<AudioContext | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const nodeRef = useRef<AudioWorkletNode | null>(null)
  const replayAudioRef = useRef<HTMLAudioElement | null>(null)
  const replaySourceRef = useRef<MediaElementAudioSourceNode | null>(null)
  const pendingFlushMessagesRef = useRef<string[]>([])

  // TTS 分句播放队列
  const ttsQueueRef = useRef<{ b64: string; fmt: string }[]>([])
  const ttsPlayingRef = useRef(false)
  const audioCtxPlayRef = useRef<AudioContext | null>(null)
  const aiTextDwellTimerRef = useRef<number | null>(null)

  // VAD 状态
  const hasSpeechRef = useRef(false)
  const lastSpeechAtRef = useRef(0)
  const answerStartedAtRef = useRef(0)
  const committingRef = useRef(false)
  const aiSpeakingRef = useRef(false)
  const finalTextRef = useRef('')
  const partialTextRef = useRef('')
  const segmentsRef = useRef<TranscriptSegment[]>([])
  const segmentSequenceRef = useRef(0)
  const lastFinalTextRef = useRef<string | null>(null)
  const partialSinceFinalRef = useRef(false)
  const ttsSpokenRef = useRef('')     // TTS 实际播报的累积文字
  const forceEndedRef = useRef(false) // 收到 interview_completed 强制结束
  const manualStopRef = useRef(false) // 用户主动退出（不触发跳转）
  const manualPausedRef = useRef(false) // 手动模式：采集挂起（未点"开始回答"）
  const onAnalysisRef = useRef(onAnalysis)
  onAnalysisRef.current = onAnalysis
  const onTimerStartedRef = useRef(opts?.onTimerStarted)
  onTimerStartedRef.current = opts?.onTimerStarted
  const onAnswerStartedRef = useRef(opts?.onAnswerStarted)
  onAnswerStartedRef.current = opts?.onAnswerStarted
  const manualRef = useRef(Boolean(opts?.manual))
  manualRef.current = Boolean(opts?.manual)
  const autoResumeRef = useRef(Boolean(opts?.autoResume))
  autoResumeRef.current = Boolean(opts?.autoResume)
  type ServerMessage = { type: string; payload: Record<string, unknown> }
  const commitTurnRef = useRef<() => void>(() => {})
  const handleServerMsgRef = useRef<(msg: ServerMessage) => void>(() => {})
  const applyAnalysisRef = useRef<(payload: Record<string, unknown>) => void>(() => {})
  const enqueueTTSRef = useRef<(b64: string, fmt: string) => void>(() => {})
  const playNextTTSRef = useRef<() => void>(() => {})

  const setStatus = useCallback((patch: Partial<VoiceSessionState>) => {
    setState((s) => ({ ...s, ...patch }))
  }, [])

  const sendAfterCaptureFlush = useCallback((message: string) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    const node = nodeRef.current
    if (!node) {
      ws.send(message)
      return
    }
    pendingFlushMessagesRef.current.push(message)
    node.port.postMessage({ type: 'flush' })
  }, [])

  /** 启动语音会话（sid 可显式传入，避免闭包拿到旧值） */
  const start = useCallback(async (sidToUse?: string) => {
    const sidVal = sidToUse || sid
    if (!sidVal || wsRef.current) return
    manualStopRef.current = false
    setStatus({ status: 'connecting', error: null, timeUp: false, hardTimeUp: false })

    // 1. WebSocket
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${proto}//${location.host}/ws/voice/${sidVal}`)
    ws.binaryType = 'arraybuffer'
    wsRef.current = ws

    let wsOpened = false
    const waitForOpen = new Promise<void>((resolve, reject) => {
      ws.onopen = () => {
        wsOpened = true
        resolve()
      }
      ws.onerror = () => {
        const error = new Error('语音通道连接失败，请稍后重试')
        setStatus({ error: error.message, status: 'error' })
        if (!wsOpened) reject(error)
      }
      ws.onclose = () => {
        setStatus({ connected: false })
        if (!wsOpened) reject(new Error('语音通道已关闭'))
        // 主动退出（退出语音模式）不触发 ended 跳转；其他断开都置 ended 确保跳报告
        if (!manualStopRef.current) setStatus({ status: 'ended' })
      }
    })

    ws.onmessage = (e) => {
      if (typeof e.data !== 'string') return
      try {
        const msg = JSON.parse(e.data)
        handleServerMsgRef.current(msg)
      } catch {
        // ignore
      }
    }
    await waitForOpen

    // 2. 音频输入：正常训练读取麦克风；本地复盘读取同源录音。
    const ctx = new AudioContext({ sampleRate: 48000 })
    ctxRef.current = ctx
    await ctx.audioWorklet.addModule('/worklets/recorder.js')
    const node = new AudioWorkletNode(ctx, 'recorder-processor')
    nodeRef.current = node
    if (opts?.replayAudioUrl) {
      const replayAudio = new Audio(opts.replayAudioUrl)
      replayAudio.preload = 'auto'
      replayAudioRef.current = replayAudio
      const source = ctx.createMediaElementSource(replayAudio)
      replaySourceRef.current = source
      source.connect(node)
      // 复盘时同步播放原声，便于观察者把字幕、弹幕与听感对应起来。
      source.connect(ctx.destination)
      replayAudio.onended = () => {
        if (ws.readyState !== WebSocket.OPEN) return
        setStatus({ status: 'thinking' })
        node.port.postMessage({ type: 'flush' })
      }
      replayAudio.onerror = () => {
        setStatus({ status: 'error', error: '录音回放失败，请确认文件仍然可用' })
      }
    } else {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      })
      streamRef.current = stream
      ctx.createMediaStreamSource(stream).connect(node)
    }
    // AudioWorklet 必须接入输出图才会持续处理；处理器不回传声音，因此不会产生回声。
    node.connect(ctx.destination)

    node.port.onmessage = (e: MessageEvent) => {
      const { pcm, rms, flushed } = e.data as {
        pcm: ArrayBuffer
        rms: number
        flushed?: boolean
      }
      if (ws.readyState !== WebSocket.OPEN) return
      const completingCapture = Boolean(
        flushed && pendingFlushMessagesRef.current.length,
      )
      if (pcm.byteLength && !aiSpeakingRef.current
        && (!committingRef.current || completingCapture)
        && !(manualRef.current && manualPausedRef.current)) {
        ws.send(pcm)
      }
      if (flushed) {
        const pendingMessage = pendingFlushMessagesRef.current.shift()
        if (pendingMessage) {
          ws.send(pendingMessage)
        } else if (replayAudioRef.current) {
          ws.send(JSON.stringify({ type: 'finish_audio' }))
        }
        return
      }
      if (aiSpeakingRef.current || committingRef.current) return
      // 手动模式：挂起时不采集（按钮控制）
      if (manualRef.current && manualPausedRef.current) return
      const now = performance.now()

      if (!hasSpeechRef.current) {
        if (rms > RMS_SPEECH_THRESHOLD) {
          hasSpeechRef.current = true
          lastSpeechAtRef.current = now
          answerStartedAtRef.current = now
          if (!committingRef.current) setStatus({ status: 'listening' })
        }
      } else {
        if (rms > RMS_SILENCE_THRESHOLD) {
          lastSpeechAtRef.current = now
        }
        // 自动模式才 VAD 自动提交
        const answerText = (finalTextRef.current + partialTextRef.current)
          .replace(/\s/g, '')
        const silenceLimit = answerText.length < SHORT_ANSWER_CHAR_LIMIT
          ? SHORT_ANSWER_SILENCE_MS
          : AUTO_COMMIT_SILENCE_MS
        if (!manualRef.current
          && now - answerStartedAtRef.current >= MIN_AUTO_TURN_MS
          && now - lastSpeechAtRef.current > silenceLimit) {
          commitTurnRef.current()
        }
      }
    }

    setStatus({ status: 'listening', connected: true })
  }, [opts?.replayAudioUrl, setStatus, sid])

  /** 提交本轮回答，进入 thinking */
  const commitTurn = useCallback(() => {
    const text = (finalTextRef.current + partialTextRef.current).trim()
    if (!text) {
      hasSpeechRef.current = false
      answerStartedAtRef.current = 0
      return
    }
    committingRef.current = true
    setStatus({ status: 'thinking', aiQuestion: null, aiQuestionDelivery: null })
    sendAfterCaptureFlush(JSON.stringify({
      type: 'commit_answer',
      payload: { text },
    }))
  }, [sendAfterCaptureFlush, setStatus])
  commitTurnRef.current = commitTurn

  /** 处理服务端消息 */
  const handleServerMsg = useCallback((msg: ServerMessage) => {
    const { type, payload } = msg
    const clearAITextDwell = () => {
      if (aiTextDwellTimerRef.current !== null) {
        window.clearTimeout(aiTextDwellTimerRef.current)
        aiTextDwellTimerRef.current = null
      }
    }
    const scheduleAITextDwell = (questionText: string) => {
      clearAITextDwell()
      aiSpeakingRef.current = true
      const dwellMs = Math.min(5000, Math.max(2500, questionText.length * 65))
      aiTextDwellTimerRef.current = window.setTimeout(() => {
        aiTextDwellTimerRef.current = null
        aiSpeakingRef.current = false
        hasSpeechRef.current = false
        answerStartedAtRef.current = 0
        committingRef.current = false
        const shouldWaitForManualStart = manualRef.current && !autoResumeRef.current
        if (shouldWaitForManualStart) manualPausedRef.current = true
        else onAnswerStartedRef.current?.()
        setStatus({ status: shouldWaitForManualStart ? 'idle' : 'listening' })
      }, dwellMs)
    }
    if (type === 'speech_recognized') {
      const text = typeof payload.text === 'string' ? payload.text : ''
      if (payload.is_final) {
        if (!text.trim()) {
          partialTextRef.current = ''
          partialSinceFinalRef.current = false
          setStatus({ partialText: '', highlightWords: new Map() })
          return
        }
        // ASR 偶尔会重发上一条 final。没有新的 partial 时，相同 final 视为同一消息。
        if (lastFinalTextRef.current === text && !partialSinceFinalRef.current) {
          partialTextRef.current = ''
          setStatus({ partialText: '', highlightWords: new Map() })
          return
        }
        const segment: TranscriptSegment = {
          id: `seg-${Date.now().toString(36)}-${++segmentSequenceRef.current}`,
          text,
          ts: Date.now(),
          annotations: [],
          analyzed: false,
        }
        const nextSegments = [...segmentsRef.current, segment]
        segmentsRef.current = nextSegments
        lastFinalTextRef.current = text
        partialSinceFinalRef.current = false
        finalTextRef.current = finalTextRef.current + text
        partialTextRef.current = ''
        setStatus({
          finalText: finalTextRef.current,
          partialText: '',
          segments: nextSegments,
          highlightWords: new Map(),
        })
      } else {
        partialTextRef.current = text
        if (text) partialSinceFinalRef.current = true
        setState((s) => ({
          ...s,
          partialText: text,
          // partial 会被 ASR 反复改写，只保留仍真实存在于当前文本的即时高亮。
          highlightWords: new Map(
            [...s.highlightWords].filter(([word]) => word && text.includes(word)),
          ),
        }))
      }
    } else if (type === 'analysis_update') {
      // 句子级实时分析：更新反馈列表、高亮词与可观察事实。
      applyAnalysisRef.current(payload)
    } else if (type === 'live_metrics') {
      // 实时指标：说话中滚动刷新（语速/发声时长），不定稿
      setStatus({
        liveMetrics: {
          speechRate: (payload.speech_rate as number | null) ?? null,
          speechRateLevel: (payload.speech_rate_level as LiveMetrics['speechRateLevel']) || 'unknown',
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
        // 即时词只映射当前 partial；迟到消息不能污染历史字幕。
        highlightWords: (() => {
          const hl = new Map(s.highlightWords)
          const word = fb.word?.trim()
          if (word && partialTextRef.current.includes(word)) {
            if (fb.kind === 'filler' || fb.kind === 'hedge') hl.set(word, fb.kind)
            if (fb.kind === 'repeat') hl.set(word, 'repeat')
          }
          return hl
        })(),
      }))
    } else if (type === 'ai_question') {
      // 问题文字先行显示（TTS 播报内容可能被 Qwen-Audio 微调，
      // 真正朗读的文字以 tts_audio 逐句 text 为准，见 enqueueTTS 的同步替换）
      ttsSpokenRef.current = ''
      clearAITextDwell()
      const delivery = payload.delivery === 'text' ? 'text' : 'voice'
      const questionText = typeof payload.text === 'string' ? payload.text : ''
      setStatus({
        aiQuestion: questionText,
        aiQuestionDelivery: delivery,
        interviewProgress: (payload.progress as InterviewProgress | null) ?? null,
        ...(delivery === 'text' ? { status: 'ai_speaking' as const } : {}),
      })
      // 无 TTS 时仍给用户一个短暂的非阻塞阅读时间，然后自动轮到用户回答。
      if (delivery === 'text') {
        scheduleAITextDwell(questionText)
      }
    } else if (type === 'stage_changed') {
      setStatus({ interviewProgress: (payload.progress as InterviewProgress | null) ?? null })
    } else if (type === 'tts_audio') {
      // 分句 TTS：用实际合成的句子同步替换显示文字（语音文字一致）
      clearAITextDwell()
      const sentText = (payload.text as string) || ''
      if (sentText) {
        ttsSpokenRef.current += sentText
        setStatus({ aiQuestion: ttsSpokenRef.current })
      }
      enqueueTTSRef.current(payload.audio as string, payload.format as string)
    } else if (type === 'ai_audio_unavailable') {
      // 整段 TTS 不可用时保留文字问题一个可读时间，再交还用户；若只是
      // 后续分句失败，已经在播放的队列负责维持 AI 发言状态。
      const questionText = typeof payload.text === 'string' && payload.text
        ? payload.text
        : ttsSpokenRef.current
      setStatus({
        status: 'ai_speaking',
        aiQuestionDelivery: 'text',
        ...(questionText ? { aiQuestion: questionText } : {}),
      })
      if (!ttsPlayingRef.current && ttsQueueRef.current.length === 0) {
        scheduleAITextDwell(questionText)
      }
    } else if (type === 'interview_completed') {
      forceEndedRef.current = true
      setStatus({ status: 'ended' })
    } else if (type === 'audio_finished' && replayAudioRef.current) {
      // 固定录音已由服务端冲刷完最后一句；稍等状态落稳后提交完整回答。
      window.setTimeout(() => commitTurnRef.current(), 250)
    } else if (type === 'timer_started') {
      // 计时零点确认：开场白播完，从此刻起计时（页面据此设 startedAt）
      onTimerStartedRef.current?.()
    } else if (type === 'time_up') {
      // 限时场景到点：只提示并继续收音。
      setStatus({ timeUp: true })
    } else if (type === 'hard_time_up') {
      // 到点后宽限 10 分钟：由页面可靠触发强制收尾。
      setStatus({ timeUp: true, hardTimeUp: true })
    } else if (type === 'error') {
      setStatus({ error: payload.message as string, status: 'error' })
    }
  }, [setStatus])
  handleServerMsgRef.current = handleServerMsg

  /** 句子级分析 → 反馈列表 + 高亮词 + 口头禅累计
   * 计数原则：partial（增量）提示不计数；final（定稿）才计数累加，
   * 且以该句内的真实出现次数为准（后端已按句分析，无跨 partial 重复）。
   */
  const applyAnalysis = useCallback((p: Record<string, unknown>) => {
    const sentence = typeof p.sentence === 'string' ? p.sentence : ''
    if (!sentence) return
    const isPartial = Boolean(p.partial_check)

    type NormalizedHit = { word: string; count: number; start?: number; end?: number }
    const normalizeHits = (value: unknown): NormalizedHit[] => {
      if (!Array.isArray(value)) return []
      const hits = new Map<string, number>()
      for (const item of value) {
        if (!item || typeof item !== 'object') continue
        const hit = item as Record<string, unknown>
        const word = typeof hit.word === 'string' ? hit.word.trim() : ''
        if (!word) continue
        const rawCount = typeof hit.count === 'number' && Number.isFinite(hit.count) ? hit.count : 0
        hits.set(word, Math.max(hits.get(word) || 0, Math.max(0, rawCount)))
      }
      return [...hits].map(([word, count]) => ({ word, count }))
    }

    const normalizePositionedHits = (value: unknown): NormalizedHit[] => {
      if (!Array.isArray(value)) return []
      return value.flatMap((item) => {
        if (!item || typeof item !== 'object') return []
        const hit = item as Record<string, unknown>
        const word = typeof hit.word === 'string' ? hit.word.trim() : ''
        const count = typeof hit.count === 'number' && Number.isFinite(hit.count)
          ? Math.max(0, hit.count)
          : 0
        const start = typeof hit.start === 'number' && Number.isInteger(hit.start) ? hit.start : undefined
        const end = typeof hit.end === 'number' && Number.isInteger(hit.end) ? hit.end : undefined
        return word ? [{ word, count, start, end }] : []
      })
    }

    const fillerHits = normalizeHits(p.filler_hits)
    const hedgeHits = normalizeHits(p.hedge_hits)
    const uncertainHits = normalizeHits(p.uncertain_hits)
    const repeated = normalizeHits(p.repeated_words)
    const explicitStutterHits = normalizePositionedHits(p.stutter_hits ?? p.consecutive_repetition_hits)
    // 兼容旧后端：只有确认同一词在原句中紧邻出现时，才把 repeated_words
    // 兜底为口吃；普通的非相邻词语复用不进入高亮、计数或建议。
    const isAdjacentStutter = (word: string) => {
      if (!word || word.length > 12) return false
      const escaped = word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      return new RegExp(`${escaped}[\\s\\u3000，,。.!！?？、；;：:]*${escaped}`).test(sentence)
    }
    const stutterHits = explicitStutterHits.length
      ? explicitStutterHits
      : repeated.filter((hit) => isAdjacentStutter(hit.word))

    const semanticRepeats: SemanticRepeatPair[] = []
    const rawSemanticRepeats = Array.isArray(p.semantic_repetitions)
      ? p.semantic_repetitions
      : Array.isArray(p.semantic_repeats)
        ? p.semantic_repeats
      : p.semantic_repetition && typeof p.semantic_repetition === 'object'
        ? [p.semantic_repetition]
        : p.semantic_repeat && typeof p.semantic_repeat === 'object'
          ? [p.semantic_repeat]
          : []
    for (const item of rawSemanticRepeats) {
      if (!item || typeof item !== 'object') continue
      const repeat = item as Record<string, unknown>
      const first = [repeat.first, repeat.sentence_a, repeat.previous, repeat.a, repeat.text_a, repeat.original, repeat.previous_sentence]
        .find((value): value is string => typeof value === 'string' && Boolean(value.trim()))
      const second = [repeat.second, repeat.sentence_b, repeat.current, repeat.b, repeat.text_b, repeat.repeated, repeat.current_sentence]
        .find((value): value is string => typeof value === 'string' && Boolean(value.trim()))
      if (!first || !second) continue
      const similarity = typeof repeat.similarity === 'number' ? repeat.similarity : undefined
      semanticRepeats.push({ first: first.trim(), second: second.trim(), similarity })
    }
    const longSentences = Array.isArray(p.long_sentences)
      ? p.long_sentences.filter((item): item is string => typeof item === 'string' && Boolean(item))
      : []

    const newFeedbacks: FeedbackItem[] = []
    const now = Date.now()
    const seq = Math.random().toString(36).slice(2, 7)

    // partial 提示：只高亮，不进反馈列表（避免与 final 重复刷屏）
    if (isPartial) {
      setState((s) => {
        const currentPartial = partialTextRef.current
        const hl = new Map(
          [...s.highlightWords].filter(([word]) => word && currentPartial.includes(word)),
        )
        for (const h of fillerHits) {
          const kind = hedgeHits.some((x) => x.word === h.word) ? 'hedge' : 'filler'
          if (currentPartial.includes(h.word)) hl.set(h.word, kind as 'filler' | 'hedge')
        }
        for (const h of hedgeHits) {
          if (currentPartial.includes(h.word)) hl.set(h.word, 'hedge')
        }
        for (const r of stutterHits) {
          if (currentPartial.includes(r.word)) hl.set(r.word, 'repeat')
        }
        return { ...s, highlightWords: hl }
      })
      return
    }

    // final 分析只消费最近一条文本匹配且尚未分析的 segment，天然抵御重复消息。
    const findMatchingSegment = (ignoreEdgeWhitespace: boolean) => {
      for (let index = segmentsRef.current.length - 1; index >= 0; index -= 1) {
        const candidate = segmentsRef.current[index]
        if (candidate.analyzed) continue
        const candidateText = ignoreEdgeWhitespace ? candidate.text.trim() : candidate.text
        const analysisText = ignoreEdgeWhitespace ? sentence.trim() : sentence
        if (candidateText === analysisText) return index
      }
      return -1
    }
    const exactIndex = findMatchingSegment(false)
    const segmentIndex = exactIndex >= 0 ? exactIndex : findMatchingSegment(true)
    if (segmentIndex < 0) return

    const segment = segmentsRef.current[segmentIndex]
    const annotations: TranscriptAnnotation[] = []
    const annotationKeys = new Set<string>()
    const hedgeWords = new Set(hedgeHits.map((hit) => hit.word))
    const appendOccurrences = (word: string, kind: TranscriptAnnotation['kind']) => {
      if (!word) return
      let from = 0
      while (from <= segment.text.length - word.length) {
        const start = segment.text.indexOf(word, from)
        if (start < 0) break
        const end = start + word.length
        const key = `${start}:${end}:${kind}:${word}`
        if (!annotationKeys.has(key)) {
          annotationKeys.add(key)
          annotations.push({ start, end, kind, word })
        }
        // 前进一个 UTF-16 code unit，确保重叠出现的位置也会被记录。
        from = start + 1
      }
    }
    for (const hit of fillerHits) {
      if (!hedgeWords.has(hit.word)) appendOccurrences(hit.word, 'filler')
    }
    for (const hit of hedgeHits) appendOccurrences(hit.word, 'hedge')
    for (const hit of stutterHits) {
      if (hit.start !== undefined && hit.end !== undefined
        && hit.start >= 0 && hit.end <= segment.text.length && hit.end > hit.start) {
        const key = `${hit.start}:${hit.end}:repeat:${hit.word}`
        if (!annotationKeys.has(key)) {
          annotationKeys.add(key)
          annotations.push({ start: hit.start, end: hit.end, kind: 'repeat', word: hit.word })
        }
      } else {
        appendOccurrences(hit.word, 'repeat')
      }
    }
    annotations.sort((a, b) => a.start - b.start || b.end - a.end || a.kind.localeCompare(b.kind))

    const nextSegments = [...segmentsRef.current]
    nextSegments[segmentIndex] = { ...segment, annotations, analyzed: true }
    segmentsRef.current = nextSegments
    onAnalysisRef.current?.(p)

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
        advice: '这类保留式措辞会削弱结论，可改为明确判断或具体条件',
      })
    }
    for (const r of stutterHits) {
      newFeedbacks.push({
        id: `fb-${now}-${seq}-r-${r.word}`,
        kind: 'repeat', word: r.word, count: r.count, sentence, ts: now,
        advice: '出现连续重启，停半秒后从完整词重新开始',
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
      // 口头禅累计：final 的 count 是该句内真实次数（partial 不计数，不再虚高）
      const totals = { ...s.fillerTotals }
      for (const h of fillerHits) totals[h.word] = (totals[h.word] || 0) + h.count
      return {
        ...s,
        segments: nextSegments,
        feedbacks: [...s.feedbacks, ...newFeedbacks].slice(-50),
        fillerTotals: totals,
        stutterTotals: stutterHits.reduce<Record<string, number>>((all, hit) => {
          all[hit.word] = (all[hit.word] || 0) + hit.count
          return all
        }, { ...s.stutterTotals }),
        semanticRepeats: semanticRepeats.length
          ? [...new Map(
            [...s.semanticRepeats, ...semanticRepeats].map((item) => [`${item.first}\u0000${item.second}`, item]),
          ).values()].slice(-6)
          : s.semanticRepeats,
        semanticRepeatTotal: s.semanticRepeatTotal + semanticRepeats.length,
        issueCount: s.issueCount + newFeedbacks.length,
      }
    })
  }, [])
  applyAnalysisRef.current = applyAnalysis

  /** TTS 分句队列：顺序播放，接续无缝 */
  const enqueueTTS = useCallback((b64: string, fmt: string) => {
    aiSpeakingRef.current = true
    setStatus({ status: 'ai_speaking' })
    ttsQueueRef.current.push({ b64, fmt })
    if (!ttsPlayingRef.current) playNextTTSRef.current()
  }, [setStatus])
  enqueueTTSRef.current = enqueueTTS

  const playNextTTS = useCallback(function playNextTTSItem() {
    const item = ttsQueueRef.current.shift()
    if (!item) {
      // 队列播完：恢复监听；限时场景通知后端计时零点（幂等，仅首次生效）
      ttsPlayingRef.current = false
      if (aiTextDwellTimerRef.current !== null) {
        window.clearTimeout(aiTextDwellTimerRef.current)
        aiTextDwellTimerRef.current = null
      }
      aiSpeakingRef.current = false
      hasSpeechRef.current = false
      answerStartedAtRef.current = 0
      finalTextRef.current = ''
      partialTextRef.current = ''
      segmentsRef.current = []
      lastFinalTextRef.current = null
      partialSinceFinalRef.current = false
      wsRef.current?.send(JSON.stringify({ type: 'begin_timer' }))
      if (manualRef.current) {
        // 手动模式：挂起采集，等用户点"开始回答"（限时场景 autoResume 除外）
        if (autoResumeRef.current) {
          committingRef.current = false
          setStatus({
            status: 'listening', partialText: '', finalText: '', segments: [], highlightWords: new Map(),
            feedbacks: [], issueCount: 0, liveMetrics: null,
          })
        } else {
          manualPausedRef.current = true
          setStatus({
            status: 'idle', partialText: '', finalText: '', segments: [], highlightWords: new Map(),
            feedbacks: [], issueCount: 0, liveMetrics: null,
          })
        }
      } else {
        committingRef.current = false
        onAnswerStartedRef.current?.()
        setStatus({
          status: 'listening', partialText: '', finalText: '', segments: [], highlightWords: new Map(),
          feedbacks: [], issueCount: 0, liveMetrics: null,
        })
      }
      return
    }
    ttsPlayingRef.current = true
    const url = `data:audio/${item.fmt || 'wav'};base64,${item.b64}`
    const el = new Audio(url)
    el.onended = playNextTTSItem
    el.onerror = playNextTTSItem
    el.play().catch(playNextTTSItem)
  }, [setStatus])
  playNextTTSRef.current = playNextTTS

  /** 主动请求第一题：先置 thinking（AI 准备中），收到 ai_question + TTS 播报 */
  const requestFirstQuestion = useCallback(() => {
    setStatus({ status: 'thinking' })
    wsRef.current?.send(JSON.stringify({ type: 'start_stage' }))
  }, [setStatus])

  /** 单人限时训练：跳过 AI 开场与 TTS，直接进入收音和计时。 */
  const beginSoloPractice = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ type: 'start_solo_stage' }))
    setStatus({ status: 'listening', aiQuestion: null, aiQuestionDelivery: null })
  }, [setStatus])

  /** 手动模式：开始回答（恢复采集，从 thinking/ai_speaking 回到 listening） */
  const beginAnswer = useCallback(() => {
    manualPausedRef.current = false
    committingRef.current = false
    hasSpeechRef.current = false
    answerStartedAtRef.current = 0
    onAnswerStartedRef.current?.()
    setStatus({ status: 'listening' })
    const replayAudio = replayAudioRef.current
    if (replayAudio) {
      replayAudio.currentTime = 0
      void ctxRef.current?.resume()
      void replayAudio.play().catch(() => {
        setStatus({ status: 'error', error: '浏览器未能开始播放录音，请再点一次“开始回答”' })
      })
    }
  }, [setStatus])

  /** 手动模式：提交回答（按钮触发，无 VAD） */
  const commitAnswer = useCallback(() => {
    commitTurn()
  }, [commitTurn])

  /** 限时场景：讲完本阶段（落库已说内容，推进阶段） */
  const finishStage = useCallback(() => {
    const text = (finalTextRef.current + partialTextRef.current).trim()
    sendAfterCaptureFlush(JSON.stringify({ type: 'finish_stage', payload: { text } }))
    // 阶段切换后清空本阶段字幕与教练提示；分析结果已由后端保存到报告。
    finalTextRef.current = ''
    partialTextRef.current = ''
    segmentsRef.current = []
    lastFinalTextRef.current = null
    partialSinceFinalRef.current = false
    hasSpeechRef.current = false
    answerStartedAtRef.current = 0
    committingRef.current = false
    setStatus({
      finalText: '', partialText: '', segments: [], highlightWords: new Map(), timeUp: false, hardTimeUp: false,
      feedbacks: [], issueCount: 0, liveMetrics: null,
    })
  }, [sendAfterCaptureFlush, setStatus])

  /** 结束面试 */
  const endInterview = useCallback(() => {
    const text = (finalTextRef.current + partialTextRef.current).trim()
    sendAfterCaptureFlush(JSON.stringify({ type: 'end_interview', payload: { text } }))
  }, [sendAfterCaptureFlush])

  /** 面试专项：跳过当前能力方向，由后端计划切换到下一个未覆盖维度。 */
  const skipQuestionDirection = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ type: 'skip_topic' }))
    setStatus({ status: 'thinking', aiQuestion: null, aiQuestionDelivery: null })
  }, [setStatus])

  /** 完全停止（离开页面） */
  const stop = useCallback(() => {
    nodeRef.current?.disconnect()
    nodeRef.current = null
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    ctxRef.current?.close()
    ctxRef.current = null
    replayAudioRef.current?.pause()
    replayAudioRef.current = null
    replaySourceRef.current?.disconnect()
    replaySourceRef.current = null
    pendingFlushMessagesRef.current = []
    audioCtxPlayRef.current?.close()
    audioCtxPlayRef.current = null
    ttsQueueRef.current = []
    ttsPlayingRef.current = false
    if (aiTextDwellTimerRef.current !== null) {
      window.clearTimeout(aiTextDwellTimerRef.current)
      aiTextDwellTimerRef.current = null
    }
    aiSpeakingRef.current = false
    manualStopRef.current = true   // 标记主动停止，避免 onclose 误触发跳转
    wsRef.current?.close()
    wsRef.current = null
    finalTextRef.current = ''
    partialTextRef.current = ''
    segmentsRef.current = []
    lastFinalTextRef.current = null
    partialSinceFinalRef.current = false
    hasSpeechRef.current = false
    answerStartedAtRef.current = 0
    committingRef.current = false
    setStatus({
      status: 'idle', connected: false, partialText: '', finalText: '', segments: [],
      aiQuestion: null, aiQuestionDelivery: null, error: null, feedbacks: [], highlightWords: new Map(),
      fillerTotals: {}, stutterTotals: {}, semanticRepeats: [], issueCount: 0, timeUp: false, hardTimeUp: false, liveMetrics: null,
      semanticRepeatTotal: 0,
      interviewProgress: null,
    })
  }, [setStatus])

  /** 清空本轮反馈（进入新题目时） */
  const resetFeedbacks = useCallback(() => {
    finalTextRef.current = ''
    partialTextRef.current = ''
    segmentsRef.current = []
    lastFinalTextRef.current = null
    partialSinceFinalRef.current = false
    answerStartedAtRef.current = 0
    setState((s) => ({
      ...s,
      feedbacks: [],
      highlightWords: new Map(),
      finalText: '',
      partialText: '',
      segments: [],
      issueCount: 0,
    }))
  }, [])

  useEffect(() => stop, [stop])

  return { state, start, stop, requestFirstQuestion, beginSoloPractice, endInterview, skipQuestionDirection, resetFeedbacks, beginAnswer, commitAnswer, finishStage }
}
