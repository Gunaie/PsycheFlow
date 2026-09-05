import type { ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { clearToken } from '../../api';

/**
 * B 端管理后台公共布局：深色顶栏（品牌 + 返回学生端 + 退出登录）+ 页面标题区 + 内容。
 * 页面级操作按钮通过 action 传入；标题区可选（登录页不使用本组件）。
 */
export default function AdminShell({
  title,
  subtitle,
  action,
  children,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}) {
  const navigate = useNavigate();
  return (
    <div className="min-h-screen bg-slate-50 pb-10">
      <header className="bg-[#1e3a5f] text-white">
        <div className="max-w-7xl mx-auto px-4 py-4 flex justify-between items-center gap-4">
          <div className="flex items-center gap-4 min-w-0">
            <Link to="/admin" className="text-lg font-bold shrink-0">
              PsycheFlow · 管理后台
            </Link>
          </div>
          <div className="flex gap-2 items-center shrink-0">
            {action}
            <button
              onClick={() => {
                clearToken();
                navigate('/');
              }}
              className="rounded-md border border-white/30 px-3 py-1.5 text-sm hover:bg-white/10"
            >
              退出
            </button>
          </div>
        </div>
        {title && (
          <div className="max-w-7xl mx-auto px-4 pb-4 border-t border-white/10 pt-3">
            <h1 className="text-lg font-bold">{title}</h1>
            {subtitle && <p className="text-xs text-slate-300 mt-0.5">{subtitle}</p>}
          </div>
        )}
      </header>
      {children}
    </div>
  );
}
