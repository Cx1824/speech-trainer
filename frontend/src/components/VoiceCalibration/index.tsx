/**
 * 声音校准卡片：朗读一段校准文本 → 建立个人表达基线。
 *
 * 流程：
 *   1. GET /config/voice-calibration 拿校准文本 + 当前基线状态
 *   2. 点「开始校准」→ 麦克风 AudioWorklet 16k PCM → WS /ws/voice/calibrate
 *   3. 读完点「完成」→ 后端聚合特征落库 → 推送 calibration_result
 *   4. 换人：直接重新校准（覆盖）或先清除
 */
import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Space, Tag, message } from 'antd'
import { AudioOutlined, CheckCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { apiService } from '@/services/api'

interface CalibState {
  loading: boolean
  text: string
  estimatedSec: number
  calibrated: boolean
  baseline: Record<string, number | string> | null
  // 校准会话
  phase: 'idle' | 'recording' | 'analyzing' | 'done'
  resultMsg: string
  resultOk: boolean | null
}

interface VoiceCalibrationProps {
  /** 校准成功/清除后通知父级（首页引导条据此刷新） */
  onChanged?: (calibrated: boolean) => void
}

export default function VoiceCalibration({ onChanged }: VoiceCalibrationProps) {
  const [st, setSt] = useState<CalibState>({
    loading: true,
    text: '',
    estimatedSec: 0,
    calibrated: false,
    baseline: null,
    phase: 'idle',
    resultMsg: '',
    resultOk: null,
  })

  const wsRef = useRef<WebSocket | null>(null)
  const ctxRef = useRef<AudioContext | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const nodeRef = useRef<AudioWorkletNode | null>(null)

  const load = async () => {
    try {
      const d = await apiService.getVoiceCalibration()
      setSt((s) => ({
        ...s,
        loading: false,
        text: d.text,
        estimatedSec: d.estimated_sec,
        calibrated: d.calibrated,
        baseline: d.baseline,
      }))
    } catch {
      setSt((s) => ({ ...s, loading: false }))
    }
  }

  useEffect(() => {
    load()
    return () => {
      // 组件卸载时清理采集
      nodeRef.current?.disconnect()
      streamRef.current?.getTracks().forEach((t) => t.stop())
      ctxRef.current?.close()
      wsRef.current?.close()
    }
  }, [])

  const stopCapture = () => {
    nodeRef.current?.disconnect()
    nodeRef.current = null
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    ctxRef.current?.close()
    ctxRef.current = null
  }

  const startCalibration = async () => {
    if (wsRef.current) return
    setSt((s) => ({ ...s, phase: 'recording', resultOk: null, resultMsg: '' }))
    try {
      // 1. WS
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
      const ws = new WebSocket(`${proto}//${location.host}/ws/voice/calibrate`)
      ws.binaryType = 'arraybuffer'
      wsRef.current = ws

      ws.onmessage = (e) => {
        if (typeof e.data !== 'string') return
        try {
          const msg = JSON.parse(e.data)
          if (msg.type === 'calibration_result') {
            const p = msg.payload as { ok: boolean; message: string; baseline?: Record<string, number | string> }
            setSt((s) => ({
              ...s,
              phase: p.ok ? 'done' : 'idle',
              resultOk: p.ok,
              resultMsg: p.message,
              calibrated: p.ok ? true : s.calibrated,
              baseline: p.baseline ?? (p.ok ? s.baseline : s.baseline),
            }))
            if (p.ok) message.success('校准完成')
            else message.warning(p.message)
            onChanged?.(p.ok)
            stopCapture()
            ws.close()
            wsRef.current = null
          }
        } catch {
          // ignore
        }
      }
      ws.onerror = () => {
        message.error('校准通道连接失败')
        setSt((s) => ({ ...s, phase: 'idle' }))
        stopCapture()
        wsRef.current = null
      }

      // 2. 麦克风（与训练页同款采集链路）
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
        const { pcm, flushed } = e.data as { pcm: ArrayBuffer; flushed?: boolean }
        if (ws.readyState !== WebSocket.OPEN) return
        if (pcm.byteLength) ws.send(pcm)
        if (flushed) ws.send(JSON.stringify({ type: 'finish' }))
      }
    } catch (e) {
      message.error(`无法开始校准：${e}`)
      setSt((s) => ({ ...s, phase: 'idle' }))
      stopCapture()
    }
  }

  const finishCalibration = () => {
    setSt((s) => ({ ...s, phase: 'analyzing' }))
    if (nodeRef.current) nodeRef.current.port.postMessage({ type: 'flush' })
    else wsRef.current?.send(JSON.stringify({ type: 'finish' }))
  }

  const resetCalibration = async () => {
    try {
      await apiService.resetVoiceCalibration()
      message.success('已清除基线')
      setSt((s) => ({ ...s, calibrated: false, baseline: null, resultOk: null, resultMsg: '' }))
      onChanged?.(false)
    } catch (e) {
      message.error(`清除失败：${e}`)
    }
  }

  const recording = st.phase === 'recording'

  return (
    <Card
      className="voice-calibration-card"
      title="声音校准（个人表达基线）"
      style={{ marginBottom: 24 }}
      extra={
        st.calibrated ? (
          <Tag icon={<CheckCircleOutlined />} color="success">已校准</Tag>
        ) : (
          <Tag color="warning">未校准</Tag>
        )
      }
    >
      {!st.loading && !st.calibrated && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="先建立一次个人表达基线"
          description="请用平时自然说话的方式朗读下面这段话（约 30 秒）。系统会记录语速、音调与停顿习惯，训练时只比较本次表达相对个人基线的可观察变化，不推断紧张、自信或其他心理状态。换人使用时请重新校准。"
        />
      )}
      {st.calibrated && st.baseline && (
        <Alert
          type="success"
          showIcon
          style={{ marginBottom: 12 }}
          message="已建立个人表达基线，训练时将比较声音与节奏的相对变化"
          description={`校准时间：${String(st.baseline.created_at || '').slice(0, 19).replace('T', ' ')} · 朗读 ${Number(st.baseline.sample_sec || 0).toFixed(0)} 秒 · 语速 ${Number(st.baseline.speech_rate || 0).toFixed(1)} 字/秒`}
        />
      )}

      {st.text && (
        <div
          style={{
            background: '#fafafa',
            border: '1px solid #f0f0f0',
            borderRadius: 8,
            padding: '12px 16px',
            fontSize: 15,
            lineHeight: 1.9,
            marginBottom: 16,
            color: recording ? '#1677ff' : undefined,
          }}
        >
          {st.text}
        </div>
      )}

      <Space wrap>
        {!recording && st.phase !== 'analyzing' && (
          <Button type="primary" icon={<AudioOutlined />} onClick={startCalibration}>
            {st.calibrated ? '重新校准（换人时用）' : '开始校准'}
          </Button>
        )}
        {recording && (
          <Button type="primary" danger icon={<AudioOutlined />} onClick={finishCalibration}>
            我读完了，完成校准
          </Button>
        )}
        {st.phase === 'analyzing' && <Button loading>正在计算基线…</Button>}
        {st.calibrated && !recording && st.phase !== 'analyzing' && (
          <Button icon={<ReloadOutlined />} onClick={resetCalibration}>
            清除基线
          </Button>
        )}
      </Space>

      {recording && (
        <div style={{ marginTop: 12, fontSize: 13, color: '#1677ff', display: 'flex', gap: 6, alignItems: 'center' }}>
          <AudioOutlined aria-hidden />
          <span>录音中，请自然读完上面的文字，然后点「完成校准」</span>
        </div>
      )}
      {st.resultMsg && st.resultOk !== null && (
        <Alert
          type={st.resultOk ? 'success' : 'warning'}
          showIcon
          style={{ marginTop: 12 }}
          message={st.resultMsg}
        />
      )}
    </Card>
  )
}
