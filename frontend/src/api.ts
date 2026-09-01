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

/** multipart 文件上传（语音转写等场景）。 */
export async function apiPostForm<T>(path: string, form: FormData): Promise<T> {
  const r = await fetch(path, {
    method: 'POST',
    headers: { ...getAuthHeaders() }, // 不手动设 Content-Type，交给浏览器生成 boundary
    body: form,
  })
  if (!r.ok) throw await toError(r)
  return r.json() as Promise<T>
}

// —— SSE 流式对话消费（fetch + ReadableStream 解析，因 EventSource 不支持 POST+auth）——

export interface SSEEvent {
  event: string
  data: any
}

/**
 * 流式对话：POST /api/chat/stream，边收 SSE 事件边回调 onEvent。
 *
 * 不用 EventSource：它只支持 GET，无法带 body 与 Authorization 头。
 * 改用 fetch + ReadableStream 手动按 \n\n 分割解析 SSE 事件。
 */
export async function streamChat(
  body: unknown,
  onEvent: (evt: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const r = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeaders(),
    },
    body: JSON.stringify(body),
    signal,
  })
  if (!r.ok) throw await toError(r)
  if (!r.body) throw new Error('响应体为空，无法流式读取')

  const reader = r.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      // SSE 事件以空行 \n\n 分隔；最后一段可能不完整，留在 buffer
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''
      for (const part of parts) {
        const evt = parseSSE(part)
        if (evt) onEvent(evt)
      }
    }
    // flush 残留
    if (buffer.trim()) {
      const evt = parseSSE(buffer)
      if (evt) onEvent(evt)
    }
  } finally {
    reader.releaseLock()
  }
}

/** 解析单条 SSE 事件块（多行 event:/data:，data 可多行拼接）。 */
function parseSSE(raw: string): SSEEvent | null {
  let event = 'message'
  const dataLines: string[] = []
  for (const line of raw.split('\n')) {
    if (line.startsWith('event: ')) event = line.slice(7).trim()
    else if (line.startsWith('data: ')) dataLines.push(line.slice(6))
  }
  if (dataLines.length === 0) return null
  const dataStr = dataLines.join('\n')
  try {
    return { event, data: JSON.parse(dataStr) }
  } catch {
    return { event, data: dataStr }
  }
}
