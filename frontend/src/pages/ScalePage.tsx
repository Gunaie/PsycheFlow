import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiGet, apiPost, apiPostBlob, getToken } from '../api'
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
  const [reportGenerated, setReportGenerated] = useState(false)

  // 测评开始时即创建 session，使 session.created_at 记录开始时间（用于报告计算测评用时）
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [combinedSessionId, setCombinedSessionId] = useState<string | null>(null)

  // ========== 单量表 useEffect ==========
  useEffect(() => {
    if (isCombined) return
    setMeta(null); setResult(null); setAnswers({}); setError(null); setReportGenerated(false); setSessionId(null)
    apiGet<ScaleMeta>(`/api/scales/${scaleId}`)
      .then(meta => {
        setMeta(meta)
        // 量表加载后即创建 session，记录测评开始时间（用于报告"测评用时"）
        apiPost<{ session_id: string }>('/api/sessions', { label: '心理测评' })
          .then(s => setSessionId(s.session_id))
          .catch(() => {})  // 失败不阻塞答题，report 生成时兜底新建
      })
      .catch((e: Error) => setError(e.message))
  }, [isCombined, scaleId])

  // ========== 合并双量表 useEffect ==========
  useEffect(() => {
    if (!isCombined) return
    setCombinedMetas({}); setCombinedAnswers({}); setCombinedResults({}); setError(null); setReportGenerated(false); setCombinedSessionId(null)
    Promise.all(COMBINED_SCALES.map(sid => apiGet<ScaleMeta>(`/api/scales/${sid}`)))
      .then(metas => {
        const map: Record<string, ScaleMeta> = {}
        const ans: CombinedAnswers = {}
        for (const m of metas) { map[m.scale_id] = m; ans[m.scale_id] = {} }
        setCombinedMetas(map)
        setCombinedAnswers(ans)
        // 量表加载后即创建 session，记录测评开始时间（用于报告"测评用时"）
        apiPost<{ session_id: string }>('/api/sessions', { label: '心理测评' })
          .then(s => setCombinedSessionId(s.session_id))
          .catch(() => {})
      })
      .catch((e: Error) => setError(e.message))
  }, [isCombined])

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

  // ========== 公共：答题进度 ==========
  const totalItems = isCombined
    ? Object.values(combinedMetas).reduce((s, m) => s + m.items.length, 0)
    : meta?.items.length ?? 0
  const answeredItems = isCombined
    ? Object.values(combinedAnswers).reduce((s, ans) => s + Object.keys(ans).length, 0)
    : Object.keys(answers).length
  const progressPct = totalItems > 0 ? Math.round((answeredItems / totalItems) * 100) : 0

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
  // 一量表一报告。使用开始答题时创建的 session（sessionId），使报告能正确计算测评用时。
  // 若 sessionId 为 null（创建失败），兜底新建（此时用时可能为 0，属降级场景）
  const generateSingleReport = async () => {
    if (isCombined || !scaleId || !allAnswered) return
    setReportLoading(true); setError(null)
    try {
      let sid = sessionId
      if (!sid) {
        const s = await apiPost<{ session_id: string }>('/api/sessions', { label: '心理测评' })
        sid = s.session_id
      }
      await apiPost(`/api/sessions/${sid}/assessments`, { scale_id: scaleId, answers })
      // 新标签页预览 PDF（先同步开空标签避开弹窗拦截，原页面定格不跳转）
      const win = window.open('', '_blank')
      if (!win) { setError('浏览器拦截了新窗口，请允许本站弹窗后重试'); return }
      win.document.write('<p style="font-family:sans-serif;text-align:center;margin-top:40vh">报告生成中…</p>')
      const blob = await apiPostBlob(`/api/sessions/${sid}/report`, {})
      const url = URL.createObjectURL(blob)
      win.location.href = url
      setReportGenerated(true)
      setTimeout(() => URL.revokeObjectURL(url), 60000)
    } catch (e) { setError((e as Error).message) }
    finally { setReportLoading(false) }
  }

  // ========== 合并双量表 submit（仅计分并展示结果，与单量表统一） ==========
  const submitCombined = async () => {
    if (!isCombined || !allAnswered) return
    setLoading(true); setError(null); setCombinedResults({})
    try {
      const scoreJobs = COMBINED_SCALES.map(sid =>
        apiPost<ScoreResult>(`/api/scales/${sid}/score`, { answers: combinedAnswers[sid] || {} })
      )
      const scores = await Promise.all(scoreJobs)
      const resMap: Record<string, ScoreResult> = {}
      for (const s of scores) resMap[s.scale_id] = s
      setCombinedResults(resMap)
    } catch (e) { setError((e as Error).message) }
    finally { setLoading(false) }
  }

  // ========== 合并双量表 generateReport（结果已展示后，创建 assessment + 生成 PDF） ==========
  const generateCombinedReport = async () => {
    if (!isCombined || !allAnswered) return
    setReportLoading(true); setError(null)
    try {
      // 使用开始答题时创建的 session（合并双量表的合法累积：两条 assessment 挂同一 session）
      let sid = combinedSessionId
      if (!sid) {
        const s = await apiPost<{ session_id: string }>('/api/sessions', { label: '心理测评' })
        sid = s.session_id
      }
      for (const s of COMBINED_SCALES) {
        await apiPost(`/api/sessions/${sid}/assessments`, { scale_id: s, answers: combinedAnswers[s] || {} })
      }
      // 新标签页预览 PDF（先同步开空标签避开弹窗拦截，原页面定格不跳转）
      const win = window.open('', '_blank')
      if (!win) { setError('浏览器拦截了新窗口，请允许本站弹窗后重试'); return }
      win.document.write('<p style="font-family:sans-serif;text-align:center;margin-top:40vh">报告生成中…</p>')
      const blob = await apiPostBlob(`/api/sessions/${sid}/report`, {})
      const url = URL.createObjectURL(blob)
      win.location.href = url
      setReportGenerated(true)
      setTimeout(() => URL.revokeObjectURL(url), 60000)
    } catch (e) { setError((e as Error).message) }
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
  const renderScaleItems = (m: ScaleMeta, ans: Record<number, number>, onChange: (qid: number, v: number) => void, showHeader = true) => {
    const disabled = isCombined ? (reportLoading || Object.keys(combinedResults).length > 0) : !!result
    const localOptionKeys = Object.keys(m.options).sort((a, b) => Number(a) - Number(b))
    return (
      <div key={m.scale_id} className="space-y-3">
        {showHeader && (
          <>
            <h2 className="text-lg font-bold text-slate-800 mt-2">{m.scale_name}</h2>
            {m.description && <p className="text-sm text-slate-500">{m.description}</p>}
          </>
        )}
        {m.items.map((item, idx) => (
          <div key={`${m.scale_id}-q${item.id}`} className="bg-white rounded-xl border border-slate-200 p-4">
            <div className="text-sm text-slate-700">
              <span className="text-slate-400 mr-2">{idx + 1}.</span>
              {item.text}
            </div>
            <div className="flex flex-wrap gap-2 mt-3">
              {localOptionKeys.map((k) => {
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

      {/* 答题进度条 */}
      {totalItems > 0 && (
        <div className="flex items-center gap-3">
          <div className="flex-1 h-2 bg-slate-200 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-300 ${progressPct === 100 ? 'bg-green-500' : 'bg-primary-500'}`}
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <span className="text-xs text-slate-500 shrink-0">{answeredItems}/{totalItems} 题</span>
        </div>
      )}

      {!isCombined && meta && renderScaleItems(
        meta, answers, (qid, v) => setAnswers(a => ({ ...a, [qid]: v })), false
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

      {/* 统一操作区：先提交查看结果，再生成下载报告 */}
      {(() => {
        const hasResults = isCombined ? Object.keys(combinedResults).length > 0 : !!result
        const onGenerate = isCombined ? generateCombinedReport : generateSingleReport
        const onSubmit = isCombined ? submitCombined : submit
        return (
          <>
            {hasResults && (
              <>
                {!getToken() && (
                  <p className="text-xs text-slate-500 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2">
                    匿名测评不存档：报告仅当前页面可见，请及时下载 PDF 留存；登录后测评将进入「历史」记录。
                  </p>
                )}
                <button
                  onClick={onGenerate}
                  disabled={reportLoading}
                  className="w-full py-2.5 rounded-xl text-sm font-semibold border border-[#1e3a5f] text-[#1e3a5f] hover:bg-blue-50 disabled:opacity-50 transition"
                >
                  {reportLoading ? '报告生成中…（约 30-60 秒，勿关闭）' : '生成 PDF 报告'}
                </button>
              </>
            )}
            <button
              onClick={onSubmit}
              disabled={!allAnswered || loading}
              className={`w-full py-3 rounded-xl font-bold text-sm transition ${
                allAnswered ? 'bg-[#1e3a5f] text-white hover:opacity-90' : 'bg-slate-200 text-slate-400 cursor-not-allowed'
              }`}
            >
              {loading ? '提交中…' : hasResults ? '重新评估' : '提交评估'}
            </button>
          </>
        )
      })()}

      {reportGenerated && (
        <div className="bg-green-50 border border-green-200 rounded-xl p-4 space-y-3">
          <div className="flex items-center gap-2 text-green-700">
            <span className="text-lg">✓</span>
            <span className="font-semibold text-sm">报告已生成并开始下载</span>
          </div>
          <p className="text-xs text-slate-600">接下来您可以：</p>
          <div className="flex flex-wrap gap-2">
            <Link to="/chat" className="px-3 py-1.5 rounded-lg text-sm border border-primary-300 text-primary-700 bg-white hover:bg-primary-50 transition">
              💬 和AI对话讨论结果
            </Link>
            <Link to="/assess" className="px-3 py-1.5 rounded-lg text-sm border border-slate-300 text-slate-600 bg-white hover:bg-slate-50 transition">
              📋 做其他测评
            </Link>
            <Link to="/history" className="px-3 py-1.5 rounded-lg text-sm border border-slate-300 text-slate-600 bg-white hover:bg-slate-50 transition">
              📊 查看历史报告
            </Link>
          </div>
        </div>
      )}

      <FooterDisclaimer />
    </div>
  )
}
