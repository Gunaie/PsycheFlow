import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { apiGet, apiGetBlob, apiPost } from '../../api';

interface EntryAssessment {
  scale_id: string;
  scale_name: string;
  total_score: number;
  severity: string;
  crisis_level: string;
}

interface BatchEntry {
  entry_id: string;
  student_no: string;
  student_name: string;
  grade: string | null;
  klass: string | null;
  entry_code: string;
  status: string;
  completed_at: string | null;
  assessments: EntryAssessment[];
}

interface CrisisItem {
  entry_id: string;
  student_no: string;
  student_name: string;
  grade: string | null;
  klass: string | null;
  scale_name: string;
  total_score: number;
  severity: string;
  crisis_triggers: string[];
}

interface BatchDetail {
  batch_id: string;
  name: string;
  scale_ids: string[];
  status: string;
  total: number;
  completed: number;
  pending: number;
  crisis_count: number;
  crisis_list: CrisisItem[];
  severity_distribution: Record<string, Record<string, number>>;
  by_class: Record<string, { total: number; completed: number }>;
  entries: BatchEntry[];
}

const SEV_CN: Record<string, string> = {
  none: '无', minimal: '极轻', mild: '轻度', moderate: '中度',
  moderately_severe: '中重度', severe: '重度',
};

const SEV_COLOR: Record<string, string> = {
  none: 'bg-emerald-500', minimal: 'bg-emerald-400', mild: 'bg-amber-400',
  moderate: 'bg-orange-500', moderately_severe: 'bg-red-500', severe: 'bg-red-700',
};

