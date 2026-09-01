/**
 * 浏览器 WAV 录音器（D3 四期语音输入）。
 *
 * 为什么不用 MediaRecorder：其输出容器（webm/ogg）依赖浏览器实现，
 * ASR 服务端兼容性不稳；Web Audio 直接采集 PCM 编码成 16kHz 单声道 WAV，
 * 格式确定、体积小（32KB/s），对百炼 ASR 万无一失。
 *
 * 用法：
 *   const rec = new WavRecorder()
 *   await rec.start()          // 申请麦克风权限并开始采集
 *   const blob = await rec.stop()  // 停止并返回 audio/wav Blob
 */

const TARGET_SAMPLE_RATE = 16000

export class WavRecorder {
  private stream: MediaStream | null = null
  private ctx: AudioContext | null = null
  private processor: ScriptProcessorNode | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private chunks: Float32Array[] = []
  private recording = false

  get active(): boolean {
    return this.recording
  }

  async start(): Promise<void> {
    if (this.recording) return
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    this.ctx = new AudioContext()
    this.source = this.ctx.createMediaStreamSource(this.stream)
    // ScriptProcessorNode 虽已标记废弃，但兼容所有浏览器且无需独立 worklet 文件
    this.processor = this.ctx.createScriptProcessor(4096, 1, 1)
    this.chunks = []
    this.processor.onaudioprocess = (e) => {
      if (!this.recording) return
      this.chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)))
    }
    this.source.connect(this.processor)
    this.processor.connect(this.ctx.destination)
    this.recording = true
  }

  async stop(): Promise<Blob> {
    if (!this.recording) throw new Error('录音未开始')
    this.recording = false

    // 采样率重采样到 16kHz（线性插值足够语音识别用）
    const srcRate = this.ctx!.sampleRate
    const merged = mergeChunks(this.chunks)
    const pcm = resample(merged, srcRate, TARGET_SAMPLE_RATE)
    const blob = encodeWav(pcm, TARGET_SAMPLE_RATE)

    // 释放资源
    this.processor?.disconnect()
    this.source?.disconnect()
    this.stream?.getTracks().forEach((t) => t.stop())
    await this.ctx?.close()
    this.processor = null
    this.source = null
    this.stream = null
    this.ctx = null
    this.chunks = []
    return blob
  }
}

function mergeChunks(chunks: Float32Array[]): Float32Array {
  const total = chunks.reduce((n, c) => n + c.length, 0)
  const out = new Float32Array(total)
  let offset = 0
  for (const c of chunks) {
    out.set(c, offset)
    offset += c.length
  }
  return out
}

function resample(input: Float32Array, srcRate: number, dstRate: number): Float32Array {
  if (srcRate === dstRate) return input
  const ratio = srcRate / dstRate
  const outLen = Math.floor(input.length / ratio)
  const out = new Float32Array(outLen)
  for (let i = 0; i < outLen; i++) {
    const pos = i * ratio
    const idx = Math.floor(pos)
    const frac = pos - idx
    const a = input[idx] ?? 0
    const b = input[idx + 1] ?? a
    out[i] = a + (b - a) * frac
  }
  return out
}

/** Float32 PCM → 16bit PCM WAV（RIFF）封装。 */
function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2)
  const view = new DataView(buffer)
  const writeStr = (offset: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i))
  }
  writeStr(0, 'RIFF')
  view.setUint32(4, 36 + samples.length * 2, true)
  writeStr(8, 'WAVE')
  writeStr(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true) // PCM
  view.setUint16(22, 1, true) // 单声道
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true) // byte rate
  view.setUint16(32, 2, true) // block align
  view.setUint16(34, 16, true) // bits per sample
  writeStr(36, 'data')
  view.setUint32(40, samples.length * 2, true)
  let offset = 44
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]))
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true)
    offset += 2
  }
  return new Blob([view], { type: 'audio/wav' })
}
