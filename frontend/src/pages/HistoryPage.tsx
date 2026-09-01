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
  none: 'bg-slate-200 text-slate-700',
  mild: 'bg-emerald-100 text-emerald-800',
  moderate: 'bg-amber-100 text-amber-800',
  severe: 'bg-red-100 text-red-800',
}

const SEV_CN: Record<string, string> = {
  none: '无',
  mild: '轻度',
  moderate: '中度',
  severe: '重度',
}

function formatDateTime(iso: string): string {
  try {
    const d = new Date(iso)
    const pad = (n: number) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  } catch {
    return iso
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
  const [showAnswers, setShowAnswers] = useState<Record<string, boolean>>({})

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
    // 先同步 window.open 保留手势，避免浏览器弹窗拦截
    const win = window.open('', '_blank')
    if (!win) {
      alert('浏览器拦截了新窗口，请允许本站弹窗后重试')
      return
    }
    win.document.write('<p style="font-family:sans-serif;text-align:center;margin-top:40vh">报告生成中…</p>')
    try {
      const blob = await apiGetBlob(`/api/sessions/${sessionId}/report`)
      const url = URL.createObjectURL(blob)
      win.location.href = url
      setTimeout(() => URL.revokeObjectURL(url), 60000)
    } catch (e) {
      win.close()
      alert(`下载失败：${(e as Error).message}`)
    }
  }

  const viewDetail = (item: SessionItem) => {
    setDetailItem(item)
  }

  const close = () => {
    setDetailItem(null)
    setShowAnswers({})
  }

  return (
    <div className="min-h-screen bg-slate-50 pb-10">
      {/* 顶部 Header */}
      <header style={{ backgroundColor: '#1e3a5f' }} className="text-white">
        <div className="max-w-7xl mx-auto px-4 py-4 flex justify-between items-center">
          <h1 className="text-lg font-bold">PsycheFlow · 历史报告</h1>
          <button
            onClick={() => navigate('/')}
            className="rounded-md border border-white/30 px-3 py-1.5 text-sm hover:bg-white/10"
          >
            ← 返回首页
          </button>
        </div>
      </header>

      {/* 主体内容 */}
      <main>
        {loading ? (
          <p className="text-center text-slate-500 py-10">加载中...</p>
        ) : error ? (
          <div className="max-w-7xl mx-auto px-4 py-6">
            <div className="bg-red-50 text-red-600 text-sm p-3 rounded-lg">加载失败：{error}</div>
          </div>
        ) : items.length === 0 ? (
          <div className="mt-20 text-center">
            <div className="text-6xl mb-4">📋</div>
            <p className="text-slate-700 text-lg font-semibold mb-2">暂无测评记录</p>
            <p className="text-slate-500 mb-6">完成量表测评后，您的报告将出现在这里</p>
            <button
              onClick={() => navigate('/scale')}
              className="rounded-md bg-[#1e3a5f] px-4 py-2 text-white hover:opacity-95"
            >
              前往测评
            </button>
          </div>
        ) : (
          <>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3 max-w-7xl mx-auto px-4 py-6">
              {items.map((item) => (
                <div
                  key={item.session_id}
                  className={`rounded-lg border bg-white p-4 shadow-sm hover:shadow-md transition ${
                    item.has_crisis ? 'border-red-400 ring-1 ring-red-200' : 'border-slate-200'
                  }`}
                >
                  {/* 顶部行 */}
                  <div className="flex justify-between items-start mb-2">
                    <div>
                      <p className="text-xs text-slate-500">
                        {formatDateTime(item.created_at)} · {item.label || '匿名'}
                      </p>
                    </div>
                    {item.has_crisis && (
                      <span className="rounded bg-red-600 px-2 py-0.5 text-xs font-semibold text-white">
                        ⚠ 危机 · 12355
                      </span>
                    )}
                  </div>

                  {/* 量表行：双量表 badge 胶囊 */}
                  <div className="flex flex-wrap gap-2 mb-4">
                    {item.assessments.map((a) => (
                      <span
                        key={a.scale_id}
                        className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                          SEV_COLOR[a.severity] || SEV_COLOR.none
                        }`}
                      >
                        {a.scale_name || a.scale_id}：{a.score}/{a.max_score} ·{' '}
                        {SEV_CN[a.severity] || a.severity}
                      </span>
                    ))}
                  </div>

                  {/* 双按钮行 */}
                  <div className="flex gap-2">
                    <button
                      onClick={() => downloadReport(item.session_id)}
                      className="flex-1 rounded-md bg-[#1e3a5f] px-3 py-1.5 text-sm text-white hover:opacity-95 flex items-center justify-center gap-1"
                    >
                      <span>⬇</span> 下载 PDF
                    </button>
                    <button
                      onClick={() => viewDetail(item)}
                      className="flex-1 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 flex items-center justify-center gap-1"
                    >
                      <span>📄</span> 查看详情
                    </button>
                  </div>
                </div>
              ))}
            </div>

            {/* 下一页分页按钮 */}
            {nextCursor && (
              <div className="text-center py-4">
                <button
                  onClick={() => loadMore(nextCursor!)}
                  disabled={loadingMore}
                  className="rounded-md border border-[#1e3a5f] px-4 py-2 text-sm text-[#1e3a5f] hover:bg-blue-50 disabled:bg-slate-200"
                >
                  {loadingMore ? '加载中...' : `加载更多 →`}
                </button>
              </div>
            )}
          </>
        )}

        <FooterDisclaimer />
      </main>

      {/* 详情 Modal */}
      {detailItem && (
        <div
          className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4"
          onClick={close}
        >
          <div
            className="bg-white rounded-lg max-w-3xl w-full max-h-[80vh] overflow-y-auto p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex justify-between items-start mb-4">
              <div>
                <h2 className="text-lg font-bold text-slate-800">
                  {detailItem.label || '匿名测评'}
                </h2>
                <p className="text-sm text-slate-500 mt-1">
                  {formatDateTime(detailItem.created_at)}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {detailItem.has_crisis && (
                  <span className="rounded bg-red-600 px-2 py-0.5 text-xs font-semibold text-white">
                    ⚠ 危机 · 12355
                  </span>
                )}
                <button
                  onClick={close}
                  className="text-slate-400 hover:text-slate-600 text-xl leading-none"
                >
                  ×
                </button>
              </div>
            </div>

            <div className="space-y-4">
              {detailItem.assessments.map((a) => {
                const key = `answers-${detailItem.session_id}-${a.scale_id}`
                const open = showAnswers[key]
                return (
                  <div
                    key={a.scale_id}
                    className="rounded-lg border border-slate-200 p-4 bg-slate-50"
                  >
                    <div className="flex justify-between items-center mb-2">
                      <h3 className="font-semibold text-slate-800">
                        {a.scale_name || a.scale_id}
                      </h3>
                      <span
                        className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                          SEV_COLOR[a.severity] || SEV_COLOR.none
                        }`}
                      >
                        {a.score}/{a.max_score} · {SEV_CN[a.severity] || a.severity}
                      </span>
                    </div>

                    {a.interpretation && (
                      <p className="text-sm text-slate-600 mt-2 mb-2">
                        <span className="font-medium text-slate-700">解释：</span>
                        {a.interpretation}
                      </p>
                    )}

                    {a.crisis_triggers && a.crisis_triggers.length > 0 && (
                      <div className="text-xs text-red-600 bg-red-50 rounded p-2 mt-2">
                        危机触发词：{a.crisis_triggers.join('；')}
                      </div>
                    )}

                    {a.crisis_level && (
                      <div className="text-xs mt-1 text-slate-600">
                        危机等级：<span className="font-medium">{a.crisis_level}</span>
                      </div>
                    )}

                    {a.answers && Object.keys(a.answers).length > 0 && (
                      <div className="mt-3">
                        <button
                          onClick={() =>
                            setShowAnswers((s) => ({ ...s, [key]: !open }))
                          }
                          className="text-xs text-[#1e3a5f] hover:underline"
                        >
                          {open ? '▼ 收起作答详情' : '▶ 展开作答详情'}
                        </button>
                        {open && (
                          <div className="mt-2 text-xs text-slate-600 bg-white rounded p-3 border border-slate-200 space-y-1">
                            {Object.entries(a.answers)
                              .sort(
                                ([x], [y]) => Number(x) - Number(y)
                              )
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
          </div>
        </div>
      )}
    </div>
  )
}
