import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

/**
 * 统一的返回按钮：胶囊描边 + 箭头 + hover 反馈。
 * - light（默认）：白底灰字，用于浅色页面
 * - dark：透明白描边，用于深色顶栏内
 */
export default function BackLink({
  to,
  children,
  variant = 'light',
}: {
  to: string
  children: ReactNode
  variant?: 'light' | 'dark'
}) {
  const cls =
    variant === 'dark'
      ? 'inline-flex items-center gap-1.5 rounded-full border border-white/30 bg-white/5 px-3.5 py-1.5 text-xs text-white/80 backdrop-blur-sm hover:bg-white/15 hover:text-white hover:border-white/50 transition'
      : 'inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3.5 py-1.5 text-sm text-slate-600 shadow-sm hover:border-[#1e3a5f] hover:text-[#1e3a5f] hover:shadow transition'
  return (
    <Link to={to} className={cls}>
      <span aria-hidden className="text-base leading-none">←</span>
      {children}
    </Link>
  )
}
