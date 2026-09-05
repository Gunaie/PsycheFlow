import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiGet, getToken } from '../api'
import FooterDisclaimer from '../components/FooterDisclaimer'

interface ScaleSummary {
  scale_id: string
  scale_name: string
  description: string
  item_count: number
}

/** 各量表卡片配图与摘要（key=scale_id） */
const SCALE_META: Record<string, { icon: string; tag: string }> = {
  phq_a: { icon: '🌧️', tag: '抑郁筛查' },
  scared: { icon: '🌬️', tag: '焦虑筛查' },
  sdq: { icon: '🌱', tag: '长处与困难' },
  mht: { icon: '🧭', tag: '综合诊断' },
}

export default function ScaleSelectPage() {
  const [scales, setScales] = useState<ScaleSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const token = getToken()

  useEffect(() => {
    apiGet<ScaleSummary[]>('/api/scales')
      .then(setScales)
      .catch((e: Error) => setError(e.message))
  }, [])

  return (
    <div className="space-y-8">
      {/* 页面标题 */}
      <div>
        <h1 className="text-2xl font-bold text-slate-800">选择测评量表</h1>
        <p className="text-sm text-slate-500 mt-1">
          {token ? '选择一份量表开始测评，完成后可查看结果并下载报告' : '登录后即可开始测评'}
        </p>
      </div>

      {error && (
        <div className="bg-red-50 text-red-600 text-sm p-3 rounded-lg">加载失败：{error}</div>
      )}

      {/* 推荐方案：合并筛查 */}
      <Link
        to="/scale/combined"
        className="block bg-gradient-to-r from-[#1e3a5f] to-[#2a5485] text-white rounded-2xl shadow-md p-6 hover:shadow-lg transition"
      >
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-start gap-4">
            <div className="w-12 h-12 rounded-xl bg-white/15 flex items-center justify-center text-2xl shrink-0">
              ⭐
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="font-bold text-lg">PHQ-A + SCARED 合并筛查</span>
                <span className="text-[10px] bg-amber-300 text-amber-900 px-1.5 py-0.5 rounded font-semibold">推荐</span>
              </div>
              <p className="text-sm text-blue-100 mt-1">抑郁 + 焦虑双量表联合评估，一次性完成两份筛查</p>
            </div>
          </div>
          <span className="text-2xl shrink-0 opacity-80">→</span>
        </div>
      </Link>

      {/* 分隔标题 */}
      <div className="flex items-center gap-3">
        <h2 className="text-base font-semibold text-slate-700 shrink-0">单项量表</h2>
        <div className="flex-1 h-px bg-slate-200" />
      </div>

      {/* 单项量表卡片网格 */}
      <div className="grid gap-4 sm:grid-cols-2">
        {scales.map((s) => {
          const meta = SCALE_META[s.scale_id] || { icon: '📝', tag: '量表' }
          return (
            <Link
              key={s.scale_id}
              to={`/assess/${s.scale_id}`}
              className="group bg-white rounded-2xl border border-slate-200 p-5 hover:border-[#1e3a5f] hover:shadow-md transition flex flex-col"
            >
              <div className="flex items-start justify-between mb-3">
                <div className="w-11 h-11 rounded-xl bg-slate-50 group-hover:bg-blue-50 flex items-center justify-center text-2xl transition">
                  {meta.icon}
                </div>
                <span className="text-xs text-slate-400">{s.item_count} 题</span>
              </div>
              <div className="flex items-center gap-2 mb-1">
                <span className="font-semibold text-slate-800">{s.scale_name}</span>
              </div>
              <span className="inline-block text-[11px] text-[#1e3a5f] bg-blue-50 px-2 py-0.5 rounded-full w-fit mb-2">
                {meta.tag}
              </span>
              <p className="text-sm text-slate-500 leading-relaxed flex-1">{s.description}</p>
              <span className="text-sm text-[#1e3a5f] font-medium mt-3 group-hover:underline">
                开始测评 →
              </span>
            </Link>
          )
        })}
      </div>

      <FooterDisclaimer />
    </div>
  )
}
