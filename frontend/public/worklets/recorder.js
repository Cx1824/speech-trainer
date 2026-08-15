/**
 * 麦克风采集 AudioWorklet。
 * 采集 Float32 @ ctx.sampleRate → 降采样到 16kHz → Int16 PCM → 主线程。
 * 每帧附带 RMS 能量（供 VAD 判断说完）。
 * postMessage 格式：{ pcm: Int16Array, rms: number }
 */
class RecorderProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this._buffer = new Float32Array(2048)
    this._len = 0
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0]
    if (!ch) return true

    for (let i = 0; i < ch.length; i++) {
      this._buffer[this._len++] = ch[i]
      if (this._len === this._buffer.length) {
        this._flush(this._buffer)
        this._len = 0
      }
    }
    return true
  }

  _flush(f32) {
    // RMS 能量
    let sum = 0
    for (let i = 0; i < f32.length; i++) sum += f32[i] * f32[i]
    const rms = Math.sqrt(sum / f32.length)

    // 降采样到 16kHz（线性插值，够用）
    const ratio = sampleRate / 16000
    const outLen = Math.floor(f32.length / ratio)
    const pcm = new Int16Array(outLen)
    for (let i = 0; i < outLen; i++) {
      const src = i * ratio
      const idx = Math.floor(src)
      const frac = src - idx
      const v = idx + 1 < f32.length ? f32[idx] * (1 - frac) + f32[idx + 1] * frac : f32[idx]
      pcm[i] = Math.max(-1, Math.min(1, v)) * 32767
    }
    this.port.postMessage({ pcm: pcm.buffer, rms }, [pcm.buffer])
  }
}

registerProcessor('recorder-processor', RecorderProcessor)
