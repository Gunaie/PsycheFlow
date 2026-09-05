import type { SyntheticEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { clearToken, getLabel, getRole, getToken } from '../api'

/** 缺图兜底：隐藏图片并显示同位置的 emoji 后备元素。 */
function hideImgShowFallback(e: SyntheticEvent<HTMLImageElement>) {
  const el = e.currentTarget
  el.style.display = 'none'
  const fb = el.nextElementSibling as HTMLElement | null
  if (fb) fb.classList.remove('hidden')
}

/**
 * 系统门户（/）：双端统一大门 + 身份换乘站。
 * - 未登录：双卡选择入口；
 * - 已登录：顶部显示当前身份与"进入工作台"，点击另一端卡片 = 明确的切换身份动作
 *   （教师 → 学生端会先退出登录；学生 → 教师端进入教师登录页校验角色）。
 * 独立全屏布局，不套学生 Shell（门户是大门，不是房间）。
 */
export default function PortalPage() {
  const navigate = useNavigate()
  const token = getToken()
  const role = getRole()
  const label = getLabel()
  const isTeacher = role === 'teacher'

  const goStudent = () => {
    if (token && isTeacher) {
      if (confirm(`将退出教师账号「${label || '当前用户'}」并进入学生端，确定？`)) {
        clearToken()
        navigate('/home')
      }
      return
    }
    navigate('/home')
  }

  const goTeacher = () => {
    navigate(token && !isTeacher ? '/admin/login' : '/admin')
  }

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col">
      <div className="flex-1 flex flex-col justify-center max-w-3xl w-full mx-auto px-4 py-12 space-y-8">
        {/* 顶部横幅（缺图自动隐藏整块） */}
        <div
          className="relative rounded-2xl overflow-hidden h-44 sm:h-56 shadow-md"
          id="portal-banner"
        >
          <img
            src="/images/portal-banner.png"
            alt="校园心理健康插画"
            className="absolute inset-0 w-full h-full object-cover"
            onError={e => {
              const wrap = e.currentTarget.parentElement
              if (wrap) wrap.style.display = 'none'
            }}
          />
          <div className="absolute inset-0 bg-gradient-to-t from-[#1e3a5f]/40 to-transparent" />
        </div>

        <div className="text-center">
          <h1 className="text-3xl font-bold text-[#1e3a5f] tracking-tight">PsycheFlow 智能心理评估系统</h1>
          <p className="text-sm text-slate-500 mt-2">青少年校园心理筛查 · 量表评估与开放对话</p>
          {token && (
            <p className="inline-flex items-center gap-2 mt-4 text-xs bg-white border border-slate-200 rounded-full px-4 py-1.5 text-slate-600">
              当前身份：<span className="font-semibold text-[#1e3a5f]">{label || '用户'}</span>
              （{isTeacher ? '教师' : '学生'}）
              <button
                onClick={() => navigate(isTeacher ? '/admin' : '/home')}
                className="text-[#1e3a5f] font-semibold hover:underline"
              >
                进入我的工作台 →
              </button>
            </p>
          )}
        </div>

        <div className="grid gap-5 sm:grid-cols-2">
          <button
            type="button"
            onClick={goStudent}
            className="group bg-white rounded-2xl border border-slate-200 overflow-hidden hover:border-[#1e3a5f] hover:shadow-lg transition flex flex-col text-left cursor-pointer"
          >
            <img
              src="/images/portal-student.png"
              alt="学生使用平板完成心理测评插画"
              className="h-36 w-full object-cover"
              onError={hideImgShowFallback}
            />
            <div className="hidden h-36 w-full bg-blue-50 flex items-center justify-center text-4xl">🎓</div>
            <div className="p-6 flex flex-col flex-1">
              <h2 className="text-lg font-bold text-slate-800 mb-1.5">学生端</h2>
              <p className="text-sm text-slate-500 leading-relaxed flex-1">
                量表测评（PHQ-A · SCARED · SDQ · MHT）、AI 陪伴对话、历史报告查看与下载
              </p>
              {token && isTeacher && (
                <span className="text-xs text-amber-600 mt-1">切换将退出当前教师账号</span>
              )}
              <span className="text-sm text-[#1e3a5f] font-semibold mt-5 group-hover:underline">
                {token && !isTeacher ? '返回学生端 →' : '进入学生端 →'}
              </span>
            </div>
          </button>

          <button
            type="button"
            onClick={goTeacher}
            className="group bg-white rounded-2xl border border-slate-200 overflow-hidden hover:border-[#1e3a5f] hover:shadow-lg transition flex flex-col text-left cursor-pointer"
          >
            <img
              src="/images/portal-teacher.png"
              alt="教师查看班级心理数据插画"
              className="h-36 w-full object-cover"
              onError={hideImgShowFallback}
            />
            <div className="hidden h-36 w-full bg-slate-100 flex items-center justify-center text-4xl">🏫</div>
            <div className="p-6 flex flex-col flex-1">
              <h2 className="text-lg font-bold text-slate-800 mb-1.5">教师端</h2>
              <p className="text-sm text-slate-500 leading-relaxed flex-1">
                创建筛查批次、发放筛查码、查看班级报告与危机名单
              </p>
              <span className="text-xs text-slate-400 mt-1">
                {token && isTeacher ? '使用当前教师账号' : '需教师账号'}
              </span>
              <span className="text-sm text-[#1e3a5f] font-semibold mt-3 group-hover:underline">
                {token && isTeacher ? '返回教师端 →' : '进入教师端 →'}
              </span>
            </div>
          </button>
        </div>

        <p className="text-center text-xs text-slate-400">
          非医疗器械，非替代专业诊疗。如遇危机请拨打 12355 青少年服务热线。
        </p>
      </div>
    </div>
  )
}
