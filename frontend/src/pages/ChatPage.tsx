import { useEffect, useRef, useState } from 'react'
import { apiDelete, apiGet, apiPost, apiPostBlob, apiPostForm, getChatSessionId, setChatSessionId, streamChat } from '../api'
import { WavRecorder } from '../lib/recorder'
import CrisisBanner from '../components/CrisisBanner'
import FooterDisclaimer from '../components/FooterDisclaimer'

interface ChatTurn {
  role: 'user' | 'assistant'
  content: string
  agent?: string  // 当前回复来自哪个 Agent（triage/assessment/intervention/escalation/case）
  sources?: SourceRef[]  // 该轮引用的知识来源（挂到对应轮次，刷新后从历史恢复）
}

interface SourceRef {
  text: string
  source: string
  chunk_id?: number
}

interface PersonaOption {
  persona_id: string
  name: string
  avatar: string
  description: string
}

interface CaseAttachment {
  kind: 'pdf' | 'text'
  name: string
  char_count?: number
}

// 对话独立 session：不复用测评的 active_session_id（避免跨用户串号 + 测评/对话解耦）。
async function ensureChatSessionId(): Promise<string> {
  const existing = getChatSessionId()
  if (existing) return existing
  const session = await apiPost<{ session_id: string }>('/api/sessions', { label: '对话' })
  setChatSessionId(session.session_id)
  return session.session_id
}

// 智能体阶段样式映射（用于 AgentBadge 展示）
const AGENT_STAGES = [
  { key: 'triage', label: '分诊', lightColor: 'bg-blue-50 text-blue-700 border-blue-300' },
  { key: 'assessment', label: '测评', lightColor: 'bg-cyan-50 text-cyan-700 border-cyan-300' },
  { key: 'intervention', label: '干预', lightColor: 'bg-emerald-50 text-emerald-700 border-emerald-300' },
  { key: 'escalation', label: '升级', lightColor: 'bg-red-100 text-red-700 border-red-400' },
  { key: 'case', label: '病例解读', lightColor: 'bg-purple-50 text-purple-700 border-purple-300' },
]

function AgentBadge({ agent }: { agent: string | undefined }) {
  if (!agent) return null
  const stage = AGENT_STAGES.find(s => s.key === agent)
  if (!stage) return null
  return (
    <span className={`inline-block text-[10px] font-medium px-2 py-0.5 rounded border ${stage.lightColor}`}>
      from: {stage.label} Agent
    </span>
  )
}

/** 病例结构化摘要卡片（诊断/用药/复诊/关注）。 */
function CaseSummaryCard({ summary }: { summary: Record<string, string> }) {
  if (!summary || Object.keys(summary).length === 0) return null
  const labels: Record<string, string> = { '诊断': '诊断', '用药': '用药', '复诊': '复诊', '关注': '关注' }
  const icons: Record<string, string> = { '诊断': '📋', '用药': '💊', '复诊': '📅', '关注': '⚠️' }
  return (
    <div className="mt-1 ml-1 w-full max-w-[80%] rounded-lg border border-indigo-200 bg-indigo-50/50 p-3">
      <p className="text-[11px] font-medium text-indigo-600 mb-2">结构化摘要</p>
      <div className="space-y-1.5">
        {Object.entries(labels).map(([key, label]) =>
          summary[key] ? (
            <div key={key} className="flex items-start gap-1.5">
              <span className="text-xs shrink-0">{icons[key]}</span>
              <span className="text-[11px] text-slate-500 shrink-0 min-w-[3rem]">{label}：</span>
              <span className="text-xs text-slate-700 whitespace-pre-line">{summary[key]}</span>
            </div>
          ) : null,
        )}
      </div>
    </div>
  )
}

