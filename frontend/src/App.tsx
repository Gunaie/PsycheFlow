import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { getRole } from './api'
import HomePage from './pages/HomePage'
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

/** C 端公共布局：顶栏 + 内容容器 + 免责 footer。 */
function Shell({ children }: { children: React.ReactNode }) {
  const location = useLocation()
  const path = location.pathname
  const role = getRole()

  const navItems = [
    { to: '/', label: '首页', active: path === '/' },
    { to: '/scale', label: '测评', active: path === '/scale' || path.startsWith('/scale/') || path.startsWith('/scales') || path === '/screening' },
    { to: '/chat', label: '对话', active: path === '/chat' },
    { to: '/history', label: '历史', active: path === '/history' },
  ]

  return (
    <div className="min-h-screen flex flex-col bg-slate-50">
      <header className="bg-white border-b border-slate-200">
        <div className="max-w-3xl mx-auto px-4 py-3 flex items-center gap-6">
          <Link to="/" className="text-lg font-bold text-primary-600 shrink-0">PsycheFlow</Link>
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
            {role === 'teacher' && (
              <Link
                to="/admin"
                className={`px-2.5 py-1 rounded-md transition ${path.startsWith('/admin') ? 'bg-primary-50 text-primary-700 font-semibold' : 'text-slate-600 hover:text-primary-600 hover:bg-slate-50'}`}
              >
                管理后台
              </Link>
            )}
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
        <Route path="/" element={<HomePage />} />
        <Route path="/scale" element={<ScaleSelectPage />} />
        <Route path="/scale/combined" element={<ScalePage />} />
        <Route path="/scales/:scaleId" element={<ScalePage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/history" element={<HistoryPage />} />
        <Route path="/screening" element={<ScreeningPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  )
}

/** B 端管理后台路由（自带全屏布局，不走公共 Shell）。 */
function AdminPages() {
  return (
    <Routes>
      <Route path="/" element={<AdminBatchesPage />} />
      <Route path="/login" element={<AdminLoginPage />} />
      <Route path="/batches/:batchId" element={<AdminBatchDetailPage />} />
      <Route path="*" element={<Navigate to="/admin" replace />} />
    </Routes>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/admin/*" element={<AdminPages />} />
      <Route path="*" element={<Shell><StudentPages /></Shell>} />
    </Routes>
  )
}
