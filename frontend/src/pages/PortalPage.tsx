import { Link, Navigate } from 'react-router-dom'
import { getRole, getToken } from '../api'

/**
 * 系统门户（/）：双端统一大门。
 * - 未登录：显示学生端 / 教师端两张入口卡；
 * - 已登录学生 → 自动进 /home；已登录教师 → 自动进 /admin（角色记忆，日常零多余点击）。
 * 独立全屏布局，不套学生 Shell（门户是大门，不是房间）。
 */
export default function PortalPage() {
  const token = getToken()
  const role = getRole()

  if (token && role === 'teacher') return <Navigate to="/admin" replace />
  if (token) return <Navigate to="/home" replace />

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col">
      <div className="flex-1 flex flex-col justify-center max-w-3xl w-full mx-auto px-4 py-12 space-y-8">
        <div className="text-center">
          <h1 className="text-3xl font-bold text-[#1e3a5f] tracking-tight">PsycheFlow 智能心理评估系统</h1>
          <p className="text-sm text-slate-500 mt-2">青少年校园心理筛查 · 量表评估与开放对话</p>
        </div>

        <div className="grid gap-5 sm:grid-cols-2">
          <Link
            to="/home"
            className="group bg-white rounded-2xl border border-slate-200 p-7 hover:border-[#1e3a5f] hover:shadow-lg transition flex flex-col"
          >
            <div className="w-14 h-14 rounded-2xl bg-blue-50 flex items-center justify-center text-3xl mb-5">
              🎓
            </div>
            <h2 className="text-lg font-bold text-slate-800 mb-1.5">学生端</h2>
            <p className="text-sm text-slate-500 leading-relaxed flex-1">
              量表测评（PHQ-A · SCARED · SDQ · MHT）、AI 陪伴对话、历史报告查看与下载
            </p>
            <span className="text-sm text-[#1e3a5f] font-semibold mt-5 group-hover:underline">
              进入学生端 →
            </span>
          </Link>

          <Link
            to="/admin"
            className="group bg-white rounded-2xl border border-slate-200 p-7 hover:border-[#1e3a5f] hover:shadow-lg transition flex flex-col"
          >
            <div className="w-14 h-14 rounded-2xl bg-slate-100 flex items-center justify-center text-3xl mb-5">
              🏫
            </div>
            <h2 className="text-lg font-bold text-slate-800 mb-1.5">教师端</h2>
            <p className="text-sm text-slate-500 leading-relaxed flex-1">
              创建筛查批次、发放筛查码、查看班级报告与危机名单
            </p>
            <span className="text-xs text-slate-400 mt-1">需教师账号</span>
            <span className="text-sm text-[#1e3a5f] font-semibold mt-3 group-hover:underline">
              进入教师端 →
            </span>
          </Link>
        </div>

        <p className="text-center text-xs text-slate-400">
          非医疗器械，非替代专业诊疗。如遇危机请拨打 12355 青少年服务热线。
        </p>
      </div>
    </div>
  )
}
