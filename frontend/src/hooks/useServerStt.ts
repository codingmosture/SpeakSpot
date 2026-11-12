import { useCallback, useEffect, useRef, useState } from 'react'
import { CHUNK_SAMPLES, startPcmCapture, type PcmCapture } from '../lib/pcmWorklet'
import type {
  ClientMessage,
  SearchResult,
  ServerMessage,
  STTSegment,
} from '../types/contracts'

/*
 * 서버 STT — WebSocket 실시간 스트리밍 (contracts.md §4).
 *
 *   connect → session.start → (session.ready) → PCM 바이너리 프레임 …
 *   ← stt.result / highlight.update / error
 *   session.stop → 남은 결과 수신 후 종료
 */

export type ServerSttPhase = 'idle' | 'connecting' | 'listening' | 'stopping'

/** session.stop 후 남은 결과를 기다리는 시간 */
const DRAIN_TIMEOUT_MS = 3_000
// 첫 세션의 로컬 STT 모델 초기화에는 수십 초가 걸릴 수 있다.
const READY_TIMEOUT_MS = 180_000

interface Options {
  url: string | null
  onSegment: (segment: STTSegment) => void
  onHighlight: (result: SearchResult) => void
}

export function useServerStt({ url, onSegment, onHighlight }: Options) {
  const [phase, setPhase] = useState<ServerSttPhase>('idle')
  const [interim, setInterim] = useState('')
  const [error, setError] = useState<string | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  const captureRef = useRef<PcmCapture | null>(null)
  const callbacks = useRef({ onSegment, onHighlight })
  useEffect(() => {
    callbacks.current = { onSegment, onHighlight }
  }, [onSegment, onHighlight])

  const cleanup = useCallback(async () => {
    const capture = captureRef.current
    captureRef.current = null
    await capture?.stop().catch(() => undefined)
    const ws = wsRef.current
    wsRef.current = null
    if (ws && ws.readyState <= WebSocket.OPEN) ws.close()
    setInterim('')
    setPhase('idle')
  }, [])

  const start = useCallback(async () => {
    if (!url || wsRef.current) return
    setError(null)
    setPhase('connecting')

    const ws = new WebSocket(url)
    ws.binaryType = 'arraybuffer'
    wsRef.current = ws
    const send = (msg: ClientMessage) => ws.send(JSON.stringify(msg))

    const readyTimer = setTimeout(() => {
      setError('서버 STT 연결이 준비되지 않았습니다. (session.ready 시간 초과)')
      void cleanup()
    }, READY_TIMEOUT_MS)

    ws.onopen = () =>
      send({
        type: 'session.start',
        payload: { encoding: 'pcm_s16le', sample_rate: 16000, channels: 1 },
      })

    ws.onmessage = async (event) => {
      if (typeof event.data !== 'string') return
      let msg: ServerMessage
      try {
        msg = JSON.parse(event.data) as ServerMessage
      } catch {
        return
      }

      switch (msg.type) {
        case 'session.ready': {
          clearTimeout(readyTimer)
          try {
            const capture = await startPcmCapture((chunk) => {
              if (ws.readyState === WebSocket.OPEN) ws.send(chunk)
            })
            // 마이크 권한을 기다리는 사이에 중지되었거나 연결이 끊겼으면 마이크를 바로 끈다
            if (wsRef.current !== ws || ws.readyState !== WebSocket.OPEN) {
              void capture.stop()
              return
            }
            captureRef.current = capture
            setPhase('listening')
          } catch {
            setError('마이크를 사용할 수 없습니다. 브라우저 권한을 확인하세요.')
            void cleanup()
          }
          break
        }
        case 'stt.result':
          if (msg.payload.is_final) {
            setInterim('')
            callbacks.current.onSegment(msg.payload)
          } else {
            setInterim(msg.payload.text)
          }
          break
        case 'highlight.update':
          callbacks.current.onHighlight(msg.payload)
          break
        case 'error':
          setError(`${msg.payload.message} (${msg.payload.code})`)
          break
      }
    }

    ws.onerror = () => setError('STT 서버와의 연결에 실패했습니다.')
    ws.onclose = () => {
      clearTimeout(readyTimer)
      if (wsRef.current === ws) void cleanup()
    }
  }, [url, cleanup])

  const stop = useCallback(async () => {
    const ws = wsRef.current
    if (!ws) return
    // 아직 연결 준비 중이면 기다리지 않고 바로 종료
    if (ws.readyState !== WebSocket.OPEN || !captureRef.current) {
      void cleanup()
      return
    }
    setPhase('stopping')
    await captureRef.current?.stop().catch(() => undefined)
    captureRef.current = null
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'session.stop', payload: {} } satisfies ClientMessage))
      // 서버가 남은 결과를 보내고 닫을 때까지 잠시 대기
      setTimeout(() => {
        if (wsRef.current === ws) void cleanup()
      }, DRAIN_TIMEOUT_MS)
    } else {
      void cleanup()
    }
  }, [cleanup])

  // 세션(=URL)이 바뀌거나 언마운트되면 연결 종료
  useEffect(() => {
    return () => {
      void cleanup()
    }
  }, [url, cleanup])

  return {
    supported: url !== null && typeof AudioWorkletNode !== 'undefined',
    phase,
    listening: phase === 'listening',
    interim,
    error,
    start,
    stop,
    chunkMs: (CHUNK_SAMPLES / 16_000) * 1000,
  }
}
