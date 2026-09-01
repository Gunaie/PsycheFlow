import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { apiGet, apiPost, apiPostBlob } from '../api'
import CrisisBanner from '../components/CrisisBanner'
import FooterDisclaimer from '../components/FooterDisclaimer'

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

const COMBINED_SCALES = ['phq_a', 'scared'] as const
type CombinedAnswers = Record<string, Record<number, number>> // { phqa: {1:3,...}, scared: {1:3,...} }

export default function ScalePage() {
  const { scaleId } = useParams<{ scaleId: string }>()
  const isCombined = !scaleId

  // ======== 单量表状态（/scales/:scaleId） ========
  const [meta, setMeta] = useState<ScaleMeta | null>(null)
  const [answers, setAnswers] = useState<Record<number, number>>({})
  const [result, setResult] = useState<ScoreResult | null>(null)

  // ======== 合并双量表状态（/scale） ========
  const [combinedMetas, setCombinedMetas] = useState<Record<string, ScaleMeta>>({})
  const [combinedAnswers, setCombinedAnswers] = useState<CombinedAnswers>({})
  const [combinedResults, setCombinedResults] = useState<Record<string, ScoreResult>>({})

  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [reportLoading, setReportLoading] = useState(false)

  // ========== 单量表 useEffect ==========
  useEffect(() => {
    if (isCombined) return
    setMeta(null); setResult(null); setAnswers({}); setError(null)
    apiGet<ScaleMeta>(`/api/scales/${scaleId}`)
      .then(setMeta)
      .catch((e: Error) => setError(e.message))
  }, [isCombined, scaleId])

  // ========== 合并双量表 useEffect ==========
  useEffect(() => {
    if (!isCombined) return
    setCombinedMetas({}); setCombinedAnswers({}); setCombinedResults({}); setError(null)
    Promise.all(COMBINED_SCALES.map(sid => apiGet<ScaleMeta>(`/api/scales/${sid}`)))
      .then(metas => {
        const map: Record<string, ScaleMeta> = {}
        const ans: CombinedAnswers = {}
        for (const m of metas) { map[m.scale_id] = m; ans[m.scale_id] = {} }
        setCombinedMetas(map)
        setCombinedAnswers(ans)
      })
      .catch((e: Error) => setError(e.message))
  }, [isCombined])

  // ========== 公共：optionKeys ==========
  const optionKeys = useMemo(() => {
    const sample = meta || Object.values(combinedMetas)[0]
    if (!sample) return []
    return Object.keys(sample.options).sort((a, b) => Number(a) - Number(b))
  }, [meta, combinedMetas])

  // ========== 公共：全部题目都回答了？ ==========
  const allAnswered = useMemo(() => {
    if (isCombined) {
      if (Object.keys(combinedMetas).length === 0) return false
      for (const sid of Object.keys(combinedMetas)) {
        const m = combinedMetas[sid]
        const ans = combinedAnswers[sid] || {}
        if (Object.keys(ans).length !== m.items.length) return false
      }
      return true
    } else {
      return meta ? Object.keys(answers).length === meta.items.length : false
    }
  }, [isCombined, meta, answers, combinedMetas, combinedAnswers])

  const combinedCrisis = Object.values(combinedResults).some(r => r.needs_crisis_escalation)
  const hasCrisis = (result?.needs_crisis_escalation || combinedCrisis)

  // ========== 单量表 submit ==========
  const submit = async () => {
    if (isCombined || !scaleId || !allAnswered) return
    setLoading(true); setError(null); setResult(null)
    try {
      const res = await apiPost<ScoreResult>(`/api/scales/${scaleId}/score`, { answers })
      setResult(res)
    } catch (e) { setError((e as Error).message) }
    finally { setLoading(false) }
  }

  // ========== 单量表 generateReport ==========
  const generateSingleReport = async () => {
    if (isCombined || !scaleId || !allAnswered) return
    setReportLoading(true); setError(null)
    const win = window.open('', '_blank')
    if (!win) { setError('浏览器拦截了新窗口，请允许本站弹窗后重试'); setReportLoading(false); return }
    win.document.write('<p style="font-family:sans-serif;text-align:center;margin-top:40vh">报告生成中…</p>')
    try {
      const session = await apiPost<{ session_id: string }>('/api/sessions', { label: `${scaleId} 测评` })
      const sid = session.session_id
      localStorage.setItem('psycheflow_active_session_id', sid)
      await apiPost(`/api/sessions/${sid}/assessments`, { scale_id: scaleId, answers })
      const blob = await apiPostBlob(`/api/sessions/${sid}/report`, { session_id: sid })
      const url = URL.createObjectURL(blob)
      win.location.href = url
      setTimeout(() => URL.revokeObjectURL(url), 60000)
    } catch (e) { win.close(); setError((e as Error).message) }
    finally { setReportLoading(false) }
  }

  // ========== 合并双量表：一步「生成报告」（submit 双量表 + assessment 双次 + report） ==========
  const generateCombinedReport = async () => {
    if (!isCombined || !allAnswered) return
    setReportLoading(true); setError(null)
    // 同步打开空窗保留用户手势
    const win = window.open('', '_blank')
    if (!win) { setError('浏览器拦截了新窗口，请允许本站弹窗后重试'); setReportLoading(false); return }
    win.document.write('<p style="font-family:sans-serif;text-align:center;margin-top:40vh">报告生成中…双量表计分+渲染约需30-60秒</p>')
    try {
      // Step 1: 先并行计分，若失败可直接报错
      const scoreJobs = COMBINED_SCALES.map(sid =>
        apiPost<ScoreResult>(`/api/scales/${sid}/score`, { answers: combinedAnswers[sid] || {} })
      )
      const scores = await Promise.all(scoreJobs)
      const resMap: Record<string, ScoreResult> = {}
      for (const s of scores) resMap[s.scale_id] = s
      setCombinedResults(resMap)

      // Step 2: 创建会话 + 双 assessments
      const session = await apiPost<{ session_id: string }>('/api/sessions', { label: 'PHQ-A + SCARED 合并测评' })
      const sid = session.session_id
      localStorage.setItem('psycheflow_active_session_id', sid)
      for (const s of COMBINED_SCALES) {
        await apiPost(`/api/sessions/${sid}/assessments`, { scale_id: s, answers: combinedAnswers[s] || {} })
      }

      // Step 3: 生成 PDF
      const blob = await apiPostBlob(`/api/sessions/${sid}/report`, { session_id: sid })
      const url = URL.createObjectURL(blob)
      win.location.href = url
      setTimeout(() => URL.revokeObjectURL(url), 60000)
    } catch (e) { win.close(); setError((e as Error).message) }
    finally { setReportLoading(false) }
  }

  // ========== 错误/加载 UI ==========
  if (error && ((!isCombined && !meta) || (isCombined && Object.keys(combinedMetas).length === 0))) {
    return <div className="bg-red-50 text-red-600 text-sm p-3 rounded-lg">加载失败：{error}</div>
  }
  if ((!isCombined && !meta) || (isCombined && Object.keys(combinedMetas).length === 0)) {
    return <div className="text-sm text-slate-400">加载中…</div>
  }

  // ========== 量表题目渲染 Block ==========
  const renderScaleItems = (m: ScaleMeta, ans: Record<number, number>, onChange: (qid: number, v: number) => void) => {
    const disabled = isCombined ? reportLoading : !!result
    return (
      <div key={m.scale_id} className="space-y-3">
        <h2 className="text-lg font-bold text-slate-800 mt-2">{m.scale_name}</h2>
        {m.description && <p className="text-sm text-slate-500">{m.description}</p>}
        {m.items.map((item, idx) => (
          <div key={`${m.scale_id}-q${item.id}`} className="bg-white rounded-xl border border-slate-200 p-4">
            <div className="text-sm text-slate-700">
              <span className="text-slate-400 mr-2">{idx + 1}.</span>
              {item.text}
            </div>
            <div className="flex flex-wrap gap-2 mt-3">
              {optionKeys.map((k) => {
                const v = Number(k)
                const active = ans[item.id] === v
                return (
                  <button
                    key={`${m.scale_id}-${item.id}-${k}`}
                    type="button"
                    disabled={disabled || (loading)}
                    onClick={() => onChange(item.id, v)}
                    className={`px-3 py-1.5 rounded-lg text-xs border transition ${
                      active
                        ? 'bg-primary-500 text-white border-primary-500'
                        : 'bg-white text-slate-600 border-slate-300 hover:border-primary-400'
                    } ${(disabled || loading) ? 'opacity-60 cursor-not-allowed' : ''}`}
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
  }

  // ========== 结果渲染 Block ==========
  const renderResultCard = (r: ScoreResult) => (
    <div key={r.scale_id} className="bg-white rounded-xl border border-slate-200 p-5 space-y-3">
      <h3 className="text-sm font-semibold text-slate-800">{r.scale_name} · 结果</h3>
      <div className="flex justify-between items-center">
        <span className="text-sm text-slate-500">总分</span>
        <span className="text-2xl font-bold text-slate-800">{r.total_score}</span>
      </div>
      <div className={`text-sm rounded-lg px-3 py-2 ${SEVERITY_STYLE[r.severity] || 'bg-slate-50 text-slate-600'}`}>
        严重度：{SEVERITY_LABEL[r.severity] || r.severity}
      </div>
      <p className="text-sm text-slate-600">{r.interpretation}</p>
      {r.crisis_triggers.length > 0 && (
        <div className="text-xs text-red-600 bg-red-50 rounded p-2">
          触发项：{r.crisis_triggers.join('；')}
        </div>
      )}
    </div>
  )

  return (
    <div className="space-y-5">
      {!isCombined && meta && (
        <div>
          <h1 className="text-xl font-bold text-slate-800">{meta.scale_name}</h1>
          <p className="text-sm text-slate-500 mt-1">{meta.description}</p>
        </div>
      )}
      {isCombined && (
        <div>
          <h1 className="text-xl font-bold text-slate-800">PHQ-A + SCARED 合并心理筛查测评</h1>
          <p className="text-sm text-slate-500 mt-1">
            共 {Object.values(combinedMetas).reduce((s, m) => s + m.items.length, 0)} 题：
            PHQ-A {combinedMetas['phq_a']?.items.length ?? 0} 题（抑郁）·
            SCARED {combinedMetas['scared']?.items.length ?? 0} 题（焦虑）
          </p>
        </div>
      )}

      {!isCombined && meta && renderScaleItems(
        meta, answers, (qid, v) => setAnswers(a => ({ ...a, [qid]: v }))
      )}
      {isCombined && COMBINED_SCALES.map(sid => {
        const m = combinedMetas[sid]
        const ans = combinedAnswers[sid] || {}
        if (!m) return null
        return renderScaleItems(m, ans, (qid, v) =>
          setCombinedAnswers(ca => ({ ...ca, [sid]: { ...(ca[sid] || {}), [qid]: v } }))
        )
      })}

      {hasCrisis && <CrisisBanner />}

      {!isCombined && result && renderResultCard(result)}
      {isCombined && Object.keys(combinedResults).length > 0 && COMBINED_SCALES.map(sid =>
        combinedResults[sid] && renderResultCard(combinedResults[sid])
      )}

      {error && <div className="bg-red-50 text-red-600 text-sm p-3 rounded-lg">{error}</div>}

      {!isCombined ? (
        <>
          {result && (
            <button
              onClick={generateSingleReport}
              disabled={reportLoading}
              className="w-full py-2 rounded-lg text-sm font-semibold border border-primary-500 text-primary-600 hover:bg-primary-50 disabled:opacity-50 transition"
            >
              {reportLoading ? '生成中…' : '生成 PDF 报告'}
            </button>
          )}
          <button
            onClick={submit}
            disabled={!allAnswered || loading}
            className={`w-full py-2.5 rounded-xl font-semibold text-sm transition ${
              allAnswered ? 'bg-primary-500 text-white hover:bg-primary-600' : 'bg-slate-200 text-slate-400 cursor-not-allowed'
            }`}
          >
            {loading ? '提交中…' : result ? '重新评估' : '提交评估'}
          </button>
        </>
      ) : (
        <button
          onClick={generateCombinedReport}
          disabled={!allAnswered || reportLoading}
          className={`w-full py-3 rounded-xl font-bold text-sm transition ${
            allAnswered && !reportLoading
              ? 'bg-[#1e3a5f] text-white hover:opacity-90'
              : 'bg-slate-200 text-slate-400 cursor-not-allowed'
          }`}
        >
          {reportLoading ? '报告生成中…（约 30-60 秒，勿关闭）' : '生成报告'}
        </button>
      )}

      <FooterDisclaimer />
    </div>
  )
}
