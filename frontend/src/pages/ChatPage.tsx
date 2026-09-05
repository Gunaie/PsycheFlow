import { useEffect, useRef, useState } from 'react'
import { apiGet, apiPost, apiPostBlob, apiPostForm, getChatSessionId, setChatSessionId, streamChat } from '../api'
import { WavRecorder } from '../lib/recorder'
import CrisisBanner from '../components/CrisisBanner'
import FooterDisclaimer from '../components/FooterDisclaimer'

interface ChatTurn {
  role: 'user' | 'assistant'
  content: string
  agent?: string  // 当前回复来自哪个 Agent（triage/assessment/intervention/escalation）
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

// 4 智能体阶段定义
const AGENT_STAGES = [
  { key: 'triage', label: '分诊', color: 'bg-blue-500', lightColor: 'bg-blue-50 text-blue-700 border-blue-300' },
  { key: 'assessment', label: '测评', color: 'bg-cyan-500', lightColor: 'bg-cyan-50 text-cyan-700 border-cyan-300' },
  { key: 'intervention', label: '干预', color: 'bg-emerald-500', lightColor: 'bg-emerald-50 text-emerald-700 border-emerald-300' },
  { key: 'escalation', label: '升级', color: 'bg-red-500', lightColor: 'bg-red-100 text-red-700 border-red-400' },
]

function getStageIndex(agent: string | undefined): number {
  if (!agent) return -1
  return AGENT_STAGES.findIndex(s => s.key === agent)
}

// 对话独立 session：不复用测评的 active_session_id（避免跨用户串号 + 测评/对话解耦）。
// 首次 send 时若无 chat session 则创建一个（label="对话"），跨刷新保留连续性，退出登录即清。
async function ensureChatSessionId(): Promise<string> {
  const existing = getChatSessionId()
  if (existing) return existing
  const session = await apiPost<{ session_id: string }>('/api/sessions', { label: '对话' })
  setChatSessionId(session.session_id)
  return session.session_id
}

function StageStepper({ currentAgent, crisis }: { currentAgent: string | undefined; crisis: boolean }) {
  const currentIdx = getStageIndex(currentAgent)
  return (
    <div className="flex items-center justify-between px-2 py-2 bg-slate-50 rounded-lg border border-slate-200">
      {AGENT_STAGES.map((stage, i) => {
        const isCurrent = i === currentIdx
        const isPast = currentIdx > i
        const isCrisis = crisis && stage.key === 'escalation'
        return (
          <div key={stage.key} className="flex items-center flex-1 last:flex-none">
            <div className="flex flex-col items-center">
              <div
                className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold transition ${
                  isCrisis
                    ? 'bg-red-500 text-white ring-4 ring-red-200'
                    : isCurrent
                    ? `${stage.color} text-white ring-4 ring-slate-200`
                    : isPast
                    ? 'bg-slate-400 text-white'
                    : 'bg-slate-200 text-slate-400'
                }`}
              >
                {i + 1}
              </div>
              <span
                className={`mt-1 text-[10px] font-medium ${
                  isCurrent || isCrisis ? 'text-slate-700' : 'text-slate-400'
                }`}
              >
                {stage.label}
              </span>
            </div>
            {i < AGENT_STAGES.length - 1 && (
              <div
                className={`flex-1 h-0.5 mx-1 mb-4 transition ${
                  currentIdx > i ? 'bg-slate-400' : 'bg-slate-200'
                }`}
              />
            )}
          </div>
        )
      })}
    </div>
  )
}

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

export default function ChatPage() {
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [sources, setSources] = useState<SourceRef[]>([])
  const [crisis, setCrisis] = useState(false)
  const [currentAgent, setCurrentAgent] = useState<string | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)
  const [personas, setPersonas] = useState<PersonaOption[]>([])
  const [personaId, setPersonaId] = useState('default')
  const [recording, setRecording] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const [speakingIdx, setSpeakingIdx] = useState<number | null>(null)
  const [sourcesCollapsed, setSourcesCollapsed] = useState(true)
  const recorderRef = useRef<WavRecorder | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns, loading])

  // 拉取可用人格列表（失败不阻断对话，保留默认人格）
  useEffect(() => {
    apiGet<PersonaOption[]>('/api/personas').then(setPersonas).catch(() => {})
  }, [])

  const activePersona = personas.find(p => p.persona_id === personaId)

  const send = async (overrideMsg?: string) => {
    const msg = (overrideMsg ?? input).trim()
    if (!msg || loading) return
    setInput('')
    setLoading(true)
    setError(null)
    setCurrentAgent(undefined)
    setSources([])
    setCrisis(false)

    // 先写 user turn + 空 assistant turn（占位，边收 token 边填）
    const prevTurns = turns
    const newTurns: ChatTurn[] = [
      ...prevTurns,
      { role: 'user', content: msg },
      { role: 'assistant', content: '', agent: undefined },
    ]
    setTurns(newTurns)

    try {
      // 对话用独立 chat session（与测评解耦），首次 send 时按需创建
      const chatSid = await ensureChatSessionId()
      await streamChat(
        {
          message: msg,
          history: prevTurns, // 历史不含当前轮 user msg
          session_id: chatSid,
          persona_id: personaId,
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
            setSources(data.sources || [])
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
            // 兜底：若 token 累加缺失（如异常 fallback），用 done.reply 补全
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
                  }
                } else if (data.current_agent) {
                  next[last] = { ...next[last], agent: data.current_agent }
                }
              }
              return next
            })
            setCrisis(!!data.crisis)
            setCurrentAgent(data.current_agent)
            if (data.sources && data.sources.length) setSources(data.sources)
            // 后端对未知人格回退 default 时，同步校正本地选择
            if (data.persona_id) setPersonaId(data.persona_id)
          } else if (event === 'error') {
            setError(data.message || '流式异常')
          }
        },
      )
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
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

  return (
    <div className="space-y-2 flex flex-col h-[calc(100vh-56px)]">
      <div>
        <h1 className="text-xl font-bold text-slate-800">开放对话</h1>
        <p className="text-sm text-slate-500 mt-1">
          {activePersona
            ? `和「${activePersona.name}」聊聊你的近况：${activePersona.description}。`
            : '和 PsycheFlow 陪伴助手聊聊你的近况。'}
        </p>
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

      {/* 4 智能体阶段 Stepper */}
      <StageStepper currentAgent={currentAgent} crisis={crisis} />

      {crisis && <CrisisBanner />}

      <div className="flex-1 bg-white rounded-xl border border-slate-200 flex flex-col overflow-hidden min-h-0">
        <div className="flex-1 space-y-3 p-4 overflow-y-auto min-h-0">
          {turns.length === 0 && !loading && (
            <div className="flex flex-col items-center gap-4 py-10">
              <div className="text-4xl">💬</div>
              <p className="text-sm text-slate-500 text-center max-w-md">
                您好！我是 PsycheFlow 陪伴助手。您可以和我聊聊最近的状况，
                或者点击下方话题快速开始：
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
                {t.role === 'assistant' && loading && i === turns.length - 1 && !t.content
                  ? (activePersona ? `${activePersona.name}正在思考…` : '陪伴助手正在思考…')
                  : t.content}
              </div>
              {t.role === 'assistant' && t.content && (
                <button
                  type="button"
                  onClick={() => speak(i, t.content)}
                  disabled={speakingIdx !== null}
                  className="mt-1 ml-1 text-[11px] text-slate-400 hover:text-slate-600 disabled:opacity-50"
                >
                  {speakingIdx === i ? '⏹ 朗读中…' : '🔊 朗读'}
                </button>
              )}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        {sources.length >= 1 && (
          <div className="sources mt-2 border-t border-slate-200 bg-slate-50 p-2 text-sm">
            <button
              type="button"
              onClick={() => setSourcesCollapsed(c => !c)}
              className="w-full flex items-center justify-between text-xs font-semibold text-slate-500 hover:text-slate-700"
            >
              <span>知识参考（{sources.length} 条，来自 PsycheFlow 心理知识库公开摘要）</span>
              <span>{sourcesCollapsed ? '展开 ▸' : '收起 ▾'}</span>
            </button>
            {!sourcesCollapsed && sources.map((s, i) => (
              <div key={i} className="mt-2 rounded border border-slate-200 bg-white p-2">
                <p className="text-xs text-slate-500 mb-1">来源：《{s.source}》片段 #{(s.chunk_id ?? 0)+1}</p>
                <p className="text-slate-700">{s.text}</p>
              </div>
            ))}
          </div>
        )}
      </div>

      {error && <div className="bg-red-50 text-red-600 text-sm p-3 rounded-lg">{error}</div>}

      <div className="flex gap-2">
        <button
          type="button"
          onClick={toggleRecord}
          disabled={loading || transcribing}
          title={recording ? '点击结束录音' : '点击开始录音'}
          className={`px-4 py-2.5 rounded-xl text-sm font-semibold transition ${
            recording
              ? 'bg-red-500 text-white animate-pulse'
              : 'bg-slate-200 text-slate-600 hover:bg-slate-300 disabled:opacity-50'
          }`}
        >
          {transcribing ? '识别中…' : recording ? '⏹ 结束' : '🎤'}
        </button>
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
          onClick={() => send()}
          disabled={loading}
          className="bg-primary-500 text-white px-5 py-2.5 rounded-xl text-sm font-semibold hover:bg-primary-600 disabled:opacity-50"
        >
          发送
        </button>
      </div>

      <FooterDisclaimer />
    </div>
  )
}
