import { useNavigate } from 'react-router-dom'
import { Link } from 'react-router-dom'
import { getToken, getLabel, clearToken } from '../api'
import BackLink from '../components/BackLink'
import FooterDisclaimer from '../components/FooterDisclaimer'

const HERO_IMG = '/index.png'

const FEATURES = [
  {
    to: '/assess',
    icon: '📋',
    title: '量表测评',
    desc: 'PHQ-A · SCARED · SDQ · MHT 四套专业量表，在线作答即时出分',
    cta: '开始测评',
  },
  {
    to: '/chat',
    icon: '💬',
    title: 'AI 对话',
    desc: '与 PsycheFlow 陪伴助手开放对话，倾诉近况、获取支持建议',
    cta: '开始对话',
  },
  {
    to: '/history',
    icon: '📊',
    title: '历史报告',
    desc: '查看历次测评记录与分数趋势，一键下载 PDF 报告',
    cta: '查看报告',
  },
  {
    to: '/screening',
    icon: '🎫',
    title: '班级筛查作答',
    desc: '输入老师发放的 6 位筛查码，免注册匿名完成班级批次测评',
    cta: '输入筛查码',
  },
]

export default function HomePage() {
  const navigate = useNavigate()
  const token = getToken()
  const label = getLabel()

  return (
    <div className="space-y-10">
      {/* 返回系统门户（门户是总大门：换端/切换身份从这里走） */}
      <div>
        <BackLink to="/">返回门户</BackLink>
      </div>

      {/* Hero 区 */}
      <section className="relative overflow-hidden rounded-3xl shadow-lg">
        <img
          src={HERO_IMG}
          alt="心理健康插画"
          className="absolute inset-0 w-full h-full object-cover"
        />
        <div className="absolute inset-0 bg-gradient-to-r from-[#1e3a5f]/90 via-[#1e3a5f]/70 to-transparent" />
        <div className="relative px-7 py-12 sm:px-10 sm:py-16">
          <h1 className="text-3xl font-bold text-white tracking-tight">智能心理评估系统</h1>
          <p className="text-base text-blue-100 mt-2 max-w-md leading-relaxed">
            青少年校园心理筛查 · 量表评估与开放对话
          </p>
          <div className="mt-7 flex flex-wrap gap-3">
            {token ? (
              <>
                <Link
                  to="/assess"
                  className="rounded-xl bg-white px-5 py-2.5 text-sm font-semibold text-[#1e3a5f] hover:bg-blue-50 transition"
                >
                  开始测评 →
                </Link>
                <Link
                  to="/chat"
                  className="rounded-xl border border-white/40 px-5 py-2.5 text-sm font-semibold text-white hover:bg-white/10 transition"
                >
                  AI 对话
                </Link>
              </>
            ) : (
              <>
                <button
                  onClick={() => navigate('/login')}
                  className="rounded-xl bg-white px-5 py-2.5 text-sm font-semibold text-[#1e3a5f] hover:bg-blue-50 transition"
                >
                  登录
                </button>
                <button
                  onClick={() => navigate('/register')}
                  className="rounded-xl border border-white/40 px-5 py-2.5 text-sm font-semibold text-white hover:bg-white/10 transition"
                >
                  注册
                </button>
              </>
            )}
          </div>
        </div>
      </section>

      {/* 用户问候 */}
      {token && (
        <div className="flex items-center justify-between flex-wrap gap-3">
          <span className="text-slate-600 text-sm">
            您好，<span className="font-semibold text-slate-800">{label || '匿名用户'}</span>，欢迎回来
          </span>
          <button
            onClick={() => { clearToken(); navigate('/') }}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50 transition"
          >
            退出登录
          </button>
        </div>
      )}

      {/* 功能卡片 */}
      <section>
        <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {FEATURES.map((f) => (
            <Link
              key={f.to}
              to={f.to}
              className="group bg-white rounded-2xl border border-slate-200 p-6 hover:border-[#1e3a5f] hover:shadow-md transition flex flex-col"
            >
              <div className="w-12 h-12 rounded-xl bg-blue-50 group-hover:bg-[#1e3a5f]/5 flex items-center justify-center text-2xl mb-4 transition">
                {f.icon}
              </div>
              <h3 className="text-base font-bold text-slate-800 mb-1.5">{f.title}</h3>
              <p className="text-sm text-slate-500 leading-relaxed flex-1">{f.desc}</p>
              <span className="text-sm text-[#1e3a5f] font-medium mt-4 group-hover:underline">
                {f.cta} →
              </span>
            </Link>
          ))}
        </div>
      </section>

      {/* 关于系统 */}
      <section className="bg-white rounded-2xl border border-slate-200 p-6">
        <h2 className="text-base font-bold text-slate-800 mb-3">关于系统</h2>
        <p className="text-sm text-slate-500 leading-relaxed">
          PsycheFlow 为校园心理健康筛查辅助工具，提供抑郁、焦虑、长处与困难及综合心理健康诊断四套标准化量表评估，
          并结合 AI 陪伴对话为青少年提供即时支持。系统非医疗器械，不替代专业诊疗与诊断。
          如遇危机信号，请拨打 <span className="font-semibold text-[#1e3a5f]">12355</span> 青少年服务热线。
        </p>
      </section>

      <FooterDisclaimer />
    </div>
  )
}
