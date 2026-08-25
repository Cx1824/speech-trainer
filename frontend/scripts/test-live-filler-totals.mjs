import assert from 'node:assert/strict'
import { stdout } from 'node:process'
import { getDisplayedFillerTotals } from '../src/utils/liveFillerTotals.ts'

// 即时标签出现时，累计不能仍显示 0。
assert.deepEqual(
  getDisplayedFillerTotals({}, new Map([['然后', 'filler']]), '然后我介绍项目背景'),
  { 然后: 1 },
)

// partial 被 ASR 改写时按当前文本重算，不把已经撤回的临时结果留下。
assert.deepEqual(
  getDisplayedFillerTotals({}, new Map([['然后', 'filler']]), '我介绍项目背景'),
  {},
)

// 同一 partial 中多次出现按真实次数展示。
assert.deepEqual(
  getDisplayedFillerTotals({ 然后: 2 }, new Map([['然后', 'filler']]), '然后，然后继续'),
  { 然后: 4 },
)

// final 到达后 partial 清空，只保留定稿累计，不会重复相加。
assert.deepEqual(
  getDisplayedFillerTotals({ 然后: 3 }, new Map(), ''),
  { 然后: 3 },
)

// 模糊词和连续重复使用独立指标，不计入口头禅。
assert.deepEqual(
  getDisplayedFillerTotals({}, new Map([['可能', 'hedge'], ['我', 'repeat']]), '可能我我会完成'),
  {},
)

stdout.write('live filler totals: 5 cases passed\n')