export default function AdminBatchDetailPage() {
  const navigate = useNavigate();
  const { batchId } = useParams<{ batchId: string }>();
  const [detail, setDetail] = useState<BatchDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchDetail = useCallback(async () => {
    if (!batchId) return;
    const res = await apiGet<BatchDetail>(`/api/admin/batches/${batchId}`);
    setDetail(res);
  }, [batchId]);

  useEffect(() => {
    setLoading(true);
    fetchDetail()
      .catch((e: Error) => {
        if (e.message.includes('401') || e.message.includes('403')) navigate('/admin/login');
        else setError(e.message);
      })
      .finally(() => setLoading(false));
    // 30s 轮询刷新进度
    const timer = setInterval(() => fetchDetail().catch(() => {}), 30000);
    return () => clearInterval(timer);
  }, [fetchDetail, navigate]);

  const closeBatch = async () => {
    if (!batchId || !confirm('关闭后所有筛查码将失效，确定关闭该批次？')) return;
    try {
      await apiPost(`/api/admin/batches/${batchId}/close`, {});
      fetchDetail();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const exportCsv = async () => {
    if (!batchId) return;
    try {
      const blob = await apiGetBlob(`/api/admin/batches/${batchId}/export`);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `batch_summary_${batchId.slice(0, 8)}.csv`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const downloadEntryReport = async (entryId: string) => {
    if (!batchId) return;
    const win = window.open('', '_blank');
    if (!win) { alert('浏览器拦截了新窗口，请允许本站弹窗后重试'); return; }
    win.document.write('<p style="font-family:sans-serif;text-align:center;margin-top:40vh">报告生成中…</p>');
    try {
      const blob = await apiGetBlob(`/api/admin/batches/${batchId}/entries/${entryId}/report`);
      const url = URL.createObjectURL(blob);
      win.location.href = url;
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (e) {
      win.close();
      alert(`报告生成失败：${(e as Error).message}`);
    }
  };

  if (loading) {
    return <div className="min-h-screen bg-slate-50 flex items-center justify-center text-slate-500">加载中...</div>;
  }
  if (!detail) {
    return <div className="min-h-screen bg-slate-50 flex items-center justify-center text-red-500">{error || '批次不存在'}</div>;
  }

  const pct = detail.total > 0 ? Math.round((detail.completed / detail.total) * 100) : 0;
  const crisisEntries = new Set(detail.crisis_list.map((c) => c.entry_id));

  return (
    <div className="min-h-screen bg-slate-50 pb-10">
      <header className="bg-[#1e3a5f] text-white">
        <div className="max-w-7xl mx-auto px-4 py-4 flex justify-between items-center">
          <div>
            <h1 className="text-lg font-bold">{detail.name}</h1>
            <p className="text-xs text-slate-300 mt-0.5">
              {detail.scale_ids.join(' + ').toUpperCase()} · 共 {detail.total} 人
              {detail.status === 'closed' && ' · 已关闭'}
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={exportCsv}
              className="rounded-md border border-white/30 px-3 py-1.5 text-sm hover:bg-white/10"
            >
              导出汇总 CSV
            </button>
            {detail.status === 'active' && (
              <button
                onClick={closeBatch}
                className="rounded-md bg-red-500/80 px-3 py-1.5 text-sm hover:bg-red-500"
              >
                关闭批次
              </button>
            )}
            <button
              onClick={() => navigate('/admin')}
              className="rounded-md border border-white/30 px-3 py-1.5 text-sm hover:bg-white/10"
            >
              ← 批次列表
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-6">
        {error && <div className="bg-red-50 text-red-600 text-sm p-3 rounded-lg">{error}</div>}

        {/* 统计卡片 */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[
            { label: '应测人数', value: detail.total, cls: 'text-slate-800' },
            { label: '已完成', value: detail.completed, cls: 'text-emerald-600' },
            { label: '未完成', value: detail.pending, cls: 'text-slate-400' },
            { label: '危机升级', value: detail.crisis_count, cls: detail.crisis_count > 0 ? 'text-red-600' : 'text-slate-800' },
          ].map((c) => (
            <div key={c.label} className="bg-white rounded-lg border border-slate-200 p-4 shadow-sm">
              <p className="text-xs text-slate-500 mb-1">{c.label}</p>
              <p className={`text-2xl font-bold ${c.cls}`}>{c.value}</p>
            </div>
          ))}
        </div>

        {/* 进度条 */}
        <div className="bg-white rounded-lg border border-slate-200 p-4 shadow-sm">
          <div className="flex justify-between text-sm text-slate-600 mb-2">
            <span>施测进度</span>
            <span>{detail.completed}/{detail.total}（{pct}%）</span>
          </div>
          <div className="h-3 rounded-full bg-slate-100 overflow-hidden">
            <div className="h-full bg-[#1e3a5f] transition-all" style={{ width: `${pct}%` }} />
          </div>
        </div>

        {/* 危机名单 */}
        {detail.crisis_list.length > 0 && (
          <div className="bg-white rounded-lg border-2 border-red-300 p-4 shadow-sm">
            <h2 className="font-bold text-red-700 mb-3">
              ⚠ 危机升级名单（{detail.crisis_list.length} 人）— 需按「学校心理老师 → 家长 → 12355」转介链处理
            </h2>
            <div className="space-y-2">
              {detail.crisis_list.map((c, i) => (
                <div key={`${c.entry_id}-${i}`} className="rounded-md bg-red-50 p-3 text-sm">
                  <div className="flex justify-between">
                    <span className="font-semibold text-slate-800">
                      {c.student_no} {c.student_name}
                      <span className="font-normal text-slate-500 ml-2">
                        {c.grade || ''} {c.klass || ''}
                      </span>
                    </span>
                    <span className="text-red-600 font-medium">
                      {c.scale_name}：{c.total_score} 分 · {SEV_CN[c.severity] || c.severity}
                    </span>
                  </div>
                  <div className="text-xs text-red-600 mt-1">触发项：{c.crisis_triggers.join('；')}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="grid gap-6 lg:grid-cols-2">
          {/* severity 分布 */}
          <div className="bg-white rounded-lg border border-slate-200 p-4 shadow-sm">
            <h2 className="font-bold text-slate-800 mb-4">严重度分布</h2>
            {detail.completed === 0 ? (
              <p className="text-sm text-slate-400">暂无完成数据</p>
            ) : (
              <div className="space-y-4">
                {Object.entries(detail.severity_distribution).map(([sid, dist]) => {
                  const sum = Object.values(dist).reduce((a, b) => a + b, 0);
                  return (
                    <div key={sid}>
                      <p className="text-xs font-semibold text-slate-600 mb-2">{sid.toUpperCase()}（{sum} 人已完成）</p>
                      <div className="flex h-6 rounded-md overflow-hidden bg-slate-100">
                        {Object.entries(dist).map(([sev, n]) => (
                          <div
                            key={sev}
                            className={`${SEV_COLOR[sev] || 'bg-slate-300'} flex items-center justify-center text-[10px] text-white font-semibold`}
                            style={{ width: `${(n / sum) * 100}%` }}
                            title={`${SEV_CN[sev] || sev}: ${n}`}
                          >
                            {n / sum > 0.08 ? `${SEV_CN[sev] || sev} ${n}` : ''}
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* 按班级 */}
          <div className="bg-white rounded-lg border border-slate-200 p-4 shadow-sm">
            <h2 className="font-bold text-slate-800 mb-4">按班级进度</h2>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-500 border-b border-slate-200">
                  <th className="pb-2">年级 / 班级</th>
                  <th className="pb-2">完成 / 应测</th>
                  <th className="pb-2">完成率</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(detail.by_class).map(([cls, g]) => {
                  const cpct = g.total > 0 ? Math.round((g.completed / g.total) * 100) : 0;
                  return (
                    <tr key={cls} className="border-b border-slate-100 last:border-0">
                      <td className="py-2 text-slate-700">{cls}</td>
                      <td className="py-2 text-slate-600">{g.completed}/{g.total}</td>
                      <td className="py-2">
                        <span className={`font-semibold ${cpct === 100 ? 'text-emerald-600' : 'text-slate-600'}`}>
                          {cpct}%
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* 学生名单表 */}
        <div className="bg-white rounded-lg border border-slate-200 p-4 shadow-sm">
          <h2 className="font-bold text-slate-800 mb-4">学生名单</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-500 border-b border-slate-200">
                  <th className="pb-2 pr-3">学号</th>
                  <th className="pb-2 pr-3">姓名</th>
                  <th className="pb-2 pr-3">年级/班级</th>
                  <th className="pb-2 pr-3">筛查码</th>
                  <th className="pb-2 pr-3">状态</th>
                  <th className="pb-2 pr-3">结果</th>
                  <th className="pb-2">报告</th>
                </tr>
              </thead>
              <tbody>
                {detail.entries.map((e) => (
                  <tr
                    key={e.entry_id}
                    className={`border-b border-slate-100 last:border-0 ${
                      crisisEntries.has(e.entry_id) ? 'bg-red-50' : ''
                    }`}
                  >
                    <td className="py-2 pr-3 font-mono text-xs text-slate-600">{e.student_no}</td>
                    <td className="py-2 pr-3 text-slate-800">
                      {e.student_name}
                      {crisisEntries.has(e.entry_id) && (
                        <span className="ml-1.5 rounded bg-red-600 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                          危机
                        </span>
                      )}
                    </td>
                    <td className="py-2 pr-3 text-xs text-slate-500">
                      {[e.grade, e.klass].filter(Boolean).join(' / ') || '-'}
                    </td>
                    <td className="py-2 pr-3 font-mono font-bold text-[#1e3a5f] tracking-wider">{e.entry_code}</td>
                    <td className="py-2 pr-3">
                      {e.status === 'completed' ? (
                        <span className="text-emerald-600 text-xs font-semibold">已完成</span>
                      ) : (
                        <span className="text-slate-400 text-xs">未完成</span>
                      )}
                    </td>
                    <td className="py-2 pr-3">
                      <div className="flex flex-wrap gap-1">
                        {e.assessments.map((a) => (
                          <span
                            key={a.scale_id}
                            className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                              a.crisis_level === 'elevated'
                                ? 'bg-red-100 text-red-700'
                                : 'bg-slate-100 text-slate-600'
                            }`}
                          >
                            {a.scale_id.toUpperCase()}: {a.total_score} · {SEV_CN[a.severity] || a.severity}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="py-2">
                      {e.status === 'completed' ? (
                        <button
                          onClick={() => downloadEntryReport(e.entry_id)}
                          className="rounded border border-[#1e3a5f] px-2 py-1 text-xs text-[#1e3a5f] hover:bg-blue-50"
                        >
                          PDF
                        </button>
                      ) : (
                        <span className="text-xs text-slate-300">-</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </main>
    </div>
  );
}
