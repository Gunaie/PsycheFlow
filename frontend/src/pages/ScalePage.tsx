import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { apiGet, apiPost, apiPostBlob } from '../api'
import CrisisBanner from '../components/CrisisBanner'

interface ScaleMeta {
  scale_id: string
  scale_name: string
  description: string
  items: { id: number; text: string }[]
  options: Record<string, string>
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

const SEVERITY_STYLE: Record<string, string> = {
  none: 'bg-green-50 text-green-700',
  mild: 'bg-yellow-50 text-yellow-700',
  moderate: 'bg-orange-50 text-orange-700',
  moderately_severe: 'bg-red-50 text-red-700',
  severe: 'bg-red-100 text-red-800',
}

const SEVERITY_LABEL: Record<string, string> = {
  none: '无明显症状',
  mild: '轻度',
  moderate: '中度',
  moderately_severe: '中重度',
  severe: '重度',
}

export default function ScalePage() {
  const { scaleId } = useParams<{ scaleId: string }>()
  const [meta, setMeta] = useState<ScaleMeta | null>(null)
  const [answers, setAnswers] = useState<Record<number, number>>({})
  const [result, setResult] = useState<ScoreResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [reportLoading, setReportLoading] = useState(false)

  useEffect(() => {
    if (!scaleId) return
    setMeta(null)
    setResult(null)
    setAnswers({})
    setError(null)
    apiGet<ScaleMeta>(`/api/scales/${scaleId}`)
      .then(setMeta)
      .catch((e: Error) => setError(e.message))
  }, [scaleId])

  const optionKeys = useMemo(() => {
    if (!meta) return []
    return Object.keys(meta.options).sort((a, b) => Number(a) - Number(b))
  }, [meta])

  const allAnswered = meta ? Object.keys(answers).length === meta.items.length : false

  const submit = async () => {
    if (!scaleId || !allAnswered) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const res = await apiPost<ScoreResult>(`/api/scales/${scaleId}/score`, { answers })
      setResult(res)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const generateReport = async () => {
    if (!scaleId || !allAnswered) return
    setReportLoading(true)
    setError(null)
    // 同步打开空窗以保留用户手势：await 之后浏览器会把 window.open 当弹窗拦截
    const win = window.open('', '_blank')
    if (!win) {
      setError('浏览器拦截了新窗口，请允许本站弹窗后重试')
      setReportLoading(false)
      return
    }
    win.document.write('<p style="font-family:sans-serif;text-align:center;margin-top:40vh">报告生成中…</p>')
    try {
      const session = await apiPost<{ session_id: string }>('/api/sessions', {})
      await apiPost(`/api/sessions/${session.session_id}/assessments`, {
        scale_id: scaleId,
        answers,
      })
      const blob = await apiPostBlob(`/api/sessions/${session.session_id}/report`, {})
      const url = URL.createObjectURL(blob)
      win.location.href = url
      setTimeout(() => URL.revokeObjectURL(url), 60000)
    } catch (e) {
      win.close()
      setError((e as Error).message)
    } finally {
      setReportLoading(false)
    }
  }

  if (error && !meta) {
    return <div className="bg-red-50 text-red-600 text-sm p-3 rounded-lg">加载失败：{error}</div>
  }
  if (!meta) return <div className="text-sm text-slate-400">加载中…</div>

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-bold text-slate-800">{meta.scale_name}</h1>
        <p className="text-sm text-slate-500 mt-1">{meta.description}</p>
      </div>

      <div className="space-y-3">
        {meta.items.map((item, idx) => (
          <div key={item.id} className="bg-white rounded-xl border border-slate-200 p-4">
            <div className="text-sm text-slate-700">
              <span className="text-slate-400 mr-2">{idx + 1}.</span>
              {item.text}
            </div>
            <div className="flex flex-wrap gap-2 mt-3">
              {optionKeys.map((k) => {
                const v = Number(k)
                const active = answers[item.id] === v
                return (
                  <button
                    key={k}
                    type="button"
                    disabled={!!result}
                    onClick={() => setAnswers((a) => ({ ...a, [item.id]: v }))}
                    className={`px-3 py-1.5 rounded-lg text-xs border transition ${
                      active
                        ? 'bg-primary-500 text-white border-primary-500'
                        : 'bg-white text-slate-600 border-slate-300 hover:border-primary-400'
                    } ${result ? 'opacity-60 cursor-not-allowed' : ''}`}
                  >
                    {meta.options[k]}
                  </button>
                )
              })}
            </div>
          </div>
        ))}
      </div>

      {result && result.needs_crisis_escalation && <CrisisBanner />}

      {result && (
        <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-3">
          <div className="flex justify-between items-center">
            <span className="text-sm text-slate-500">总分</span>
            <span className="text-2xl font-bold text-slate-800">{result.total_score}</span>
          </div>
          <div
            className={`text-sm rounded-lg px-3 py-2 ${
              SEVERITY_STYLE[result.severity] || 'bg-slate-50 text-slate-600'
            }`}
          >
            严重度：{SEVERITY_LABEL[result.severity] || result.severity}
          </div>
          <p className="text-sm text-slate-600">{result.interpretation}</p>
          {result.crisis_triggers.length > 0 && (
            <div className="text-xs text-red-600 bg-red-50 rounded p-2">
              触发项：{result.crisis_triggers.join('；')}
            </div>
          )}
          <button
            onClick={generateReport}
            disabled={reportLoading}
            className="w-full py-2 rounded-lg text-sm font-semibold border border-primary-500 text-primary-600 hover:bg-primary-50 disabled:opacity-50 transition"
          >
            {reportLoading ? '生成中…' : '生成 PDF 报告'}
          </button>
        </div>
      )}

      {error && <div className="bg-red-50 text-red-600 text-sm p-3 rounded-lg">{error}</div>}

      <button
        onClick={submit}
        disabled={!allAnswered || loading}
        className={`w-full py-2.5 rounded-xl font-semibold text-sm transition ${
          allAnswered
            ? 'bg-primary-500 text-white hover:bg-primary-600'
            : 'bg-slate-200 text-slate-400 cursor-not-allowed'
        }`}
      >
        {loading ? '提交中…' : result ? '重新评估' : '提交评估'}
      </button>
    </div>
  )
}
