import { useEffect, useRef } from 'react'
import './Danmu.css'

export interface DanmuItem {
  id: string
  text: string
  level?: 'normal' | 'filler' | 'repeat'
}

interface DanmuProps {
  items: DanmuItem[]
}

const FILLER_WORDS = ['然后', '就是', '嗯', '那个', '呃', '其实', '基本上']

function detectLevel(text: string): 'normal' | 'filler' | 'repeat' {
  // 简单规则：含口癖词 → filler，同段重复字符 → repeat
  if (FILLER_WORDS.some((w) => text.includes(w))) return 'filler'
  const words = text.split(/[\s,，。、]+/).filter(Boolean)
  const set = new Set(words)
  if (words.length > 4 && words.length - set.size >= 2) return 'repeat'
  return 'normal'
}

export default function Danmu({ items }: DanmuProps) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    // 自动清理已飞出屏幕的弹幕
    const handle = setInterval(() => {
      el.querySelectorAll('.danmu-item').forEach((node) => {
        const rect = node.getBoundingClientRect()
        if (rect.right < 0) node.remove()
      })
    }, 1000)
    return () => clearInterval(handle)
  }, [])

  return (
    <div className="danmu-container" ref={containerRef}>
      {items.map((item, idx) => {
        const level = item.level ?? detectLevel(item.text)
        const top = 8 + ((idx % 8) * 36)
        const duration = 8 + (item.text.length > 20 ? 4 : 0)
        return (
          <div
            key={item.id}
            className={`danmu-item danmu-${level}`}
            style={{ top: `${top}px`, animationDuration: `${duration}s` }}
          >
            {item.text}
          </div>
        )
      })}
    </div>
  )
}
