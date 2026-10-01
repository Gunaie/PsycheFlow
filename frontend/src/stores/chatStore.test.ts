import { describe, it, expect, beforeEach } from 'vitest'
import { useChatStore } from './chatStore'

describe('chatStore', () => {
  beforeEach(() => {
    useChatStore.setState(useChatStore.getInitialState())
  })

  it('初始状态正确', () => {
    const s = useChatStore.getState()
    expect(s.turns).toEqual([])
    expect(s.loading).toBe(false)
    expect(s.streaming).toBe(false)
    expect(s.crisisHit).toBe(false)
    expect(s.currentAgent).toBeUndefined()
    expect(s.personaId).toBe('default')
    expect(s.sessionId).toBe('')
  })

  it('appendTurn 追加对话轮', () => {
    useChatStore.getState().appendTurn({ role: 'user', content: '你好' })
    expect(useChatStore.getState().turns).toHaveLength(1)
    useChatStore.getState().appendTurn({ role: 'assistant', content: '嗨' })
    expect(useChatStore.getState().turns).toHaveLength(2)
  })

  it('updateLastAssistant 覆盖最后 assistant 内容', () => {
    const st = useChatStore.getState()
    st.appendTurn({ role: 'user', content: 'q' })
    st.appendTurn({ role: 'assistant', content: 'a1' })
    st.updateLastAssistant('a2')
    expect(useChatStore.getState().turns[1].content).toBe('a2')
  })

  it('appendToLastAssistant 追加 token', () => {
    const st = useChatStore.getState()
    st.appendTurn({ role: 'user', content: 'q' })
    st.appendTurn({ role: 'assistant', content: 'Hel' })
    st.appendToLastAssistant('lo')
    expect(useChatStore.getState().turns[1].content).toBe('Hello')
  })

  it('updateLastAssistantMeta 更新 agent/sources/content', () => {
    const st = useChatStore.getState()
    st.appendTurn({ role: 'assistant', content: '' })
    st.updateLastAssistantMeta({ agent: 'intervention', sources: [{ text: 'x', source: 'f' }] })
    const last = useChatStore.getState().turns[0]
    expect(last.agent).toBe('intervention')
    expect(last.sources).toHaveLength(1)
  })

  it('clearTurns 清空并保留其他状态', () => {
    const st = useChatStore.getState()
    st.appendTurn({ role: 'user', content: 'hi' })
    st.setCrisisHit(true)
    st.clearTurns()
    expect(useChatStore.getState().turns).toEqual([])
    expect(useChatStore.getState().crisisHit).toBe(true)
  })

  it('持久化只保留 sessionId 与 personaId', () => {
    const persisted = (useChatStore as any).persist
      .getOptions()
      .partialize(useChatStore.getState())
    expect(Object.keys(persisted)).toEqual(['sessionId', 'personaId'])
  })
})
