import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface ChatTurn {
  role: 'user' | 'assistant'
  content: string
  agent?: string
  sources?: Array<{ text: string; source: string; chunk_id?: number }>
}

interface ChatState {
  // 对话历史
  turns: ChatTurn[]
  setTurns: (turns: ChatTurn[]) => void
  appendTurn: (turn: ChatTurn) => void
  updateLastAssistant: (content: string) => void
  appendToLastAssistant: (delta: string) => void
  updateLastAssistantMeta: (meta: Partial<Pick<ChatTurn, 'agent' | 'sources' | 'content'>>) => void
  clearTurns: () => void

  // 会话标识
  sessionId: string
  setSessionId: (id: string) => void

  // 加载状态
  loading: boolean
  setLoading: (loading: boolean) => void
  streaming: boolean
  setStreaming: (streaming: boolean) => void

  // 人格
  personaId: string
  setPersonaId: (id: string) => void

  // 危机横幅
  crisisHit: boolean
  setCrisisHit: (hit: boolean) => void

  // 当前 Agent（UI 徽标）
  currentAgent: string | undefined
  setCurrentAgent: (agent: string | undefined) => void
}

export const useChatStore = create<ChatState>()(
  persist(
    (set) => ({
      turns: [],
      setTurns: (turns) => set({ turns }),
      appendTurn: (turn) => set((s) => ({ turns: [...s.turns, turn] })),
      updateLastAssistant: (content) =>
        set((s) => {
          const turns = [...s.turns]
          if (turns.length && turns[turns.length - 1].role === 'assistant') {
            turns[turns.length - 1] = { ...turns[turns.length - 1], content }
          }
          return { turns }
        }),
      appendToLastAssistant: (delta) =>
        set((s) => {
          const turns = [...s.turns]
          if (turns.length && turns[turns.length - 1].role === 'assistant') {
            const last = turns[turns.length - 1]
            turns[turns.length - 1] = { ...last, content: last.content + delta }
          }
          return { turns }
        }),
      updateLastAssistantMeta: (meta) =>
        set((s) => {
          const turns = [...s.turns]
          if (turns.length && turns[turns.length - 1].role === 'assistant') {
            turns[turns.length - 1] = { ...turns[turns.length - 1], ...meta }
          }
          return { turns }
        }),
      clearTurns: () => set({ turns: [] }),

      sessionId: '',
      setSessionId: (id) => set({ sessionId: id }),

      loading: false,
      setLoading: (loading) => set({ loading }),
      streaming: false,
      setStreaming: (streaming) => set({ streaming }),

      personaId: 'default',
      setPersonaId: (id) => set({ personaId: id }),

      crisisHit: false,
      setCrisisHit: (hit) => set({ crisisHit: hit }),

      currentAgent: undefined,
      setCurrentAgent: (agent) => set({ currentAgent: agent }),
    }),
    {
      name: 'psycheflow-chat',
      partialize: (state) => ({
        sessionId: state.sessionId,
        personaId: state.personaId,
      }),
    }
  )
)
