export type LiveHighlightKind = 'filler' | 'repeat' | 'hedge'

/**
 * 合并已定稿累计与当前字幕中的即时口头禅命中。
 *
 * ASR partial 会反复改写整句，因此即时命中必须每次按当前文本重算，
 * 不能写入持久累计；句子定稿后 partial 清空，准确分析结果自然接管。
 */
export function getDisplayedFillerTotals(
  finalizedTotals: Record<string, number>,
  highlightWords: ReadonlyMap<string, LiveHighlightKind>,
  partialText: string,
): Record<string, number> {
  const displayedTotals = { ...finalizedTotals }

  for (const [word, kind] of highlightWords) {
    if (kind !== 'filler' || !word || !partialText.includes(word)) continue

    let occurrences = 0
    let cursor = 0
    while (cursor <= partialText.length - word.length) {
      const index = partialText.indexOf(word, cursor)
      if (index < 0) break
      occurrences += 1
      cursor = index + word.length
    }

    if (occurrences > 0) {
      displayedTotals[word] = (displayedTotals[word] || 0) + occurrences
    }
  }

  return displayedTotals
}
