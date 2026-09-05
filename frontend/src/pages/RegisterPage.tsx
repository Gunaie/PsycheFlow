import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiPost, setToken } from '../api';
import BackLink from '../components/BackLink';
import FooterDisclaimer from '../components/FooterDisclaimer';

interface Consents {
  tool: boolean;
  guardian: boolean;
  privacy14: boolean;
  crisis: boolean;
}

interface StudentProfile {
  name: string;
  student_id: string;
  grade: string;
  class_name: string;
  gender: string;
  age: string;
  guardian_phone: string;
  school_name: string;
  teacher_email: string;
}

interface RegisterResponse {
  token: string;
  label: string;
  role: string;
}

export default function RegisterPage() {
  const navigate = useNavigate();
  const [consents, setConsents] = useState<Consents>({
    tool: false,
    guardian: false,
    privacy14: false,
    crisis: false,
  });
  const [profile, setProfile] = useState<StudentProfile>({
    name: '',
    student_id: '',
    grade: '',
    class_name: '',
    gender: '',
    age: '',
    guardian_phone: '',
    school_name: '',
    teacher_email: '',
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RegisterResponse | null>(null);
  const [copied, setCopied] = useState(false);

  const fourChecked = consents.tool && consents.guardian && consents.privacy14 && consents.crisis;

  const toggle = (key: keyof Consents) => {
    setConsents((c) => ({ ...c, [key]: !c[key] }));
  };

  const handleRegister = async () => {
    if (!fourChecked || loading) return;
    setLoading(true);
    setError(null);
    try {
      // 提交前清洗：① 空字符串 → None（后端所有 Profile 字段默认 None 可空）；
      //             ② age 转 int 或 null（避免 int_parsing 422）；
      //             ③ 字段名对齐后端 Profile（student_no/klass/school）
      const cleanedProfile = {
        name: profile.name || null,
        student_no: profile.student_id || null,
        grade: profile.grade || null,
        klass: profile.class_name || null,
        gender: profile.gender || null,
        age: (!profile.age || profile.age === '') ? null : Number(profile.age),
        guardian_phone: profile.guardian_phone || null,
        school: profile.school_name || null,
        teacher_email: profile.teacher_email || null,
      };
      const res = await apiPost<RegisterResponse>('/api/auth/register', {
        consents,
        profile: cleanedProfile,
      });
      setResult(res);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const handleCopy = async () => {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result.token);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setError('复制失败，请手动复制 Token');
    }
  };

  const handleGoScale = () => {
    if (!result) return;
    setToken(result.token, result.label, result.role);
    navigate('/assess');
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="bg-[#1e3a5f] text-white">
        <div className="max-w-3xl mx-auto px-6 py-5 flex items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold">PsycheFlow · 注册与知情同意</h1>
            <p className="text-sm text-slate-200 mt-1">
              本系统面向未成年人校园心理筛查，请完成以下知情同意后使用
            </p>
          </div>
          <BackLink to="/home" variant="dark">返回首页</BackLink>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-8">
        <section className="rounded-md border border-slate-300 bg-white p-4 mb-4">
          <h2 className="text-lg font-semibold text-slate-800 mb-2">
            🛡️「工具性质说明（必选）」
          </h2>
          <p className="text-sm text-slate-600 leading-relaxed mb-3">
            PsycheFlow 仅作为校园心理筛查辅助工具，输出结果包括量表分、发展建议和 PDF 报告，
            <strong>均不是医学诊断、不能作为精神科诊疗或开处方药的依据</strong>。
            所有高危信号会按硬编码流程通知 12355 青少年公益心理热线及学校心理老师，不依赖任何 AI 决定。
          </p>
          <label className="flex items-start gap-2 text-sm text-slate-700 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={consents.tool}
              onChange={() => toggle('tool')}
              className="mt-0.5 shrink-0"
            />
            <span>我已阅读并同意，本系统非医疗器械，不替代专业诊疗/诊断/开药。</span>
          </label>
        </section>

        <section className="rounded-md border border-slate-300 bg-white p-4 mb-4">
          <h2 className="text-lg font-semibold text-slate-800 mb-2">
            👪「监护人/学校授权声明（必选）」
          </h2>
          <p className="text-sm text-slate-600 leading-relaxed mb-3">
            由于本系统的被测对象为中小学生（均为未成年人），根据《未成年人保护法》第 73 条与学校心理筛查规范，
            本系统必须获得家长/监护人的书面同意或学校的书面授权后方可使用。您在本复选框勾选即代表：
            您是该学生的家长/监护人，或代表持有授权文件的学校心理老师/德育处，
            同意被测学生完成本系统测评、接收报告与相关推荐，
            且同意系统在危机情况下为保护生命打破保密限制。
          </p>
          <label className="flex items-start gap-2 text-sm text-slate-700 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={consents.guardian}
              onChange={() => toggle('guardian')}
              className="mt-0.5 shrink-0"
            />
            <span>我是学生家长/监护人或学校授权人员，同意被测未成年人使用本系统并允许生成筛查报告</span>
          </label>
        </section>

        <section className="rounded-md border border-slate-300 bg-white p-4 mb-4">
          <h2 className="text-lg font-semibold text-slate-800 mb-2">
            🔒「14 周岁以下信息特别保护（必选）」
          </h2>
          <p className="text-sm text-slate-600 leading-relaxed mb-3">
            根据《个人信息保护法》第 31 条，14 周岁以下未成年人的个人信息属于敏感个人信息，
            系统将严格遵守"最小必要"原则收集：
            <br />
            ① 您可以<strong>完全匿名</strong>测评，档案字段全部可空；
            <br />
            ② 学生姓名、学号、班级、监护人手机号等均为可选项，不填也能完整使用；
            <br />
            ③ 系统不收集人脸、指纹、通讯录、相册等与测评无关的数据；
            <br />
            ④ 数据仅用于报告生成与危机转介，不用于商业目的，未经授权绝不向第三方披露。
          </p>
          <label className="flex items-start gap-2 text-sm text-slate-700 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={consents.privacy14}
              onChange={() => toggle('privacy14')}
              className="mt-0.5 shrink-0"
            />
            <span>我理解 14 周岁以下未成年人个人信息受特别保护，系统仅收集测评必要字段</span>
          </label>
        </section>

        <section className="rounded-md border border-slate-300 bg-white p-4 mb-4">
          <h2 className="text-lg font-semibold text-slate-800 mb-2">
            🚨「危机打破保密与转介链路（必选）」
          </h2>
          <p className="text-sm text-slate-600 leading-relaxed mb-3">
            当测评中出现自杀/自伤/伤人等危机关键词（如"不想活了""跳楼""去死"）或 PHQ-A 量表第 9 题单项≥1 分时，
            系统<strong>为保护学生生命安全打破保密</strong>，按硬编码链路立即转介：
            <br />
            ① 校内：学校心理老师 → 德育处 → 家长；
            <br />
            ② 校外：12355 共青团青少年心理热线（24h 免费） → 120 急救 → 110 报警（视等级）；
            <br />
            ③ 每次危机命中会在服务器独立 logs 目录写 JSON 审计文件，可追溯、不可篡改。
            <br />
            您勾选即代表同意该流程。
          </p>
          <label className="flex items-start gap-2 text-sm text-slate-700 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={consents.crisis}
              onChange={() => toggle('crisis')}
              className="mt-0.5 shrink-0"
            />
            <span>我同意危机升级时为保护学生生命安全，系统可打破保密并通知 12355 + 学校心理老师链路</span>
          </label>
        </section>

        <section className="rounded-md border border-slate-300 bg-white p-4 mb-6">
          <h2 className="text-lg font-semibold text-slate-800 mb-4">
            🎒 学生档案（全部可空 · 推荐匿名）
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-1">
            <div className="mb-3 w-full max-w-md">
              <label className="block text-sm text-slate-600 mb-1">姓名</label>
              <input
                type="text"
                value={profile.name}
                onChange={(e) => setProfile({ ...profile, name: e.target.value })}
                placeholder="可留空不填"
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:border-[#1e3a5f]"
              />
            </div>
            <div className="mb-3 w-full max-w-md">
              <label className="block text-sm text-slate-600 mb-1">学号</label>
              <input
                type="text"
                value={profile.student_id}
                onChange={(e) => setProfile({ ...profile, student_id: e.target.value })}
                placeholder="可留空不填"
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:border-[#1e3a5f]"
              />
            </div>
            <div className="mb-3 w-full max-w-md">
              <label className="block text-sm text-slate-600 mb-1">年级</label>
              <input
                type="text"
                value={profile.grade}
                onChange={(e) => setProfile({ ...profile, grade: e.target.value })}
                placeholder="可留空不填"
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:border-[#1e3a5f]"
              />
            </div>
            <div className="mb-3 w-full max-w-md">
              <label className="block text-sm text-slate-600 mb-1">班级</label>
              <input
                type="text"
                value={profile.class_name}
                onChange={(e) => setProfile({ ...profile, class_name: e.target.value })}
                placeholder="可留空不填"
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:border-[#1e3a5f]"
              />
            </div>
            <div className="mb-3 w-full max-w-md">
              <label className="block text-sm text-slate-600 mb-1">性别</label>
              <select
                value={profile.gender}
                onChange={(e) => setProfile({ ...profile, gender: e.target.value })}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:border-[#1e3a5f] bg-white"
              >
                <option value="">不填</option>
                <option value="男">男</option>
                <option value="女">女</option>
                <option value="其他">其他</option>
              </select>
            </div>
            <div className="mb-3 w-full max-w-md">
              <label className="block text-sm text-slate-600 mb-1">年龄</label>
              <input
                type="number"
                value={profile.age}
                onChange={(e) => setProfile({ ...profile, age: e.target.value })}
                placeholder="可留空不填"
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:border-[#1e3a5f]"
              />
            </div>
            <div className="mb-3 w-full max-w-md">
              <label className="block text-sm text-slate-600 mb-1">监护人手机号</label>
              <input
                type="text"
                value={profile.guardian_phone}
                onChange={(e) => setProfile({ ...profile, guardian_phone: e.target.value })}
                placeholder="可留空不填"
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:border-[#1e3a5f]"
              />
            </div>
            <div className="mb-3 w-full max-w-md">
              <label className="block text-sm text-slate-600 mb-1">学校名</label>
              <input
                type="text"
                value={profile.school_name}
                onChange={(e) => setProfile({ ...profile, school_name: e.target.value })}
                placeholder="可留空不填"
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:border-[#1e3a5f]"
              />
            </div>
            <div className="mb-3 w-full max-w-md md:col-span-2">
              <label className="block text-sm text-slate-600 mb-1">心理老师邮箱</label>
              <input
                type="text"
                value={profile.teacher_email}
                onChange={(e) => setProfile({ ...profile, teacher_email: e.target.value })}
                placeholder="可留空不填"
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:border-[#1e3a5f]"
              />
            </div>
          </div>
        </section>

        {error && (
          <div className="bg-red-50 text-red-600 text-sm p-3 rounded-lg mb-4">{error}</div>
        )}

        <button
          onClick={handleRegister}
          disabled={!fourChecked || loading}
          className={`w-full rounded-md py-3 font-semibold text-white ${
            fourChecked && !loading
              ? 'bg-[#1e3a5f] hover:opacity-95'
              : 'bg-slate-300 cursor-not-allowed'
          }`}
        >
          {loading ? '生成账号中...' : '生成账号'}
        </button>

        {!fourChecked && (
          <p className="text-xs text-slate-500 mt-2 text-center">
            请先勾选上方 4 项知情同意
          </p>
        )}

        <FooterDisclaimer />
      </main>

      {result && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-2xl max-w-md w-full p-6">
            <div className="text-center mb-4">
              <div className="text-4xl mb-2">✅</div>
              <h3 className="text-xl font-bold text-slate-800">注册成功</h3>
              <p className="text-sm text-slate-500 mt-1">
                label: <span className="font-mono font-semibold text-slate-700">{result.label}</span>
              </p>
            </div>

            <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 mb-4">
              <p className="text-sm text-yellow-800 font-medium">
                ⚠️ 请务必妥善保存以下 Token（仅显示一次，忘记将无法登录）：
              </p>
            </div>

            <div className="bg-[#1e3a5f] text-white rounded-lg p-4 mb-4">
              <div className="text-xs text-slate-300 mb-1">你的 Token</div>
              <div className="text-lg font-mono break-all leading-relaxed select-all">
                {result.token}
              </div>
            </div>

            <button
              onClick={handleCopy}
              className="w-full mb-3 py-2 rounded-md border border-slate-300 bg-white text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              {copied ? '✅ 已复制到剪贴板' : '复制到剪贴板'}
            </button>

            <button
              onClick={handleGoScale}
              className="w-full py-2.5 rounded-md bg-[#1e3a5f] text-white text-sm font-semibold hover:opacity-95"
            >
              前往测评
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
