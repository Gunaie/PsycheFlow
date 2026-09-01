import { useEffect, useRef, useState } from 'react'
import { apiGet, apiPost, apiPostBlob, apiPostForm } from '../api'
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

interface ChatResponse {
  reply: string
  sources: SourceRef[]
  crisis: boolean
  current_agent?: string  // 新增可选字段（向后兼容）
  agent_trace?: string[]   // 新增可选字段
  persona_id?: string      // 实际生效的人格（未知 id 回退 default）
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

  const send = async () => {
    const msg = input.trim()
    if (!msg || loading) return
    setInput('')
    setLoading(true)
    setError(null)
    setCurrentAgent(undefined)
    const history: ChatTurn[] = [...turns, { role: 'user', content: msg }]
    setTurns(history)
    try {
      const res = await apiPost<ChatResponse>('/api/chat', {
        message: msg,
        history: turns,
        session_id: localStorage.getItem('psycheflow_active_session_id') || null,
        persona_id: personaId,
      })
      setTurns([...history, { role: 'assistant', content: res.reply, agent: res.current_agent }])
      setSources(res.sources)
      setCrisis(res.crisis)
      setCurrentAgent(res.current_agent)
      // 后端对未知人格回退 default 时，同步校正本地选择
      if (res.persona_id) setPersonaId(res.persona_id)
    } catch (e) {
      setError((e as Error).message)
      setSources([])
      setCrisis(false)
      setCurrentAgent(undefined)
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
    <div className="space-y-3 flex flex-col h-[calc(100vh-160px)]">
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
                {t.content}
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
          {loading && (
            <div className="flex flex-col items-start">
              <div className="mb-1 ml-1">
                <span className="inline-block text-[10px] font-medium px-2 py-0.5 rounded border bg-slate-100 text-slate-500 border-slate-300">
                  编排中…
                </span>
              </div>
              <div className="bg-slate-100 text-slate-400 px-3 py-2 rounded-2xl text-sm">
                {activePersona ? `${activePersona.name}正在思考…` : '陪伴助手正在思考…'}
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {sources.length >= 1 && (
          <div className="sources mt-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
            <div className="mb-1 text-xs font-semibold text-slate-500">知识参考（来自 PsycheFlow 心理知识库公开摘要）</div>
            {sources.map((s, i) => (
              <div key={i} className="mb-2 last:mb-0 rounded border border-slate-200 bg-white p-2">
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
          onClick={send}
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
