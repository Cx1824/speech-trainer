/**
 * 麦克风采集 AudioWorklet。
 *
 * 采集 Float32 @ ctx.sampleRate，经带低通滤波的状态化重采样转换为
 * 16kHz Int16 PCM。重采样器跨处理块保留相位和滤波历史，避免简单抽样
 * 把 8kHz 以上噪声折叠进语音频段。
 */

export const TARGET_SAMPLE_RATE = 16000

function sinc(value) {
  if (Math.abs(value) < 1e-8) return 1
  const radians = Math.PI * value
  return Math.sin(radians) / radians
}

export class StreamingResampler {
  constructor(inputRate, outputRate = TARGET_SAMPLE_RATE, taps = 63, phaseCount = 256) {
    if (!(inputRate > 0) || !(outputRate > 0)) throw new Error('采样率必须大于 0')
    if (taps < 3 || taps % 2 === 0) throw new Error('滤波器长度必须是大于 1 的奇数')
    this.inputRate = inputRate
    this.outputRate = outputRate
    this.ratio = inputRate / outputRate
    this.half = (taps - 1) / 2
    this.phaseCount = phaseCount
    this.passthrough = inputRate === outputRate
    this.buffer = this.passthrough ? new Float32Array(0) : new Float32Array(this.half)
    this.position = this.half
    this.kernels = this.passthrough ? [] : this._buildKernels(taps)
  }

  _buildKernels(taps) {
    const cutoff = 0.5 * Math.min(1, this.outputRate / this.inputRate) * 0.94
    const kernels = []
    for (let phase = 0; phase < this.phaseCount; phase++) {
      const fraction = phase / this.phaseCount
      const kernel = new Float64Array(taps)
      let sum = 0
      for (let tap = 0; tap < taps; tap++) {
        const offset = tap - this.half - fraction
        const window = 0.5 - 0.5 * Math.cos((2 * Math.PI * tap) / (taps - 1))
        const coefficient = 2 * cutoff * sinc(2 * cutoff * offset) * window
        kernel[tap] = coefficient
        sum += coefficient
      }
      if (sum !== 0) {
        for (let tap = 0; tap < taps; tap++) kernel[tap] /= sum
      }
      kernels.push(kernel)
    }
    return kernels
  }

  process(input, finalize = false) {
    if (this.passthrough) return Float32Array.from(input)

    const signalLength = this.buffer.length + input.length
    const padding = finalize ? this.half + Math.ceil(this.ratio) : 0
    const combined = new Float32Array(signalLength + padding)
    combined.set(this.buffer)
    combined.set(input, this.buffer.length)
    const output = []

    while (
      this.position + this.half < combined.length
      && (!finalize || this.position < signalLength)
    ) {
      const center = Math.floor(this.position)
      const fraction = this.position - center
      const phase = Math.min(
        this.phaseCount - 1,
        Math.round(fraction * this.phaseCount),
      )
      const kernel = this.kernels[phase]
      const start = center - this.half
      let value = 0
      for (let tap = 0; tap < kernel.length; tap++) {
        value += combined[start + tap] * kernel[tap]
      }
      output.push(value)
      this.position += this.ratio
    }

    if (finalize) {
      this.buffer = new Float32Array(this.half)
      this.position = this.half
    } else {
      const discard = Math.max(0, Math.floor(this.position) - this.half)
      this.buffer = combined.slice(discard)
      this.position -= discard
    }
    return Float32Array.from(output)
  }
}

const WorkletBase = globalThis.AudioWorkletProcessor ?? class {
  constructor() {
    this.port = { postMessage() {}, onmessage: null }
  }
}

export class RecorderProcessor extends WorkletBase {
  constructor() {
    super()
    const inputRate = Number(globalThis.sampleRate || 48000)
    this._buffer = new Float32Array(Math.max(128, Math.round(inputRate * 0.02)))
    this._len = 0
    this._resampler = new StreamingResampler(inputRate)
    this.port.onmessage = (event) => {
      if (event.data?.type === 'flush') this._flushPending()
    }
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0]
    if (!channel) return true

    for (let index = 0; index < channel.length; index++) {
      this._buffer[this._len++] = channel[index]
      if (this._len === this._buffer.length) {
        this._emit(this._buffer, false)
        this._len = 0
      }
    }
    return true
  }

  _flushPending() {
    const pending = this._buffer.slice(0, this._len)
    this._len = 0
    this._emit(pending, true)
  }

  _emit(input, finalize) {
    let sum = 0
    for (let index = 0; index < input.length; index++) sum += input[index] * input[index]
    const rms = input.length ? Math.sqrt(sum / input.length) : 0
    const resampled = this._resampler.process(input, finalize)
    const pcm = new Int16Array(resampled.length)
    for (let index = 0; index < resampled.length; index++) {
      const value = Math.max(-1, Math.min(1, resampled[index]))
      pcm[index] = value < 0 ? value * 32768 : value * 32767
    }
    this.port.postMessage(
      { pcm: pcm.buffer, rms, flushed: finalize },
      [pcm.buffer],
    )
  }
}

if (typeof globalThis.registerProcessor === 'function') {
  globalThis.registerProcessor('recorder-processor', RecorderProcessor)
}
