import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiGet } from '../api'

interface ScaleSummary {
  scale_id: string
  scale_name: string
  description: string
  item_count: number
}

export default function HomePage() {
  const [scales, setScales] = useState<ScaleSummary[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiGet<ScaleSummary[]>('/api/scales')
      .then(setScales)
      .catch((e: Error) => setError(e.message))
  }, [])

  return (
    <div className="space-y-6">
      <section>
        <h1 className="text-2xl font-bold text-slate-800">智能心理评估系统</h1>
        <p className="text-sm text-slate-500 mt-1">青少年校园心理筛查 · 量表评估与开放对话</p>
      </section>

      {error && (
        <div className="bg-red-50 text-red-600 text-sm p-3 rounded-lg">加载失败：{error}</div>
      )}

      <section>
        <h2 className="text-base font-semibold text-slate-700 mb-3">标准化量表</h2>
        <div className="grid gap-3">
          {scales.map((s) => (
            <Link
              key={s.scale_id}
              to={`/scales/${s.scale_id}`}
              className="block bg-white rounded-xl shadow-sm border border-slate-200 p-4 hover:border-primary-500 hover:shadow transition"
            >
              <div className="flex justify-between items-start">
                <div>
                  <div className="font-semibold text-slate-800">{s.scale_name}</div>
                  <div className="text-sm text-slate-500 mt-1">{s.description}</div>
                </div>
                <span className="text-xs text-slate-400 shrink-0 ml-3">{s.item_count} 题</span>
              </div>
            </Link>
          ))}
        </div>
      </section>

      <section>
        <Link
          to="/chat"
          className="block bg-primary-500 text-white rounded-xl shadow-sm p-4 hover:bg-primary-600 transition"
        >
          <div className="font-semibold">开放对话</div>
          <div className="text-sm text-primary-50 mt-1">和 PsycheFlow 陪伴助手聊聊近况</div>
        </Link>
      </section>
    </div>
  )
}
