import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiPost, setToken } from '../api';
import FooterDisclaimer from '../components/FooterDisclaimer';

type TabKey = 'token' | 'label';

interface LoginTokenResponse {
  token: string;
  label: string;
  role: string;
}

interface LoginLabelResponse {
  token: string;
  label: string;
  role: string;
}

export default function LoginPage() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<TabKey>('token');
  const [tokenInput, setTokenInput] = useState('');
  const [labelInput, setLabelInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleLoginByToken = async () => {
    if (!tokenInput.trim() || loading) return;
    setLoading(true);
    setError(null);
    try {
      const res = await apiPost<LoginTokenResponse>('/api/auth/login_by_token', {
        token: tokenInput.trim(),
      });
      setToken(res.token, res.label, res.role);
      navigate('/');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const handleLoginByLabel = async () => {
    if (!labelInput.trim() || loading) return;
    setLoading(true);
    setError(null);
    try {
      const res = await apiPost<LoginLabelResponse>('/api/auth/login_by_label', {
        label: labelInput.trim(),
      });
      setToken(res.token, res.label, res.role);
      navigate('/');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="bg-[#1e3a5f] text-white">
        <div className="max-w-3xl mx-auto px-6 py-5">
          <h1 className="text-2xl font-bold">PsycheFlow · 登录</h1>
          <p className="text-sm text-slate-200 mt-1">
            使用注册时获得的 Token 或 Label 登录
          </p>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-8">
        <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
          <div className="flex gap-4 border-b border-slate-200 mb-6">
            <button
              onClick={() => {
                setTab('token');
                setError(null);
              }}
              className={`pb-3 px-1 text-sm font-medium border-b-2 transition ${
                tab === 'token'
                  ? 'border-[#1e3a5f] text-[#1e3a5f]'
                  : 'border-transparent text-slate-500 hover:text-slate-700'
              }`}
            >
              按 Token 登录
            </button>
            <button
              onClick={() => {
                setTab('label');
                setError(null);
              }}
              className={`pb-3 px-1 text-sm font-medium border-b-2 transition ${
                tab === 'label'
                  ? 'border-[#1e3a5f] text-[#1e3a5f]'
                  : 'border-transparent text-slate-500 hover:text-slate-700'
              }`}
            >
              按标签 Label 登录
            </button>
          </div>

          {tab === 'token' ? (
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">
                  访问 Token
                </label>
                <input
                  type="text"
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleLoginByToken();
                  }}
                  placeholder="粘贴 32 位 Token"
                  className="w-full rounded-md border border-slate-300 px-3 py-2.5 text-sm font-mono focus:outline-none focus:border-[#1e3a5f]"
                />
              </div>
              <button
                onClick={handleLoginByToken}
                disabled={!tokenInput.trim() || loading}
                className={`w-full rounded-md py-2.5 font-semibold text-white text-sm ${
                  tokenInput.trim() && !loading
                    ? 'bg-[#1e3a5f] hover:opacity-95'
                    : 'bg-slate-300 cursor-not-allowed'
                }`}
              >
                {loading ? '登录中...' : '登录'}
              </button>
            </div>
          ) : (
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1.5">
                  账号 Label
                </label>
                <input
                  type="text"
                  value={labelInput}
                  onChange={(e) => setLabelInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleLoginByLabel();
                  }}
                  placeholder="输入账号 label，如 ceshi-ab12"
                  className="w-full rounded-md border border-slate-300 px-3 py-2.5 text-sm focus:outline-none focus:border-[#1e3a5f]"
                />
              </div>
              <button
                onClick={handleLoginByLabel}
                disabled={!labelInput.trim() || loading}
                className={`w-full rounded-md py-2.5 font-semibold text-white text-sm ${
                  labelInput.trim() && !loading
                    ? 'bg-[#1e3a5f] hover:opacity-95'
                    : 'bg-slate-300 cursor-not-allowed'
                }`}
              >
                {loading ? '登录中...' : '登录'}
              </button>
            </div>
          )}

          {error && (
            <div className="text-red-600 text-sm mt-4">
              ❌ {error}
            </div>
          )}
        </div>

        <FooterDisclaimer />
      </main>
    </div>
  );
}
