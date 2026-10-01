import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { apiGet, apiPost, streamChat, getToken, setToken, clearToken, getChatSessionId, setChatSessionId } from './api'

describe('api 封装', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('token 与 chat session 读写', () => {
    expect(getToken()).toBeNull()
    setToken('abc', '标签', 'student')
    expect(getToken()).toBe('abc')
    setChatSessionId('s-1')
    expect(getChatSessionId()).toBe('s-1')
    clearToken()
    expect(getToken()).toBeNull()
    expect(getChatSessionId()).toBeNull()
  })

  it('apiGet 携带 Authorization 头并返回 JSON', async () => {
    setToken('tok')
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const data = await apiGet<{ ok: boolean }>('/api/x')
    expect(data.ok).toBe(true)
    expect(fetchMock).toHaveBeenCalledWith('/api/x', expect.objectContaining({
      headers: { Authorization: 'Bearer tok' },
    }))
  })

  it('apiPost 抛出后端 detail 字符串错误', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: '服务器繁忙' }), { status: 500 }),
    ))
    await expect(apiPost('/api/x', {})).rejects.toThrow('服务器繁忙')
  })

  it('apiPost 拼接 Pydantic 校验错误列表', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: [
            { loc: ['body', 'name'], msg: 'field required' },
            { loc: ['body', 'age'], msg: 'value error' },
          ],
        }),
        { status: 422 },
      ),
    ))
    await expect(apiPost('/api/x', {})).rejects.toThrow('name: field required；age: value error')
  })

  it('apiGet 遇到非 JSON 错误体时回退 HTTP 状态码', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('oops', { status: 502 })))
    await expect(apiGet('/api/x')).rejects.toThrow('HTTP 502')
  })
})

describe('streamChat SSE 解析', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function sseResponse(chunks: string[]): Response {
    const encoder = new TextEncoder()
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const c of chunks) controller.enqueue(encoder.encode(c))
        controller.close()
      },
    })
    return new Response(stream, { status: 200 })
  }

  it('按 \\n\\n 切分并解析 event/data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([
      'event: agent\ndata: {"agent":"triage"}\n\n',
      'event: token\ndata: {"token":"你"}\n\n',
      'event: token\ndata: {"token":"好"}\n\n',
      'event: done\ndata: {"reply":"你好","crisis":false}\n\n',
    ])))

    const events: Array<{ event: string; data: any }> = []
    await streamChat({ message: 'hi' }, (evt) => events.push(evt))

    expect(events.map(e => e.event)).toEqual(['agent', 'token', 'token', 'done'])
    expect(events[0].data.agent).toBe('triage')
    expect(events[3].data.reply).toBe('你好')
  })

  it('跨 chunk 的不完整事件留在 buffer 继续拼接', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([
      'event: token\ndata: {"token":"he',
      'llo"}\n\n',
    ])))

    const events: Array<{ event: string; data: any }> = []
    await streamChat({}, (evt) => events.push(evt))
    expect(events).toHaveLength(1)
    expect(events[0].data.token).toBe('hello')
  })

  it('无 data 行的事件被忽略', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([
      'event: ping\n\n',
      'event: token\ndata: {"token":"x"}\n\n',
    ])))

    const events: Array<{ event: string; data: any }> = []
    await streamChat({}, (evt) => events.push(evt))
    expect(events).toHaveLength(1)
    expect(events[0].event).toBe('token')
  })

  it('非 2xx 响应抛出后端 detail 错误', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: '限流' }), { status: 429 }),
    ))
    await expect(streamChat({}, () => {})).rejects.toThrow('限流')
  })
})
