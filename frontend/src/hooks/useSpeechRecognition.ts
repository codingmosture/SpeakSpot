import { useCallback, useEffect, useRef, useState } from 'react'
import { SpeechResultTracker, type SpeechRecognitionEventLike } from '../lib/speechResults'

/*
 * 브라우저 Web Speech API 기반 임시 STT.
 * 백엔드 STT(WebSocket 스트리밍)가 준비되면 같은 인터페이스
 * ({ listening, interim, start, stop })를 유지한 채 내부 구현만 교체한다.
 * Chrome / Edge 에서 동작한다.
 */

interface SpeechRecognitionErrorEventLike {
  error: string
}
interface SpeechRecognitionLike {
  lang: string
  continuous: boolean
  interimResults: boolean
  onresult: ((e: SpeechRecognitionEventLike) => void) | null
  onerror: ((e: SpeechRecognitionErrorEventLike) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
  abort: () => void
}
type SpeechRecognitionCtor = new () => SpeechRecognitionLike

function getRecognitionCtor(): SpeechRecognitionCtor | null {
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor
    webkitSpeechRecognition?: SpeechRecognitionCtor
  }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

const ERROR_MESSAGES: Record<string, string> = {
  'not-allowed': '마이크 권한이 거부되었습니다.',
  'service-not-allowed': '음성 인식 서비스를 사용할 수 없습니다.',
  'audio-capture': '마이크를 찾을 수 없습니다.',
  network: '음성 인식 네트워크 오류가 발생했습니다.',
}

interface Options {
  lang?: string
  /** 확정된 발화 문장이 나올 때마다 호출 */
  onFinal: (text: string) => void
  /** 매번 바뀌는 미확정 발화. 빈 문자열은 미확정 발화 종료. */
  onInterim?: (text: string) => void
  /** 한 번의 STT 결과 변경을 순서대로 전달한다. */
  onUpdate?: (text: string, isFinal: boolean, utteranceId: string) => void
}

export function useSpeechRecognition({ lang = 'ko-KR', onFinal, onInterim, onUpdate }: Options) {
  const supported = getRecognitionCtor() !== null
  const [listening, setListening] = useState(false)
  const [interim, setInterim] = useState('')
  const [error, setError] = useState<string | null>(null)

  const recognitionRef = useRef<SpeechRecognitionLike | null>(null)
  const tracker = useRef(new SpeechResultTracker())
  const wantListeningRef = useRef(false)
  const onFinalRef = useRef(onFinal)
  const onInterimRef = useRef(onInterim)
  const onUpdateRef = useRef(onUpdate)

  useEffect(() => {
    onFinalRef.current = onFinal
    onInterimRef.current = onInterim
    onUpdateRef.current = onUpdate
  }, [onFinal, onInterim, onUpdate])

  const start = useCallback(() => {
    const Ctor = getRecognitionCtor()
    if (!Ctor) {
      setError('이 브라우저는 음성 인식을 지원하지 않습니다. (Chrome/Edge 권장)')
      return
    }
    if (recognitionRef.current) return

    const recognition = new Ctor()
    recognition.lang = lang
    recognition.continuous = true
    recognition.interimResults = true

    recognition.onresult = (event) => {
      const frame = tracker.current.read(event)
      for (const update of frame.updates) {
        if (onUpdateRef.current) onUpdateRef.current(update.text, update.isFinal, update.utteranceId)
        else if (update.isFinal) onFinalRef.current(update.text)
      }
      setInterim(frame.caption)
      onInterimRef.current?.(frame.interim)
    }

    recognition.onerror = (event) => {
      // no-speech / aborted 는 정상적인 흐름에서도 발생하므로 무시
      if (event.error === 'no-speech' || event.error === 'aborted') return
      setError(ERROR_MESSAGES[event.error] ?? `음성 인식 오류: ${event.error}`)
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        wantListeningRef.current = false
      }
    }

    // 브라우저가 일정 시간 후 인식을 자동 종료하므로, 사용자가 끄지 않았다면 재시작
    recognition.onend = () => {
      if (wantListeningRef.current) {
        tracker.current.resetCycle()
        try {
          recognition.start()
          return
        } catch {
          /* fallthrough */
        }
      }
      recognitionRef.current = null
      wantListeningRef.current = false
      setListening(false)
      setInterim('')
      tracker.current.clearCaption()
      onInterimRef.current?.('')
      tracker.current.resetCycle()
    }

    recognitionRef.current = recognition
    wantListeningRef.current = true
    setError(null)
    tracker.current.clearCaption()
    tracker.current.resetCycle()
    setInterim('')
    onInterimRef.current?.('')
    try {
      recognition.start()
      setListening(true)
    } catch {
      recognitionRef.current = null
      wantListeningRef.current = false
      setError('음성 인식을 시작하지 못했습니다.')
    }
  }, [lang])

  const stop = useCallback(() => {
    wantListeningRef.current = false
    recognitionRef.current?.stop()
  }, [])

  useEffect(
    () => () => {
      wantListeningRef.current = false
      recognitionRef.current?.abort()
    },
    [],
  )

  return { supported, listening, interim, error, start, stop }
}
