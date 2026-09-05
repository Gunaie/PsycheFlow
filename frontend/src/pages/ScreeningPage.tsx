import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom'
import { apiGet, apiPost } from '../api'
import CrisisBanner from '../components/CrisisBanner'
import FooterDisclaimer from '../components/FooterDisclaimer'

interface ScaleMeta {
  scale_id: string
  scale_name: string
  description: string
  items: { id: number; text: string }[]
  options: Record<string, string>
}

interface ScreeningInfo {
  batch_name: string
  scale_ids: string[]
  status: string
}

interface ScoreResult {
  scale_id: string
  scale_name: string
  total_score: number
  severity: string
  crisis_level: string
  crisis_triggers: string[]
  interpretation: string
  needs_crisis_escalation: boolean
}

export default function ScreeningPage() {
  const [code, setCode] = useState('')
  const [info, setInfo] = useState<ScreeningInfo | null>(null)
  const [metas, setMetas] = useState<Record<string, ScaleMeta>>({})
  const [answers, setAnswers] = useState<Record<string, Record<number, number>>>({})
  const [results, setResults] = useState<ScoreResult[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const enterCode = async () => {
    const c = code.trim().toUpperCase()
    if (!c || loading) return
    setLoading(true)
    setError(null)
    try {
      const res = await apiGet<ScreeningInfo>(`/api/screening/${c}`)
      setInfo(res)
      // 已完成的批次直接展示完成态
      if (res.status !== 'completed') {
        const ms = await Promise.all(res.scale_ids.map((sid) => apiGet<ScaleMeta>(`/api/scales/${sid}`)))
        const map: Record<string, ScaleMeta> = {}
        const ans: Record<string, Record<number, number>> = {}
        for (const m of ms) { map[m.scale_id] = m; ans[m.scale_id] = {} }
        setMetas(map)
        setAnswers(ans)
      }
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const allAnswered = useMemo(() => {
    if (!info) return false
    for (const sid of info.scale_ids) {
      const m = metas[sid]
      if (!m) return false
      if (Object.keys(answers[sid] || {}).length !== m.items.length) return false
    }
    return true
  }, [info, metas, answers])

  const submit = async () => {
    if (!info || !allAnswered || loading) return
    setLoading(true)
    setError(null)
    try {
      const res = await apiPost<{ results: ScoreResult[]; has_crisis: boolean }>(
        `/api/screening/${code.trim().toUpperCase()}/submit`,
        { answers },
      )
      setResults(res.results)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const hasCrisis = !!results && results.some((r) => r.needs_crisis_escalation)
  const totalItems = Object.values(metas).reduce((s, m) => s + m.items.length, 0)

  // ======== 完成态 ========
  if (results) {
    return (
      <div className="space-y-5">
        <h1 className="text-xl font-bold text-slate-800">测评已完成</h1>
        <p className="text-sm text-slate-500">
          感谢你的作答。结果已同步给学校心理老师；如需进一步了解请前往 <Link to="/chat" className="text-primary-600 hover:underline">AI 心理陪伴对话</Link>。
        </p>
        {hasCrisis && <CrisisBanner />}
        {results.map((r) => (
          <div key={r.scale_id} className="bg-white rounded-xl border border-slate-200 p-5 space-y-3">
            <h3 className="text-sm font-semibold text-slate-800">{r.scale_name}</h3>
            <div className="flex justify-between items-center">
              <span className="text-sm text-slate-500">总分</span>
              <span className="text-2xl font-bold text-slate-800">{r.total_score}</span>
            </div>
            <p className="text-sm text-slate-600">{r.interpretation}</p>
            {r.crisis_triggers.length > 0 && (
              <div className="text-xs text-red-600 bg-red-50 rounded p-2">
                触发项：{r.crisis_triggers.join('；')}
              </div>
            )}
          </div>
        ))}
        <FooterDisclaimer />
      </div>
    )
  }

  // ======== 未输码：输入筛查码 ========
  if (!info) {
    return (
      <div className="max-w-md mx-auto space-y-5">
        <div className="text-center pt-6">
          <h1 className="text-xl font-bold text-slate-800">批量心理筛查</h1>
          <p className="text-sm text-slate-500 mt-2">
            请输入学校心理老师发放的 6 位筛查码。作答匿名，结果仅同步给你的心理老师。
          </p>
        </div>
        <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm space-y-4">
          <input
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            onKeyDown={(e) => { if (e.key === 'Enter') enterCode() }}
            maxLength={6}
            placeholder="如：A3K9M2"
            className="w-full rounded-lg border border-slate-300 px-4 py-3 text-center text-2xl font-mono font-bold tracking-[0.4em] text-[#1e3a5f] focus:outline-none focus:border-[#1e3a5f]"
          />
          <button
            onClick={enterCode}
            disabled={!code.trim() || loading}
            className={`w-full py-2.5 rounded-xl font-semibold text-sm transition ${
              code.trim() && !loading
                ? 'bg-[#1e3a5f] text-white hover:opacity-90'
                : 'bg-slate-200 text-slate-400 cursor-not-allowed'
            }`}
          >
            {loading ? '验证中…' : '进入测评'}
          </button>
          {error && <div className="bg-red-50 text-red-600 text-sm p-3 rounded-lg">{error}</div>}
        </div>
        <FooterDisclaimer />
      </div>
    )
  }

  // ======== 已完成过（再次输码）========
  if (info.status === 'completed') {
    return (
      <div className="max-w-md mx-auto text-center space-y-4 pt-10">
        <h1 className="text-xl font-bold text-slate-800">该筛查码已完成测评</h1>
        <p className="text-sm text-slate-500">每位学生的筛查码仅可作答一次。如有疑问请联系学校心理老师。</p>
        <FooterDisclaimer />
      </div>
    )
  }

  // ======== 答题态 ========
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-bold text-slate-800">{info.batch_name}</h1>
        <p className="text-sm text-slate-500 mt-1">
          共 {totalItems} 题 · {info.scale_ids.map((s) => metas[s]?.scale_name).filter(Boolean).join('、')}
        </p>
      </div>

      {info.scale_ids.map((sid) => {
        const m = metas[sid]
        if (!m) return null
        const localOptionKeys = Object.keys(m.options).sort((a, b) => Number(a) - Number(b))
        return (
          <div key={sid} className="space-y-3">
            <h2 className="text-lg font-bold text-slate-800 mt-2">{m.scale_name}</h2>
            {m.description && <p className="text-sm text-slate-500">{m.description}</p>}
            {m.items.map((item, idx) => (
              <div key={`${sid}-q${item.id}`} className="bg-white rounded-xl border border-slate-200 p-4">
                <div className="text-sm text-slate-700">
                  <span className="text-slate-400 mr-2">{idx + 1}.</span>
                  {item.text}
                </div>
                <div className="flex flex-wrap gap-2 mt-3">
                  {localOptionKeys.map((k) => {
                    const v = Number(k)
                    const active = (answers[sid] || {})[item.id] === v
                    return (
                      <button
                        key={`${sid}-${item.id}-${k}`}
                        type="button"
                        disabled={loading}
                        onClick={() =>
                          setAnswers((a) => ({ ...a, [sid]: { ...(a[sid] || {}), [item.id]: v } }))
                        }
                        className={`px-3 py-1.5 rounded-lg text-xs border transition ${
                          active
                            ? 'bg-primary-500 text-white border-primary-500'
                            : 'bg-white text-slate-600 border-slate-300 hover:border-primary-400'
                        }`}
                      >
                        {m.options[k]}
                      </button>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
        )
      })}

      {error && <div className="bg-red-50 text-red-600 text-sm p-3 rounded-lg">{error}</div>}

      <button
        onClick={submit}
        disabled={!allAnswered || loading}
        className={`w-full py-3 rounded-xl font-bold text-sm transition ${
          allAnswered && !loading
            ? 'bg-[#1e3a5f] text-white hover:opacity-90'
            : 'bg-slate-200 text-slate-400 cursor-not-allowed'
        }`}
      >
        {loading ? '提交中…' : `提交测评（${Object.values(answers).reduce((s, a) => s + Object.keys(a).length, 0)}/${totalItems} 题）`}
      </button>

      <FooterDisclaimer />
    </div>
  )
}
