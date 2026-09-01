/** 极简 fetch 封装：统一错误展示，/api 走 vite 代理到后端。 */

async function toError(r: Response): Promise<Error> {
  try {
    const data = await r.json()
    return new Error(data.detail || `HTTP ${r.status}`)
  } catch {
    return new Error(`HTTP ${r.status}`)
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const r = await fetch(path)
  if (!r.ok) throw await toError(r)
  return r.json() as Promise<T>
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw await toError(r)
  return r.json() as Promise<T>
}

export async function apiPostBlob(path: string, body: unknown): Promise<Blob> {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw await toError(r)
  return r.blob()
}
