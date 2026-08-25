import assert from 'node:assert/strict'

import { RecorderProcessor, StreamingResampler } from '../public/worklets/recorder.js'

const INPUT_RATE = 48000
const OUTPUT_RATE = 16000

function sine(frequency, seconds = 1) {
  return Float32Array.from(
    { length: INPUT_RATE * seconds },
    (_, index) => Math.sin(2 * Math.PI * frequency * index / INPUT_RATE),
  )
}

function resample(input, blockSize) {
  const resampler = new StreamingResampler(INPUT_RATE, OUTPUT_RATE)
  const parts = []
  for (let offset = 0; offset < input.length; offset += blockSize) {
    parts.push(...resampler.process(input.slice(offset, offset + blockSize)))
  }
  parts.push(...resampler.process(new Float32Array(0), true))
  return Float32Array.from(parts)
}

function rms(input) {
  let energy = 0
  for (const value of input) energy += value * value
  return Math.sqrt(energy / input.length)
}

const speechBand = sine(1000)
const blockwise = resample(speechBand, 960)
const singleBlock = resample(speechBand, speechBand.length)
assert.equal(blockwise.length, OUTPUT_RATE)
assert.equal(singleBlock.length, OUTPUT_RATE)

let maximumDifference = 0
for (let index = 0; index < blockwise.length; index++) {
  maximumDifference = Math.max(
    maximumDifference,
    Math.abs(blockwise[index] - singleBlock[index]),
  )
}
assert.ok(maximumDifference < 1e-6, '跨处理块应保持连续的滤波状态和采样相位')
assert.ok(rms(blockwise) > 0.69, '语音频段不应被明显衰减')

const outOfBand = resample(sine(10000), 960)
assert.ok(rms(outOfBand) < 0.01, '8kHz以上信号应在降采样前被滤除')

const passthrough = new StreamingResampler(OUTPUT_RATE, OUTPUT_RATE).process(
  Float32Array.from([0.2, -0.3, 0.4]),
)
assert.deepEqual([...passthrough], [...Float32Array.from([0.2, -0.3, 0.4])])

const processor = new RecorderProcessor()
const messages = []
processor.port.postMessage = (message) => messages.push(message)
processor.process([[Float32Array.from({ length: 127 }, () => 0.25)]])
processor.port.onmessage({ data: { type: 'flush' } })
assert.equal(messages.length, 1)
assert.equal(messages[0].flushed, true)
assert.ok(messages[0].pcm.byteLength > 0, '不足一个发送块的尾音也必须被提交')

processor.process([[Float32Array.from({ length: 960 }, () => 0.25)]])
assert.equal(messages.length, 2)
assert.equal(messages[1].flushed, false)
