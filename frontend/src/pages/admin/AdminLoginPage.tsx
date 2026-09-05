import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiPost, setToken } from '../../api';
import BackLink from '../../components/BackLink';

interface AuthResp {
  token: string;
  label: string;
  role: string;
}

type TabKey = 'login' | 'register';

export default function AdminLoginPage() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<TabKey>('login');
  const [label, setLabel] = useState('');
  const [password, setPassword] = useState('');
  const [password2, setPassword2] = useState('');
  const [authorized, setAuthorized] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleLogin = async () => {
    if (!label.trim() || !password || loading) return;
    setLoading(true);
    setError(null);
    try {
      const res = await apiPost<AuthResp>('/api/auth/login_by_password', {
        label: label.trim(),
        password,
      });
      if (res.role !== 'teacher') {
        setError('该账号不是教师账号，请从学生端登录');
        return;
      }
      setToken(res.token, res.label, res.role);
      navigate('/admin');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async () => {
    if (!label.trim() || !password || !authorized || loading) return;
    if (password !== password2) {
      setError('两次输入的密码不一致');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await apiPost<AuthResp>('/api/auth/register', {
        consents: { tool: true, guardian: true, privacy14: true, crisis: true },
        role: 'teacher',
        label: label.trim(),
        password,
      });
      setToken(res.token, res.label, res.role);
      navigate('/admin');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const canSubmit =
    tab === 'login'
      ? label.trim() && password
      : label.trim() && password.length >= 6 && password === password2 && authorized;

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="bg-[#1e3a5f] text-white">
        <div className="max-w-3xl mx-auto px-6 py-5 flex items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold">PsycheFlow · 学校心理筛查管理后台</h1>
            <p className="text-sm text-slate-200 mt-1">教师账号登录 / 注册</p>
          </div>
          <BackLink to="/" variant="dark">返回首页</BackLink>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-8">
        <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
          <div className="flex gap-4 border-b border-slate-200 mb-6">
            <button
              onClick={() => { setTab('login'); setError(null); }}
              className={`pb-3 px-1 text-sm font-medium border-b-2 transition ${
                tab === 'login'
                  ? 'border-[#1e3a5f] text-[#1e3a5f]'
                  : 'border-transparent text-slate-500 hover:text-slate-700'
              }`}
            >
              登录
            </button>
            <button
              onClick={() => { setTab('register'); setError(null); }}
              className={`pb-3 px-1 text-sm font-medium border-b-2 transition ${
                tab === 'register'
                  ? 'border-[#1e3a5f] text-[#1e3a5f]'
                  : 'border-transparent text-slate-500 hover:text-slate-700'
              }`}
            >
              注册教师账号
            </button>
          </div>

          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">账号名</label>
              <input
                type="text"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="3-64 个字符，如 wangls"
                className="w-full rounded-md border border-slate-300 px-3 py-2.5 text-sm focus:outline-none focus:border-[#1e3a5f]"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">密码</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && tab === 'login') handleLogin(); }}
                placeholder={tab === 'register' ? '至少 6 位' : '输入密码'}
                className="w-full rounded-md border border-slate-300 px-3 py-2.5 text-sm focus:outline-none focus:border-[#1e3a5f]"
              />
            </div>
            {tab === 'register' && (
              <>
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">确认密码</label>
                  <input
                    type="password"
                    value={password2}
                    onChange={(e) => setPassword2(e.target.value)}
                    className="w-full rounded-md border border-slate-300 px-3 py-2.5 text-sm focus:outline-none focus:border-[#1e3a5f]"
                  />
                </div>
                <label className="flex items-start gap-2 text-xs text-slate-600 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={authorized}
                    onChange={(e) => setAuthorized(e.target.checked)}
                    className="mt-0.5"
                  />
                  <span>
                    我确认本系统仅用于校园心理筛查辅助（非医疗器械、非替代专业诊疗），
                    已获得学校/监护人授权，将遵守未成年人隐私保护与危机转介规范（学校心理老师 → 家长 → 12355）。
                  </span>
                </label>
              </>
            )}

            <button
              onClick={tab === 'login' ? handleLogin : handleRegister}
              disabled={!canSubmit || loading}
              className={`w-full rounded-md py-2.5 font-semibold text-white text-sm ${
                canSubmit && !loading
                  ? 'bg-[#1e3a5f] hover:opacity-95'
                  : 'bg-slate-300 cursor-not-allowed'
              }`}
            >
              {loading ? '处理中...' : tab === 'login' ? '登录' : '注册并登录'}
            </button>
          </div>

          {error && <div className="text-red-600 text-sm mt-4">{error}</div>}
        </div>
      </main>
    </div>
  );
}
