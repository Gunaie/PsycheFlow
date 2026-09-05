import type { ReactNode } from 'react'
import { Link, Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { getRole, getToken } from './api'
import HomePage from './pages/HomePage'
import PortalPage from './pages/PortalPage'
import ScaleSelectPage from './pages/ScaleSelectPage'
import ScalePage from './pages/ScalePage'
import ChatPage from './pages/ChatPage'
import RegisterPage from './pages/RegisterPage'
import LoginPage from './pages/LoginPage'
import HistoryPage from './pages/HistoryPage'
import ScreeningPage from './pages/ScreeningPage'
import AdminBatchesPage from './pages/admin/AdminBatchesPage'
import AdminBatchDetailPage from './pages/admin/AdminBatchDetailPage'
import AdminLoginPage from './pages/admin/AdminLoginPage'

/** 旧路径 /scales/:scaleId → /assess/:scaleId 兼容重定向（Navigate 不支持参数插值，需转发参数）。 */
function LegacyScaleRedirect() {
  const { scaleId } = useParams()
  return <Navigate to={`/assess/${scaleId}`} replace />
}

/** 路由守卫：教师身份才能进管理后台（服务端 admin API 仍有真实鉴权兜底）。 */
function RequireTeacher({ children }: { children: ReactNode }) {
  if (!getToken() || getRole() !== 'teacher') return <Navigate to="/admin/login" replace />
  return <>{children}</>
}

/** 路由守卫：学生世界禁止教师会话进入——教师手动访问学生路由一律弹回管理后台。 */
function RequireStudent({ children }: { children: ReactNode }) {
  if (getToken() && getRole() === 'teacher') return <Navigate to="/admin" replace />
  return <>{children}</>
}

/** 已登录用户访问登录/注册页 → 按角色送回各自首页，防止两端交叉。 */
function RedirectIfAuthed({ children }: { children: ReactNode }) {
  if (getToken()) return <Navigate to={getRole() === 'teacher' ? '/admin' : '/home'} replace />
  return <>{children}</>
}

/** C 端公共布局：顶栏 + 内容容器 + 免责 footer。 */
function Shell({ children }: { children: ReactNode }) {
  const location = useLocation()
  const path = location.pathname
  const role = getRole()

  // 顶栏按角色分化：教师只保留 管理后台（学生功能不做导航暴露）；学生为四个常规入口
  const navItems =
    role === 'teacher'
      ? [
          { to: '/admin', label: '管理后台', active: path.startsWith('/admin') },
        ]
      : [
          { to: '/home', label: '首页', active: path === '/home' },
          { to: '/assess', label: '测评', active: path === '/assess' || path.startsWith('/assess/') },
          { to: '/chat', label: '对话', active: path === '/chat' },
          { to: '/history', label: '历史', active: path === '/history' },
        ]

  return (
    <div className="min-h-screen flex flex-col bg-slate-50">
      <header className="bg-white border-b border-slate-200">
        <div className="max-w-3xl mx-auto px-4 py-3 flex items-center gap-6">
          <Link to="/home" className="text-lg font-bold text-primary-600 shrink-0">PsycheFlow</Link>
          <nav className="flex gap-1 text-sm">
            {navItems.map(item => (
              <Link
                key={item.to}
                to={item.to}
                className={`px-2.5 py-1 rounded-md transition ${item.active ? 'bg-primary-50 text-primary-700 font-semibold' : 'text-slate-600 hover:text-primary-600 hover:bg-slate-50'}`}
              >
                {item.label}
              </Link>
            ))}
          </nav>
        </div>
      </header>

      <main className="flex-1 w-full">{children}</main>

      <footer className="text-center text-xs text-slate-400 py-6">
        非医疗器械，非替代专业诊疗。
      </footer>
    </div>
  )
}

/** C 端页面路由（包在 max-w-3xl 容器里，与原布局一致）。 */
function StudentPages() {
  const location = useLocation()
  const wide = location.pathname === '/chat'
  return (
    <div className={`${wide ? 'max-w-5xl' : 'max-w-3xl'} w-full mx-auto px-4 py-6`}>
      <Routes>
        <Route path="/home" element={<RequireStudent><HomePage /></RequireStudent>} />
        <Route path="/assess" element={<RequireStudent><ScaleSelectPage /></RequireStudent>} />
        <Route path="/assess/combined" element={<RequireStudent><ScalePage /></RequireStudent>} />
        <Route path="/assess/:scaleId" element={<RequireStudent><ScalePage /></RequireStudent>} />
        <Route path="/chat" element={<RequireStudent><ChatPage /></RequireStudent>} />
        <Route path="/register" element={<RedirectIfAuthed><RegisterPage /></RedirectIfAuthed>} />
        <Route path="/login" element={<RedirectIfAuthed><LoginPage /></RedirectIfAuthed>} />
        <Route
          path="/history"
          element={
            <RequireStudent>
              {getToken() ? <HistoryPage /> : <Navigate to="/login" replace />}
            </RequireStudent>
          }
        />
        <Route path="/screening" element={<RequireStudent><ScreeningPage /></RequireStudent>} />
        {/* 旧路径兼容重定向 */}
        <Route path="/scale/combined" element={<Navigate to="/assess/combined" replace />} />
        <Route path="/scale" element={<Navigate to="/assess" replace />} />
        <Route path="/scales/:scaleId" element={<LegacyScaleRedirect />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  )
}

/** B 端管理后台路由（AdminShell 布局，不走公共 Shell）。 */
function AdminPages() {
  return (
    <Routes>
      <Route path="/login" element={<AdminLoginPage />} />
      <Route path="/" element={<RequireTeacher><AdminBatchesPage /></RequireTeacher>} />
      <Route path="/batches/:batchId" element={<RequireTeacher><AdminBatchDetailPage /></RequireTeacher>} />
      <Route path="*" element={<Navigate to="/admin" replace />} />
    </Routes>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/admin/*" element={<AdminPages />} />
      {/* 系统门户：未登录选身份，已登录按角色自动跳转（独立布局，不套学生 Shell） */}
      <Route path="/" element={<PortalPage />} />
      <Route path="*" element={<Shell><StudentPages /></Shell>} />
    </Routes>
  )
}
