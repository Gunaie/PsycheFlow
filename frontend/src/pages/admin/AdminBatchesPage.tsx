import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiGet, apiPost, getToken } from '../../api';

interface BatchItem {
  batch_id: string;
  name: string;
  scale_ids: string[];
  status: string;
  total: number;
  completed: number;
  created_at: string;
}

interface EntryOut {
  entry_id: string;
  student_no: string;
  student_name: string;
  grade: string | null;
  klass: string | null;
  entry_code: string;
  status: string;
}

interface BatchCreateResp {
  batch_id: string;
  name: string;
  scale_ids: string[];
  total: number;
  entries: EntryOut[];
}

const CSV_TEMPLATE = '学号,姓名,年级,班级\nS001,张小明,高一,1班\nS002,李小红,高一,1班\nS003,王小刚,高一,2班';

interface ScaleOption {
  scale_id: string;
  scale_name: string;
  description: string;
  item_count: number;
}

export default function AdminBatchesPage() {
  const navigate = useNavigate();
  const [batches, setBatches] = useState<BatchItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [scaleOptions, setScaleOptions] = useState<ScaleOption[]>([]);

  // 创建表单
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState('');
  const [scales, setScales] = useState<string[]>(['phq_a']);
  const [rosterCsv, setRosterCsv] = useState('');
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<BatchCreateResp | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const fetchBatches = async () => {
    try {
      const res = await apiGet<{ items: BatchItem[] }>('/api/admin/batches');
      setBatches(res.items);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => {
    // 路由守卫：无 token 或非教师 → 跳登录
    if (!getToken()) {
      navigate('/admin/login');
      return;
    }
    setLoading(true);
    fetchBatches()
      .catch((e: Error) => {
        if (e.message.includes('401') || e.message.includes('403')) navigate('/admin/login');
        else setError(e.message);
      })
      .finally(() => setLoading(false));
    // 量表选项动态拉取（D1 量表库扩展：不再硬编码）
    apiGet<ScaleOption[]>('/api/scales')
      .then(setScaleOptions)
      .catch(() => {});
  }, [navigate]);

  const handleFile = (f: File | undefined) => {
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => setRosterCsv(String(reader.result || ''));
    reader.readAsText(f, 'utf-8');
  };

  const downloadTemplate = () => {
    const blob = new Blob(['\ufeff' + CSV_TEMPLATE], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'screening_roster_template.csv';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  };

  const toggleScale = (sid: string) => {
    setScales((s) => (s.includes(sid) ? s.filter((x) => x !== sid) : [...s, sid]));
  };

  const handleCreate = async () => {
    if (!name.trim() || scales.length === 0 || !rosterCsv.trim() || creating) return;
    setCreating(true);
    setError(null);
    try {
      const res = await apiPost<BatchCreateResp>('/api/admin/batches', {
        name: name.trim(),
        scale_ids: scales,
        roster_csv: rosterCsv,
      });
      setCreated(res);
      setShowCreate(false);
      setName(''); setRosterCsv(''); setScales(['phq_a']);
      fetchBatches();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setCreating(false);
    }
  };

  const copyCodes = async () => {
    if (!created) return;
    const lines = created.entries.map((e) => `${e.student_no} ${e.student_name} ${e.entry_code}`);
    try {
      await navigator.clipboard.writeText(lines.join('\n'));
      alert('筛查码名单已复制到剪贴板');
    } catch {
      alert('复制失败，请手动选择文本复制');
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 pb-10">
      <header className="bg-[#1e3a5f] text-white">
        <div className="max-w-7xl mx-auto px-4 py-4 flex justify-between items-center">
          <h1 className="text-lg font-bold">PsycheFlow · 筛查批次管理</h1>
          <div className="flex gap-2">
            <button
              onClick={() => setShowCreate((v) => !v)}
              className="rounded-md bg-white/10 border border-white/30 px-3 py-1.5 text-sm hover:bg-white/20"
            >
              {showCreate ? '收起创建' : '+ 创建批次'}
            </button>
            <button
              onClick={() => {
                localStorage.removeItem('psycheflow_token');
                localStorage.removeItem('psycheflow_label');
                localStorage.removeItem('psycheflow_role');
                navigate('/');
              }}
              className="rounded-md border border-white/30 px-3 py-1.5 text-sm hover:bg-white/10"
            >
              退出
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-6">
        {error && <div className="bg-red-50 text-red-600 text-sm p-3 rounded-lg">{error}</div>}

        {/* 创建批次表单 */}
        {showCreate && (
          <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4 shadow-sm">
            <h2 className="font-bold text-slate-800">创建筛查批次</h2>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">批次名称</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="如：高一 3 月心理健康普测"
                className="w-full max-w-lg rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:border-[#1e3a5f]"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1.5">施测量表（可多选）</label>
              <div className="flex flex-wrap gap-4">
                {scaleOptions.map((s) => (
                  <label key={s.scale_id} className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
                    <input type="checkbox" checked={scales.includes(s.scale_id)} onChange={() => toggleScale(s.scale_id)} />
                    {s.scale_name}
                    <span className="text-xs text-slate-400">（{s.item_count} 题）</span>
                  </label>
                ))}
              </div>
            </div>
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="block text-sm font-medium text-slate-700">
                  学生名单 CSV（列：学号,姓名,年级,班级）
                </label>
                <div className="flex gap-2">
                  <button onClick={downloadTemplate} className="text-xs text-[#1e3a5f] hover:underline">
                    下载模板
                  </button>
                  <button
                    onClick={() => fileRef.current?.click()}
                    className="text-xs text-[#1e3a5f] hover:underline"
                  >
                    上传文件
                  </button>
                  <input
                    ref={fileRef}
                    type="file"
                    accept=".csv,text/csv"
                    className="hidden"
                    onChange={(e) => handleFile(e.target.files?.[0])}
                  />
                </div>
              </div>
              <textarea
                value={rosterCsv}
                onChange={(e) => setRosterCsv(e.target.value)}
                rows={8}
                placeholder={CSV_TEMPLATE}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-xs font-mono focus:outline-none focus:border-[#1e3a5f]"
              />
            </div>
            <button
              onClick={handleCreate}
              disabled={!name.trim() || scales.length === 0 || !rosterCsv.trim() || creating}
              className={`rounded-md px-5 py-2 text-sm font-semibold text-white ${
                name.trim() && scales.length > 0 && rosterCsv.trim() && !creating
                  ? 'bg-[#1e3a5f] hover:opacity-95'
                  : 'bg-slate-300 cursor-not-allowed'
              }`}
            >
              {creating ? '创建中...' : '创建批次并生成筛查码'}
            </button>
          </div>
        )}

        {/* 创建成功：展示筛查码 */}
        {created && (
          <div className="bg-white rounded-xl border border-emerald-300 p-5 space-y-3 shadow-sm">
            <div className="flex justify-between items-center">
              <h2 className="font-bold text-slate-800">
                批次「{created.name}」创建成功 · 共 {created.total} 人
              </h2>
              <div className="flex gap-2">
                <button
                  onClick={copyCodes}
                  className="rounded-md border border-[#1e3a5f] px-3 py-1.5 text-xs text-[#1e3a5f] hover:bg-blue-50"
                >
                  复制筛查码名单
                </button>
                <button
                  onClick={() => setCreated(null)}
                  className="rounded-md border border-slate-300 px-3 py-1.5 text-xs text-slate-500 hover:bg-slate-50"
                >
                  关闭
                </button>
              </div>
            </div>
            <p className="text-xs text-slate-500">
              请将每位学生的筛查码发给学生。学生打开 <span className="font-mono">/screening</span> 页输入筛查码即可作答。
            </p>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 max-h-64 overflow-y-auto">
              {created.entries.map((e) => (
                <div key={e.entry_id} className="rounded-md border border-slate-200 p-2.5 text-center bg-slate-50">
                  <div className="text-[10px] text-slate-400">{e.student_no} {e.student_name}</div>
                  <div className="text-lg font-mono font-bold text-[#1e3a5f] tracking-widest">{e.entry_code}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 批次列表 */}
        {loading ? (
          <p className="text-center text-slate-500 py-10">加载中...</p>
        ) : batches.length === 0 ? (
          <div className="mt-16 text-center">
            <p className="text-slate-700 text-lg font-semibold mb-2">暂无筛查批次</p>
            <p className="text-slate-500 mb-6">点击右上角「创建批次」开始第一次批量筛查</p>
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {batches.map((b) => {
              const pct = b.total > 0 ? Math.round((b.completed / b.total) * 100) : 0;
              return (
                <button
                  key={b.batch_id}
                  onClick={() => navigate(`/admin/batches/${b.batch_id}`)}
                  className="text-left bg-white rounded-lg border border-slate-200 p-4 shadow-sm hover:shadow-md hover:border-[#1e3a5f]/40 transition"
                >
                  <div className="flex justify-between items-start mb-2">
                    <h3 className="font-bold text-slate-800">{b.name}</h3>
                    <span
                      className={`rounded px-2 py-0.5 text-xs font-semibold ${
                        b.status === 'active' ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-200 text-slate-500'
                      }`}
                    >
                      {b.status === 'active' ? '进行中' : '已关闭'}
                    </span>
                  </div>
                  <p className="text-xs text-slate-500 mb-3">
                    {b.scale_ids.join(' + ').toUpperCase()} · {b.created_at.slice(0, 10)}
                  </p>
                  <div className="flex justify-between text-xs text-slate-600 mb-1.5">
                    <span>完成 {b.completed}/{b.total}</span>
                    <span>{pct}%</span>
                  </div>
                  <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
                    <div className="h-full bg-[#1e3a5f]" style={{ width: `${pct}%` }} />
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </main>
    </div>
  );
}
