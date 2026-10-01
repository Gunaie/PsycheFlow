import { describe, it, expect, beforeEach } from 'vitest'
import { useAuthStore } from './authStore'

describe('authStore', () => {
  beforeEach(() => {
    useAuthStore.setState({ token: '', role: '', label: '', accountId: '' })
  })

  it('setAuth / clearAuth 正确更新状态', () => {
    useAuthStore.getState().setAuth('t', 'student', '张三', 'acc-1')
    const s = useAuthStore.getState()
    expect(s.token).toBe('t')
    expect(s.role).toBe('student')
    expect(s.label).toBe('张三')
    expect(s.accountId).toBe('acc-1')
    expect(s.isAuthed()).toBe(true)
    expect(s.isTeacher()).toBe(false)

    s.clearAuth()
    expect(useAuthStore.getState().isAuthed()).toBe(false)
  })

  it('教师角色识别', () => {
    useAuthStore.getState().setAuth('t', 'teacher', '老师', 'acc-2')
    expect(useAuthStore.getState().isTeacher()).toBe(true)
  })
})
