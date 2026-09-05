import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiGet, apiGetBlob } from '../api'
import FooterDisclaimer from '../components/FooterDisclaimer'

interface Assessment {
  scale_id: string
  scale_name: string
  score: number
  max_score: number
  severity: string
  interpretation?: string
  crisis_triggers?: string[]
  crisis_level?: string
  answers?: Record<number, number>
}

interface SessionItem {
  session_id: string
  created_at: string
  label: string
  has_crisis: boolean
  assessments: Assessment[]
}

interface SessionsResponse {
  items: SessionItem[]
  next_cursor: string | null
}

const SEV_COLOR: Record<string, string> = {
  none: 'bg-slate-100 text-slate-600 border-slate-200',
  mild: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  moderate: 'bg-amber-50 text-amber-700 border-amber-200',
  severe: 'bg-red-50 text-red-700 border-red-200',
}

const SEV_DOT: Record<string, string> = {
  none: 'bg-slate-400',
  mild: 'bg-emerald-500',
  moderate: 'bg-amber-500',
  severe: 'bg-red-500',
}

const SEV_CN: Record<string, string> = {
  none: '无',
  mild: '轻度',
  moderate: '中度',
  severe: '重度',
}

function formatDate(iso: string): string {
  try {
    // 后端 created_at 用 datetime.utcnow 存储但 isoformat() 不带时区标记，
    // 浏览器 new Date() 会把无时区字符串当本地时间，导致显示比真实本地时间差 8 小时。
    // 这里显式按 UTC 解析（无时区标记则补 'Z'），由 Date 自动转本地时区显示。
    const normalized = /Z$|[+-]\d{2}:\d{2}$/.test(iso) ? iso : iso + 'Z'
    const d = new Date(normalized)
    const pad = (n: number) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
  } catch {
    return iso
  }
}

