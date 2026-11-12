/**
 * 마이크 입력(Float32, 브라우저 기본 샘플레이트)을
 * 16 kHz mono PCM s16le 청크로 변환하는 AudioWorklet (contracts.md §4.1).
 *
 * 브라우저 샘플레이트(44.1k/48k 등)는 환경마다 다르므로 여기서 다운샘플링한다.
 * 리샘플링은 구간 평균(box filter) 방식 — 음성 인식 용도로 충분하다.
 */

export const TARGET_SAMPLE_RATE = 16_000
/** 100ms 단위로 전송 */
export const CHUNK_SAMPLES = 1_600

const workletSource = /* js */ `
class PcmEncoder extends AudioWorkletProcessor {
  constructor(options) {
    super()
    const { targetRate, chunkSamples } = options.processorOptions
    this.ratio = sampleRate / targetRate
    this.chunkSamples = chunkSamples
    this.buf = new Int16Array(chunkSamples)
    this.len = 0
    this.acc = 0
    this.count = 0
    this.t = 0
    this.last = 0
    this.port.onmessage = (e) => {
      if (e.data === 'flush' && this.len > 0) {
        this.port.postMessage(this.buf.slice(0, this.len).buffer)
        this.len = 0
      }
    }
  }

  push(v) {
    const s = Math.max(-1, Math.min(1, v))
    this.buf[this.len++] = s < 0 ? s * 0x8000 : s * 0x7fff
    if (this.len === this.chunkSamples) {
      this.port.postMessage(this.buf.buffer, [this.buf.buffer])
      this.buf = new Int16Array(this.chunkSamples)
      this.len = 0
    }
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0]
    if (!channel) return true
    for (let i = 0; i < channel.length; i++) {
      this.acc += channel[i]
      this.count++
      this.t += 1
      while (this.t >= this.ratio) {
        const v = this.count ? this.acc / this.count : this.last
        this.last = v
        this.acc = 0
        this.count = 0
        this.t -= this.ratio
        this.push(v)
      }
    }
    return true
  }
}
registerProcessor('pcm-encoder', PcmEncoder)
`

export interface PcmCapture {
  /** 남은 버퍼를 내보내고 마이크·오디오 컨텍스트를 정리한다 */
  stop: () => Promise<void>
}

export async function startPcmCapture(onChunk: (chunk: ArrayBuffer) => void): Promise<PcmCapture> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  })

  const context = new AudioContext()
  const moduleUrl = URL.createObjectURL(new Blob([workletSource], { type: 'text/javascript' }))
  try {
    await context.audioWorklet.addModule(moduleUrl)
  } finally {
    URL.revokeObjectURL(moduleUrl)
  }

  const source = context.createMediaStreamSource(stream)
  const encoder = new AudioWorkletNode(context, 'pcm-encoder', {
    numberOfInputs: 1,
    numberOfOutputs: 1,
    channelCount: 1,
    channelCountMode: 'explicit',
    processorOptions: { targetRate: TARGET_SAMPLE_RATE, chunkSamples: CHUNK_SAMPLES },
  })
  // 일부 브라우저는 출력이 연결되지 않은 노드를 처리하지 않으므로 무음 출력에 연결
  const mute = context.createGain()
  mute.gain.value = 0

  encoder.port.onmessage = (e: MessageEvent<ArrayBuffer>) => onChunk(e.data)
  source.connect(encoder).connect(mute).connect(context.destination)

  return {
    async stop() {
      encoder.port.postMessage('flush')
      await new Promise((r) => setTimeout(r, 50))
      encoder.port.onmessage = null
      source.disconnect()
      encoder.disconnect()
      stream.getTracks().forEach((t) => t.stop())
      await context.close()
    },
  }
}
