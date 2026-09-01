import { useEffect, useRef, useState } from 'react'
import { apiPost } from '../api'
import CrisisBanner from '../components/CrisisBanner'

interface ChatTurn {
  role: 'user' | 'assistant'
  content: string
}

interface SourceRef {
  text: string
  source: string
}

interface ChatResponse {
  reply: string
  sources: SourceRef[]
  crisis: boolean
}

export default function ChatPage() {
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [sources, setSources] = useState<SourceRef[]>([])
  const [crisis, setCrisis] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns, loading])

  const send = async () => {
    const msg = input.trim()
    if (!msg || loading) return
    setInput('')
    setLoading(true)
    setError(null)
    const history: ChatTurn[] = [...turns, { role: 'user', content: msg }]
    setTurns(history)
    try {
      const res = await apiPost<ChatResponse>('/api/chat', {
        message: msg,
        history: turns,
      })
      setTurns([...history, { role: 'assistant', content: res.reply }])
      setSources(res.sources)
      setCrisis(res.crisis)
    } catch (e) {
      setError((e as Error).message)
      setSources([])
      setCrisis(false)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-4 flex flex-col h-[calc(100vh-160px)]">
      <div>
        <h1 className="text-xl font-bold text-slate-800">开放对话</h1>
        <p className="text-sm text-slate-500 mt-1">和 PsycheFlow 陪伴助手聊聊你的近况。</p>
      </div>

      {crisis && <CrisisBanner />}

      <div className="flex-1 bg-white rounded-xl border border-slate-200 flex flex-col overflow-hidden">
        <div className="flex-1 space-y-3 p-4 overflow-y-auto">
          {turns.length === 0 && !loading && (
            <div className="text-sm text-slate-400 text-center py-12">
              试试说"最近考试压力大"或"我和同学闹矛盾了"
            </div>
          )}
          {turns.map((t, i) => (
            <div
              key={i}
              className={`flex ${t.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[80%] px-3 py-2 rounded-2xl text-sm whitespace-pre-wrap ${
                  t.role === 'user'
                    ? 'bg-primary-500 text-white'
                    : 'bg-slate-100 text-slate-700'
                }`}
              >
                {t.content}
              </div>
            </div>
          ))}
          {loading && (
            <div className="flex justify-start">
              <div className="bg-slate-100 text-slate-400 px-3 py-2 rounded-2xl text-sm">
                陪伴助手正在思考…
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {sources.length > 0 && (
          <div className="px-4 pb-3 border-t border-slate-100 pt-2">
            <details className="text-xs text-slate-400">
              <summary className="cursor-pointer hover:text-slate-600">
                知识参考（{sources.length}）
              </summary>
              <ul className="mt-1 space-y-1">
                {sources.map((s, i) => (
                  <li key={i} className="text-slate-500">
                    {s.source}：{s.text}
                  </li>
                ))}
              </ul>
            </details>
          </div>
        )}
      </div>

      {error && <div className="bg-red-50 text-red-600 text-sm p-3 rounded-lg">{error}</div>}

      <div className="flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send()
            }
          }}
          placeholder="输入你想聊的…"
          className="flex-1 border border-slate-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-primary-500"
        />
        <button
          onClick={send}
          disabled={loading}
          className="bg-primary-500 text-white px-5 py-2.5 rounded-xl text-sm font-semibold hover:bg-primary-600 disabled:opacity-50"
        >
          发送
        </button>
      </div>
    </div>
  )
}