function formatTime(iso: string): string {
  try {
    const normalized = /Z$|[+-]\d{2}:\d{2}$/.test(iso) ? iso : iso + 'Z'
    const d = new Date(normalized)
    const pad = (n: number) => String(n).padStart(2, '0')
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`
  } catch {
    return ''
  }
}

export default function HistoryPage() {
  const navigate = useNavigate()
  const [items, setItems] = useState<SessionItem[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [detailItem, setDetailItem] = useState<SessionItem | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [showAnswers, setShowAnswers] = useState<Record<string, boolean>>({})
  const [downloadingId, setDownloadingId] = useState<string | null>(null)

  const fetchSessions = async (cursor?: string) => {
    try {
      const params = new URLSearchParams({ page_size: '20' })
      if (cursor) params.set('cursor', cursor)
      const res = await apiGet<SessionsResponse>(`/api/sessions?${params.toString()}`)
      if (cursor) {
        setItems((prev) => [...prev, ...res.items])
      } else {
        setItems(res.items)
      }
      setNextCursor(res.next_cursor)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  useEffect(() => {
    setLoading(true)
    fetchSessions().finally(() => setLoading(false))
  }, [])

  const loadMore = (cursor: string) => {
    setLoadingMore(true)
    fetchSessions(cursor).finally(() => setLoadingMore(false))
  }

  const downloadReport = async (sessionId: string) => {
    setDownloadingId(sessionId)
    try {
      const blob = await apiGetBlob(`/api/sessions/${sessionId}/report`)
      // 通过 <a download> 触发真实下载，不新开标签页（blob 新标签页会被浏览器内置 PDF 查看器打开成"预览"）
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `psycheflow-report-${sessionId}.pdf`
      document.body.appendChild(a)
      a.click()
      a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 60000)
    } catch (e) {
      alert(`下载失败：${(e as Error).message}`)
    } finally {
      setDownloadingId(null)
    }
  }

  // 查看详情：列表精简数据先快速弹窗，异步拉取详情接口补全 interpretation/answers/crisis 等字段
  const viewDetail = async (item: SessionItem) => {
    setDetailItem(item)
    setDetailLoading(true)
    try {
      const full = await apiGet<SessionItem>(`/api/sessions/${item.session_id}`)
      setDetailItem(full)
    } catch {
      // 失败时保留列表精简数据，不阻断查看
    } finally {
      setDetailLoading(false)
    }
  }
  const close = () => { setDetailItem(null); setShowAnswers({}) }

  return (
    <div className="space-y-6">
      {/* 页面标题 */}
      <div className="flex items-end justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-bold text-slate-800">历史报告</h1>
          <p className="text-sm text-slate-500 mt-1">
            {loading ? '加载中…' : items.length > 0 ? `共 ${items.length} 份测评记录` : '查看您的历次测评记录与报告'}
          </p>
        </div>
      </div>

      {/* 内容区 */}
      {loading ? (
        <div className="flex items-center justify-center py-20 text-slate-400">
          <span className="text-sm">加载中...</span>
        </div>
      ) : error ? (
        <div className="bg-red-50 text-red-600 text-sm p-4 rounded-xl">加载失败：{error}</div>
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 text-center">
          <div className="w-20 h-20 rounded-full bg-slate-100 flex items-center justify-center text-4xl mb-5">
            📋
          </div>
          <p className="text-slate-700 text-lg font-semibold mb-2">暂无测评记录</p>
          <p className="text-slate-500 text-sm mb-6">完成量表测评后，您的报告将出现在这里</p>
          <button
            onClick={() => navigate('/assess')}
            className="rounded-lg bg-[#1e3a5f] px-5 py-2.5 text-sm text-white font-medium hover:opacity-95 transition"
          >
            前往测评 →
          </button>
        </div>
      ) : (
        <>
          <div className="space-y-3">
            {items.map((item) => {
              const date = formatDate(item.created_at)
              const time = formatTime(item.created_at)
              return (
                <div
                  key={item.session_id}
                  className={`rounded-2xl border bg-white p-5 transition hover:shadow-md ${
                    item.has_crisis ? 'border-red-300' : 'border-slate-200'
                  }`}
                >
                  {/* 顶部信息行 */}
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2 text-sm">
                      <span className="font-semibold text-slate-700">{date}</span>
                      <span className="text-slate-300">|</span>
                      <span className="text-slate-500">{time}</span>
                      {item.label && (
                        <>
                          <span className="text-slate-300">|</span>
                          <span className="text-slate-500">{item.label}</span>
                        </>
                      )}
                    </div>
                    {item.has_crisis && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-red-50 border border-red-200 px-2.5 py-0.5 text-xs font-semibold text-red-600">
                        ⚠ 危机 · 12355
                      </span>
                    )}
                  </div>

                  {/* 量表结果 badges */}
                  <div className="flex flex-wrap gap-2 mb-4">
                    {item.assessments.map((a) => (
                      <span
                        key={a.scale_id}
                        className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1 text-xs font-medium ${
                          SEV_COLOR[a.severity] || SEV_COLOR.none
                        }`}
                      >
                        <span className={`w-1.5 h-1.5 rounded-full ${SEV_DOT[a.severity] || SEV_DOT.none}`} />
                        {a.scale_name || a.scale_id}
                        <span className="text-slate-400">·</span>
                        {a.score}/{a.max_score}
                        <span className="text-slate-400">·</span>
                        {SEV_CN[a.severity] || a.severity}
                      </span>
                    ))}
                  </div>

                  {/* 操作按钮 */}
                  <div className="flex gap-2">
                    <button
                      onClick={() => downloadReport(item.session_id)}
                      disabled={downloadingId === item.session_id}
                      className="flex-1 rounded-lg bg-[#1e3a5f] px-3 py-2 text-sm text-white font-medium hover:opacity-95 transition flex items-center justify-center gap-1.5 disabled:opacity-60"
                    >
                      <span>⬇</span> {downloadingId === item.session_id ? '生成中…' : '下载 PDF'}
                    </button>
                    <button
                      onClick={() => viewDetail(item)}
                      className="flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 font-medium hover:bg-slate-50 transition flex items-center justify-center gap-1.5"
                    >
                      <span>📄</span> 查看详情
                    </button>
                  </div>
                </div>
              )
            })}
          </div>

          {/* 加载更多 */}
          {nextCursor && (
            <div className="text-center pt-2">
              <button
                onClick={() => loadMore(nextCursor!)}
                disabled={loadingMore}
                className="rounded-lg border border-[#1e3a5f] px-6 py-2 text-sm text-[#1e3a5f] font-medium hover:bg-blue-50 disabled:opacity-50 transition"
              >
                {loadingMore ? '加载中...' : '加载更多 ↓'}
              </button>
            </div>
          )}
        </>
      )}

      <FooterDisclaimer />

      {/* 详情 Modal */}
      {detailItem && (
        <div
          className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4"
          onClick={close}
        >
          <div
            className="bg-white rounded-2xl max-w-2xl w-full max-h-[85vh] overflow-y-auto p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex justify-between items-start mb-5">
              <div>
                <h2 className="text-lg font-bold text-slate-800">
                  {detailItem.label || '匿名测评'}
                </h2>
                <p className="text-sm text-slate-500 mt-1">
                  {formatDate(detailItem.created_at)} {formatTime(detailItem.created_at)}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {detailItem.has_crisis && (
                  <span className="rounded-full bg-red-50 border border-red-200 px-2.5 py-0.5 text-xs font-semibold text-red-600">
                    ⚠ 危机 · 12355
                  </span>
                )}
                <button
                  onClick={close}
                  className="text-slate-400 hover:text-slate-600 text-2xl leading-none"
                >
                  ×
                </button>
              </div>
            </div>

            <div className="space-y-4">
              {detailLoading && (
                <div className="text-center text-sm text-slate-400 py-2">
                  报告内容加载中…
                </div>
              )}
              {detailItem.assessments.map((a) => {
                const key = `answers-${detailItem.session_id}-${a.scale_id}`
                const open = showAnswers[key]
                return (
                  <div
                    key={a.scale_id}
                    className="rounded-xl border border-slate-200 p-4 bg-slate-50/60"
                  >
                    <div className="flex justify-between items-center mb-2">
                      <h3 className="font-semibold text-slate-800">
                        {a.scale_name || a.scale_id}
                      </h3>
                      <span
                        className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-0.5 text-xs font-medium ${
                          SEV_COLOR[a.severity] || SEV_COLOR.none
                        }`}
                      >
                        <span className={`w-1.5 h-1.5 rounded-full ${SEV_DOT[a.severity] || SEV_DOT.none}`} />
                        {a.score}/{a.max_score} · {SEV_CN[a.severity] || a.severity}
                      </span>
                    </div>

                    {a.interpretation && (
                      <p className="text-sm text-slate-600 mt-2 mb-2 leading-relaxed">
                        {a.interpretation}
                      </p>
                    )}

                    {a.crisis_triggers && a.crisis_triggers.length > 0 && (
                      <div className="text-xs text-red-600 bg-red-50 rounded-lg p-2.5 mt-2">
                        危机触发词：{a.crisis_triggers.join('；')}
                      </div>
                    )}

                    {a.crisis_level && (
                      <div className="text-xs mt-1.5 text-slate-600">
                        危机等级：<span className="font-medium">{a.crisis_level}</span>
                      </div>
                    )}

                    {a.answers && Object.keys(a.answers).length > 0 && (
                      <div className="mt-3">
                        <button
                          onClick={() => setShowAnswers((s) => ({ ...s, [key]: !open }))}
                          className="text-xs text-[#1e3a5f] hover:underline font-medium"
                        >
                          {open ? '▼ 收起作答详情' : '▶ 展开作答详情'}
                        </button>
                        {open && (
                          <div className="mt-2 text-xs text-slate-600 bg-white rounded-lg p-3 border border-slate-200 grid grid-cols-2 gap-x-4 gap-y-1">
                            {Object.entries(a.answers)
                              .sort(([x], [y]) => Number(x) - Number(y))
                              .map(([qid, val]) => (
                                <div key={qid}>
                                  第 {qid} 题：<span className="font-medium">{val}</span>
                                </div>
                              ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>

            <div className="mt-5 pt-4 border-t border-slate-200">
              <button
                onClick={() => downloadReport(detailItem.session_id)}
                disabled={downloadingId === detailItem.session_id}
                className="w-full rounded-lg bg-[#1e3a5f] px-4 py-2.5 text-sm text-white font-medium hover:opacity-95 transition disabled:opacity-60"
              >
                {downloadingId === detailItem.session_id ? '报告生成中…' : '⬇ 下载 PDF 报告'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
