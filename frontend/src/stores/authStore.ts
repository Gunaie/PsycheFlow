import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface AuthState {
  token: string
  role: 'student' | 'teacher' | ''
  label: string
  accountId: string
  setAuth: (token: string, role: 'student' | 'teacher', label: string, accountId: string) => void
  clearAuth: () => void
  isAuthed: () => boolean
  isTeacher: () => boolean
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: '',
      role: '',
      label: '',
      accountId: '',
      setAuth: (token, role, label, accountId) => set({ token, role, label, accountId }),
      clearAuth: () => set({ token: '', role: '', label: '', accountId: '' }),
      isAuthed: () => !!get().token,
      isTeacher: () => get().role === 'teacher',
    }),
    {
      name: 'psycheflow-auth',
    }
  )
)
