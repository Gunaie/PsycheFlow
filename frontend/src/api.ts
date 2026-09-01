/** 极简 fetch 封装：统一错误展示，/api 走 vite 代理到后端。 */

const TOKEN_KEY = 'psycheflow_token';
const LABEL_KEY = 'psycheflow_label';

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string, label?: string): void {
  localStorage.setItem(TOKEN_KEY, token);
  if (label) localStorage.setItem(LABEL_KEY, label);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(LABEL_KEY);
}

export function getLabel(): string | null {
  return localStorage.getItem(LABEL_KEY);
}

export function getAuthHeaders(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

export async function apiGetAuth<T = any>(url: string): Promise<T> {
  const res = await fetch(url, {
    headers: { ...getAuthHeaders(), 'Accept': 'application/json' },
  });
  if (!res.ok) throw new Error(`GET ${url} ${res.status}`);
  return (await res.json()) as T;
}

export async function apiGetBlob(url: string): Promise<Blob> {
  const res = await fetch(url, { headers: getAuthHeaders() });
  if (!res.ok) throw new Error(`GET ${url} ${res.status}`);
  return await res.blob();
}

async function toError(r: Response): Promise<Error> {
  try {
    const data = await r.json()
    const d = data.detail
    let msg: string
    if (typeof d === 'string') {
      msg = d
    } else if (Array.isArray(d) && d.length > 0 && typeof d[0] === 'object') {
      // FastAPI Pydantic 校验错误列表：取每条 loc+msg 拼接成中文可读提示
      msg = d.map((e: any) => {
        const loc = Array.isArray(e.loc) ? e.loc.slice(1).join('.') : ''
        return `${loc || '字段'}: ${e.msg || ''}`
      }).filter(Boolean).join('；')
    } else if (d && typeof d === 'object' && 'code' in d) {
      // 自定义错误体 {code, missing}：比如缺知情同意
      msg = String(d.code) + (Array.isArray(d.missing) && d.missing.length ? `：缺少 ${d.missing.join(', ')}` : '')
    } else if (d === undefined || d === null) {
      msg = `HTTP ${r.status}`
    } else {
      msg = JSON.stringify(d)
    }
    return new Error(msg || `HTTP ${r.status}`)
  } catch {
    return new Error(`HTTP ${r.status}`)
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const r = await fetch(path, {
    headers: { ...getAuthHeaders() },
  })
  if (!r.ok) throw await toError(r)
  return r.json() as Promise<T>
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeaders(),
    },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw await toError(r)
  return r.json() as Promise<T>
}

export async function apiPostBlob(path: string, body: unknown): Promise<Blob> {
  const r = await fetch(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeaders(),
    },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw await toError(r)
  return r.blob()
}
