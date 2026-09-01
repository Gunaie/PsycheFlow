import { Link, Navigate, Route, Routes } from 'react-router-dom'
import HomePage from './pages/HomePage'
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
  return (
    <div className="min-h-screen flex flex-col bg-slate-50">
      <header className="bg-white border-b border-slate-200">
        <div className="max-w-3xl mx-auto px-4 py-3 flex items-center gap-6">
          <Link to="/" className="text-lg font-bold text-primary-600">PsycheFlow</Link>
          <nav className="flex gap-4 text-sm">
            <Link to="/" className="text-slate-600 hover:text-primary-600">首页</Link>
            <Link to="/chat" className="text-slate-600 hover:text-primary-600">对话</Link>
            <Link to="/admin" className="text-slate-600 hover:text-primary-600">管理后台</Link>
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
  return (
    <div className="max-w-3xl w-full mx-auto px-4 py-6">
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/scale" element={<ScalePage />} />
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