/** 单轮知识来源卡片（每轮独立折叠，默认收起）。 */
function TurnSources({ sources }: { sources: SourceRef[] }) {
  const [open, setOpen] = useState(false)
  if (!sources || sources.length === 0) return null
  return (
    <div className="mt-1 ml-1 w-full max-w-[80%]">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="text-[11px] text-slate-400 hover:text-slate-600"
      >
        知识参考（{sources.length} 条）{open ? ' ▾' : ' ▸'}
      </button>
      {open && (
        <div className="mt-1 space-y-1">
          {sources.map((s, i) => (
            <div key={i} className="rounded border border-slate-200 bg-slate-50 p-2">
              <p className="text-[11px] text-slate-500 mb-0.5">来源：《{s.source}》片段 #{(s.chunk_id ?? 0) + 1}</p>
              <p className="text-xs text-slate-600">{s.text}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const MAX_INPUT = 2000

export default function ChatPage() {
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [crisis, setCrisis] = useState(false)
  const [currentAgent, setCurrentAgent] = useState<string | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)
  const [personas, setPersonas] = useState<PersonaOption[]>([])
  const [personaId, setPersonaId] = useState('default')
  const [recording, setRecording] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const [speakingIdx, setSpeakingIdx] = useState<number | null>(null)
  // 病例上传面板
  const [casePanel, setCasePanel] = useState(false)
  const [caseMode, setCaseMode] = useState<'pdf' | 'text'>('pdf')
  const [caseFile, setCaseFile] = useState<File | null>(null)
  const [caseText, setCaseText] = useState('')
  const [caseContext, setCaseContext] = useState<string | null>(null) // 病例追问：持有上次上传的文书原文
  const [caseSummary, setCaseSummary] = useState<Record<string, string> | null>(null) // 结构化摘要卡片
  const [analyzing, setAnalyzing] = useState(false)
  const [startingNew, setStartingNew] = useState(false)
  const [clearing, setClearing] = useState(false)

  const recorderRef = useRef<WavRecorder | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns, loading])

  // 输入框自适应高度（多行 textarea，Enter 发送 / Shift+Enter 换行）
  useEffect(() => {
    const ta = taRef.current
    if (ta) {
      ta.style.height = 'auto'
      ta.style.height = `${Math.min(ta.scrollHeight, 120)}px`
    }
  }, [input])

  // 拉取可用人格列表（失败不阻断对话，保留默认人格）
  useEffect(() => {
    apiGet<PersonaOption[]>('/api/personas').then(setPersonas).catch(() => {})
  }, [])

  // 刷新后回填历史对话（ConversationTurn 已在后端双写落库，按 chat session 拉回）
  useEffect(() => {
    const sid = getChatSessionId()
    if (!sid) return
    let cancelled = false
    apiGet<{ items: Array<{ role: string; content: string; sources: SourceRef[]; attachments: CaseAttachment[]; crisis_hit: boolean }> }>(
      `/api/chat/history?session_id=${encodeURIComponent(sid)}`,
    )
      .then(data => {
        if (cancelled || !data.items?.length) return
        setTurns(
          data.items
            .filter(t => t.role === 'user' || t.role === 'assistant')
            .map(t => ({
              role: t.role as 'user' | 'assistant',
              content: t.content,
              sources: t.sources && t.sources.length ? t.sources : undefined,
              agent: t.role === 'assistant' ? (t.crisis_hit ? 'escalation' : undefined) : undefined,
            })),
        )
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  const activePersona = personas.find(p => p.persona_id === personaId)

  const send = async (overrideMsg?: string) => {
    const msg = (overrideMsg ?? input).trim()
    if (!msg || loading) return
    setInput('')
    setLoading(true)
    setError(null)
    setCurrentAgent(undefined)
    setCrisis(false)

    // 先写 user turn + 空 assistant turn（占位，边收 token 边填）
    const prevTurns = turns
    const newTurns: ChatTurn[] = [
      ...prevTurns,
      { role: 'user', content: msg },
      { role: 'assistant', content: '', agent: undefined },
    ]
    setTurns(newTurns)

    const ctrl = new AbortController()
    abortRef.current = ctrl
    try {
      // 对话用独立 chat session（与测评解耦），首次 send 时按需创建
      const chatSid = await ensureChatSessionId()
      await streamChat(
        {
          message: msg,
          history: prevTurns.map(t => ({ role: t.role, content: t.content })),
          session_id: chatSid,
          persona_id: personaId,
          case_context: caseContext ?? undefined, // 病例追问时携带文书原文
        },
        (evt) => {
          const { event, data } = evt
          if (event === 'agent') {
            // 更新 stepper + 当前 assistant 气泡的 agent badge
            setCurrentAgent(data.agent)
            setTurns((prev) => {
              const next = [...prev]
              const last = next.length - 1
              if (next[last]?.role === 'assistant') {
                next[last] = { ...next[last], agent: data.agent }
              }
              return next
            })
          } else if (event === 'sources') {
            // 知识来源挂到当前 assistant 轮（历史轮次互不覆盖）
            setTurns((prev) => {
              const next = [...prev]
              const last = next.length - 1
              if (next[last]?.role === 'assistant') {
                next[last] = { ...next[last], sources: data.sources || [] }
              }
              return next
            })
          } else if (event === 'token') {
            // 累加 token 到 assistant 气泡（边生成边显示）
            setTurns((prev) => {
              const next = [...prev]
              const last = next.length - 1
              if (next[last]?.role === 'assistant') {
                next[last] = {
                  ...next[last],
                  content: next[last].content + data.token,
                }
              }
              return next
            })
          } else if (event === 'crisis') {
            // 危机路径不流式，整段话术一次性推
            setTurns((prev) => {
              const next = [...prev]
              const last = next.length - 1
              if (next[last]?.role === 'assistant') {
                next[last] = { ...next[last], content: data.reply, agent: 'escalation' }
              }
              return next
            })
            setCrisis(true)
            setCurrentAgent('escalation')
          } else if (event === 'done') {
            // 兜底：若 token 累加缺失（如异常 fallback/停止时部分内容），用 done.reply 补全
            setTurns((prev) => {
              const next = [...prev]
              const last = next.length - 1
              if (next[last]?.role === 'assistant') {
                const cur = next[last].content || ''
                if (!cur.trim() && data.reply) {
                  next[last] = {
                    ...next[last],
                    content: data.reply,
                    agent: data.current_agent || next[last].agent,
                    sources: data.sources?.length ? data.sources : next[last].sources,
                  }
                } else {
                  next[last] = {
                    ...next[last],
                    agent: data.current_agent || next[last].agent,
                    sources: next[last].sources || (data.sources?.length ? data.sources : undefined),
                  }
                }
              }
              return next
            })
            setCrisis(!!data.crisis)
            setCurrentAgent(data.current_agent)
            // 后端对未知人格回退 default 时，同步校正本地选择
            if (data.persona_id) setPersonaId(data.persona_id)
          } else if (event === 'error') {
            setError(data.message || '流式异常')
          }
        },
        ctrl.signal,
      )
    } catch (e) {
      // 用户主动点「停止」导致的 AbortError 静默处理（部分内容已落库，刷新可回填）
      if ((e as Error).name !== 'AbortError') {
        setError((e as Error).message)
      }
    } finally {
      abortRef.current = null
      setLoading(false)
    }
  }

  /** 停止生成：中止 SSE 读取（后端 finally 会把已生成片段落库）。 */
  const stopGeneration = () => {
    abortRef.current?.abort()
  }

  /** 开新对话：新建独立 chat session 并清空当前页面。 */
  const startNewChat = async () => {
    if (startingNew) return
    abortRef.current?.abort()
    setStartingNew(true)
    try {
      const session = await apiPost<{ session_id: string }>('/api/sessions', { label: '对话' })
      setChatSessionId(session.session_id)
      setTurns([])
      setError(null)
      setCrisis(false)
      setCurrentAgent(undefined)
      setCaseContext(null)
      setCaseSummary(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setStartingNew(false)
    }
  }

  /** 清空当前会话的全部聊天记录（服务端删除，不可恢复）。 */
  const clearHistory = async () => {
    if (clearing || busy) return
    const sid = getChatSessionId()
    if (!sid) return
    if (!window.confirm('确定清空当前对话的全部聊天记录？清空后不可恢复。')) return
    setClearing(true)
    try {
      await apiDelete(`/api/chat/history?session_id=${encodeURIComponent(sid)}`)
      setTurns([])
      setError(null)
      setCrisis(false)
      setCurrentAgent(undefined)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setClearing(false)
    }
  }

  /** 病例解读：上传 PDF 或粘贴文本 → 后端解析+危机扫描+LLM 解读，结果作为一轮对话。 */
  const submitCase = async () => {
    if (analyzing) return
    if (caseMode === 'pdf' && !caseFile) {
      setError('请先选择 PDF 病例文件')
      return
    }
    if (caseMode === 'text' && !caseText.trim()) {
      setError('请粘贴病例文本内容')
      return
    }
    setAnalyzing(true)
    setError(null)

    const fileName = caseFile?.name || '粘贴文本'
    const bubble = caseMode === 'pdf' ? `📎 病例文件：${fileName}` : '📝 粘贴的病例文本'
    const prevTurns = turns
    // 乐观占位：user 描述轮 + 空 assistant 轮
    setTurns([
      ...prevTurns,
      { role: 'user', content: bubble },
      { role: 'assistant', content: '', agent: 'case' },
    ])

    try {
      const chatSid = await ensureChatSessionId()
      const form = new FormData()
      form.append('session_id', chatSid)
      form.append('persona_id', personaId)
      if (caseMode === 'pdf' && caseFile) {
        form.append('file', caseFile)
      } else {
        form.append('text', caseText)
      }
      const res = await apiPostForm<{
        reply: string
        sources: SourceRef[]
        crisis: boolean
        current_agent: string
        attachment: CaseAttachment
      }>('/api/chat/case-upload', form)

      // 保存原文供后续追问；保存摘要供卡片渲染
      if (res.case_text) setCaseContext(res.case_text)
      if (res.case_summary) setCaseSummary(res.case_summary)

      setTurns((prev) => {
        const next = [...prev]
        const last = next.length - 1
        if (next[last]?.role === 'assistant') {
          next[last] = {
            ...next[last],
            content: res.reply,
            agent: res.current_agent || 'case',
            sources: res.sources && res.sources.length ? res.sources : undefined,
          }
        }
        return next
      })
      if (res.crisis) setCrisis(true)
      // 成功后收起面板并清空
      setCasePanel(false)
      setCaseFile(null)
      setCaseText('')
    } catch (e) {
      // 失败（扫描件/超限/422 等）：撤掉占位轮，保留用户输入以便调整后重试
      setError((e as Error).message)
      setTurns(prev => prev.slice(0, Math.max(0, prev.length - 2)))
    } finally {
      setAnalyzing(false)
    }
  }

  /** 语音输入：点击开始录音，再次点击结束并转写回填输入框（可编辑后再发送）。 */
  const toggleRecord = async () => {
    if (recording) {
      const rec = recorderRef.current
      recorderRef.current = null
      setRecording(false)
      setTranscribing(true)
      try {
        const blob = await rec!.stop()
        const form = new FormData()
        form.append('file', blob, 'speech.wav')
        const { text } = await apiPostForm<{ text: string }>('/api/voice/transcribe', form)
        setInput((prev) => (prev ? `${prev}${text}` : text))
      } catch (e) {
        setError((e as Error).message)
      } finally {
        setTranscribing(false)
      }
    } else {
      try {
        setError(null)
        const rec = new WavRecorder()
        await rec.start()
        recorderRef.current = rec
        setRecording(true)
      } catch (e) {
        setError('无法使用麦克风：' + (e as Error).message)
      }
    }
  }

  /** 语音输出：朗读 AI 回复（后端已剥离「来源：《xxx》」标记）。 */
  const speak = async (idx: number, text: string) => {
    if (speakingIdx !== null) return
    setSpeakingIdx(idx)
    try {
      const blob = await apiPostBlob('/api/voice/synthesize', { text })
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      const done = () => {
        URL.revokeObjectURL(url)
        setSpeakingIdx(null)
      }
      audio.onended = done
      audio.onerror = done
      await audio.play()
    } catch (e) {
      setError('语音播放失败：' + (e as Error).message)
      setSpeakingIdx(null)
    }
  }

  const busy = loading || analyzing

  return (
    <div className="space-y-2 flex flex-col h-[calc(100vh-56px)]">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-slate-800">开放对话</h1>
          <p className="text-sm text-slate-500 mt-1">
            {activePersona
              ? `和「${activePersona.name}」聊聊你的近况：${activePersona.description}。`
              : '和 PsycheFlow 陪伴助手聊聊你的近况。'}
          </p>
        </div>
        <div className="shrink-0 flex items-center gap-2">
          <button
            type="button"
            onClick={clearHistory}
            disabled={clearing || busy || turns.length === 0}
            className="text-sm px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-slate-600 hover:border-red-300 hover:text-red-600 disabled:opacity-50 transition"
            title="删除当前对话的全部聊天记录（不可恢复）"
          >
            {clearing ? '清空中…' : '🗑 清空记录'}
          </button>
          <button
            type="button"
            onClick={startNewChat}
            disabled={startingNew || busy}
            className="text-sm px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-slate-600 hover:border-primary-400 hover:text-primary-700 disabled:opacity-50 transition"
            title="清空当前对话，开启一段新会话"
          >
            {startingNew ? '开启中…' : '＋ 新对话'}
          </button>
        </div>
      </div>

      {/* 多角色人格选择（切换只影响干预 Agent 的语气风格，安全底线不变） */}
      {personas.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {personas.map(p => (
            <button
              key={p.persona_id}
              type="button"
              title={p.description}
              onClick={() => setPersonaId(p.persona_id)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm border transition ${
                personaId === p.persona_id
                  ? 'bg-primary-50 border-primary-500 text-primary-700 font-semibold'
                  : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300'
              }`}
            >
              <span aria-hidden>{p.avatar}</span>
              {p.name}
            </button>
          ))}
        </div>
      )}

      {crisis && <CrisisBanner />}

      <div className="flex-1 bg-white rounded-xl border border-slate-200 flex flex-col overflow-hidden min-h-0">
        <div className="flex-1 space-y-3 p-4 overflow-y-auto min-h-0">
          {turns.length === 0 && !busy && (
            <div className="flex flex-col items-center gap-4 py-10">
              <img
                src="/images/chat-empty.png"
                alt="AI 陪伴助手插画"
                className="w-36 h-36 rounded-2xl object-cover"
                onError={e => {
                  const el = e.currentTarget
                  el.style.display = 'none'
                  const fb = el.nextElementSibling as HTMLElement | null
                  if (fb) fb.classList.remove('hidden')
                }}
              />
              <div className="text-4xl hidden">💬</div>
              <p className="text-sm text-slate-500 text-center max-w-md">
                您好！我是 PsycheFlow 陪伴助手。您可以和我聊聊最近的状况，
                点击下方话题快速开始；也可以点 📎 上传医院病例，我会用大白话帮您解读。
              </p>
              <div className="flex flex-wrap gap-2 justify-center max-w-md">
                {[
                  '我想做测评',
                  '我最近压力大',
                  '什么是焦虑',
                  '我心情不好',
                ].map(suggestion => (
                  <button
                    key={suggestion}
                    type="button"
                    onClick={() => send(suggestion)}
                    className="px-3 py-1.5 rounded-full text-sm border border-slate-200 bg-white text-slate-600 hover:border-primary-400 hover:text-primary-700 transition"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}
          {turns.map((t, i) => (
            <div
              key={i}
              className={`flex flex-col ${t.role === 'user' ? 'items-end' : 'items-start'}`}
            >
              {t.role === 'assistant' && t.agent && (
                <div className="mb-1 ml-1">
                  <AgentBadge agent={t.agent} />
                </div>
              )}
              <div
                className={`max-w-[80%] px-3 py-2 rounded-2xl text-sm whitespace-pre-wrap ${
                  t.role === 'user'
                    ? 'bg-primary-500 text-white'
                    : 'bg-slate-100 text-slate-700'
                }`}
              >
                {t.role === 'assistant' && busy && i === turns.length - 1 && !t.content
                  ? (
                    <>
                      {t.agent === 'case'
                        ? '正在解读病例，约需 10-30 秒…'
                        : (activePersona ? `${activePersona.name}正在思考…` : '陪伴助手正在思考…')}
                      {/* 只显示阶段进度文字；Agent 徽标已在气泡上方渲染，勿重复（避免双「from: 干预 Agent」） */}
                      {AGENT_STAGES.find(s => s.key === currentAgent) && (
                        <span className="mt-1 block text-[10px] text-slate-400">
                          {AGENT_STAGES.find(s => s.key === currentAgent)?.label}中…
                        </span>
                      )}
                    </>
                  )
                  : t.content}
              </div>
              {t.role === 'assistant' && t.content && !busy && (
                <button
                  type="button"
                  onClick={() => speak(i, t.content)}
                  disabled={speakingIdx !== null}
                  className="mt-1 ml-1 text-[11px] text-slate-400 hover:text-slate-600 disabled:opacity-50"
                >
                  {speakingIdx === i ? '⏹ 朗读中…' : '🔊 朗读'}
                </button>
              )}
              {t.role === 'assistant' && <TurnSources sources={t.sources || []} />}
              {t.role === 'assistant' && t.agent === 'case' && caseSummary && (
                <CaseSummaryCard summary={caseSummary} />
              )}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      </div>

      {/* 病例上传面板（📎 展开）：PDF 文件 / 粘贴文本 二选一 */}
      {casePanel && (
        <div className="border border-purple-200 bg-purple-50/40 rounded-xl p-3 space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex gap-2 text-sm">
              <button
                type="button"
                onClick={() => setCaseMode('pdf')}
                className={`px-2.5 py-1 rounded-md border transition ${
                  caseMode === 'pdf'
                    ? 'bg-purple-100 border-purple-400 text-purple-700 font-semibold'
                    : 'bg-white border-slate-200 text-slate-600'
                }`}
              >
                PDF 文件
              </button>
              <button
                type="button"
                onClick={() => setCaseMode('text')}
                className={`px-2.5 py-1 rounded-md border transition ${
                  caseMode === 'text'
                    ? 'bg-purple-100 border-purple-400 text-purple-700 font-semibold'
                    : 'bg-white border-slate-200 text-slate-600'
                }`}
              >
                粘贴文本
              </button>
            </div>
            <button
              type="button"
              onClick={() => setCasePanel(false)}
              className="text-slate-400 hover:text-slate-600 text-sm"
            >
              ✕
            </button>
          </div>

          {caseMode === 'pdf' ? (
            <div className="text-sm space-y-1">
              <input
                type="file"
                accept=".pdf,application/pdf"
                className="block w-full text-sm text-slate-600 file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-purple-100 file:text-purple-700 hover:file:bg-purple-200"
                onChange={e => setCaseFile(e.target.files?.[0] || null)}
              />
              <p className="text-xs text-slate-500">
                支持文字版 PDF（诊断证明/出院小结/测评报告，≤10MB）；扫描拍照件请改用「粘贴文本」。
              </p>
            </div>
          ) : (
            <textarea
              value={caseText}
              onChange={e => setCaseText(e.target.value.slice(0, 6000))}
              rows={4}
              placeholder="把病历上的文字复制粘贴到这里（可含诊断、医嘱、量表分数等）…"
              className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-purple-400 resize-y"
            />
          )}

          <div className="flex items-center justify-between gap-2">
            <p className="text-[11px] text-slate-400">
              解读仅为科普参考，不替代医生诊断；文件仅在内存解析、内容不留存。
            </p>
            <button
              type="button"
              onClick={submitCase}
              disabled={analyzing}
              className="shrink-0 bg-purple-600 text-white px-4 py-1.5 rounded-lg text-sm font-semibold hover:bg-purple-700 disabled:opacity-50"
            >
              {analyzing ? '解读中…' : '发送解读'}
            </button>
          </div>
        </div>
      )}

      {error && <div className="bg-red-50 text-red-600 text-sm p-3 rounded-lg">{error}</div>}

      <div className="flex gap-2 items-end">
        <button
          type="button"
          onClick={toggleRecord}
          disabled={busy || transcribing}
          title={recording ? '点击结束录音' : '点击开始录音'}
          className={`px-4 py-2.5 rounded-xl text-sm font-semibold transition ${
            recording
              ? 'bg-red-500 text-white animate-pulse'
              : 'bg-slate-200 text-slate-600 hover:bg-slate-300 disabled:opacity-50'
          }`}
        >
          {transcribing ? '识别中…' : recording ? '⏹ 结束' : '🎤'}
        </button>
        <button
          type="button"
          onClick={() => setCasePanel(p => !p)}
          disabled={busy}
          title="上传病例（PDF/文本）做科普解读"
          className={`px-4 py-2.5 rounded-xl text-sm font-semibold transition ${
            casePanel
              ? 'bg-purple-100 text-purple-700 ring-1 ring-purple-300'
              : 'bg-slate-200 text-slate-600 hover:bg-slate-300 disabled:opacity-50'
          }`}
        >
          📎
        </button>
        <div className="flex-1 relative">
          <textarea
            ref={taRef}
            value={input}
            rows={1}
            maxLength={MAX_INPUT}
            onChange={(e) => setInput(e.target.value.slice(0, MAX_INPUT))}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                send()
              }
            }}
            placeholder="输入你想聊的…（Enter 发送，Shift+Enter 换行）"
            className="w-full border border-slate-300 rounded-xl px-4 py-2.5 pr-14 text-sm focus:outline-none focus:border-primary-500 resize-none overflow-hidden leading-6"
          />
          <span className="absolute right-3 bottom-1.5 text-[10px] text-slate-300 pointer-events-none">
            {input.length}/{MAX_INPUT}
          </span>
        </div>
        {loading ? (
          <button
            type="button"
            onClick={stopGeneration}
            className="bg-red-500 text-white px-5 py-2.5 rounded-xl text-sm font-semibold hover:bg-red-600"
            title="停止生成（已生成的内容会保留）"
          >
            停止
          </button>
        ) : (
          <button
            onClick={() => send()}
            disabled={analyzing}
            className="bg-primary-500 text-white px-5 py-2.5 rounded-xl text-sm font-semibold hover:bg-primary-600 disabled:opacity-50"
          >
            发送
          </button>
        )}
      </div>

      <FooterDisclaimer />
    </div>
  )
}
