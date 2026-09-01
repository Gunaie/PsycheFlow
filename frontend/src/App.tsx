import { Link, Route, Routes } from 'react-router-dom'
import HomePage from './pages/HomePage'
import ScalePage from './pages/ScalePage'
import ChatPage from './pages/ChatPage'

export default function App() {
  return (
    <div className="min-h-screen flex flex-col bg-slate-50">
      <header className="bg-white border-b border-slate-200">
        <div className="max-w-3xl mx-auto px-4 py-3 flex items-center gap-6">
          <Link to="/" className="text-lg font-bold text-primary-600">PsycheFlow</Link>
          <nav className="flex gap-4 text-sm">
            <Link to="/" className="text-slate-600 hover:text-primary-600">首页</Link>
            <Link to="/chat" className="text-slate-600 hover:text-primary-600">对话</Link>
          </nav>
        </div>
      </header>

      <main className="flex-1 max-w-3xl w-full mx-auto px-4 py-6">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/scales/:scaleId" element={<ScalePage />} />
          <Route path="/chat" element={<ChatPage />} />
        </Routes>
      </main>

      <footer className="text-center text-xs text-slate-400 py-6">
        非医疗器械，非替代专业诊疗。
      </footer>
    </div>
  )
}
