export type SttSource = 'server' | 'browser'

export interface SttView {
  supported: boolean
  listening: boolean
  busy: boolean
  interim: string
  error: string | null
  start: () => void
  stop: () => void
}

interface Props {
  stt: SttView
  sttSource: SttSource
  canSearch: boolean
}

export function RecordingToolbar({ stt, sttSource, canSearch }: Props) {
  const sttActive = stt.listening || stt.busy
  return (
    <section className={`recording-toolbar ${sttActive ? 'is-active' : ''}`} aria-label="실시간 음성 인식">
      <button
        type="button"
        className={`btn mic ${stt.listening ? 'recording' : ''}`}
        onClick={sttActive ? stt.stop : stt.start}
        disabled={!sttActive && (!stt.supported || !canSearch)}
        aria-pressed={sttActive}
        aria-describedby="stt-caption"
      >
        <span className="mic-dot" aria-hidden />
        {stt.busy && !stt.listening
          ? '연결 중…'
          : stt.listening
            ? '인식 중지'
            : '음성 인식'}
      </button>
      <p id="stt-caption" className="interim" aria-live="polite">
        {!canSearch
          ? '문서가 준비되면 사용할 수 있습니다.'
          : !stt.supported
            ? sttSource === 'browser'
              ? '이 브라우저는 음성 인식을 지원하지 않습니다. (Chrome/Edge 권장)'
              : '음성 인식을 사용할 수 없습니다.'
            : stt.listening
              ? stt.interim || '듣고 있습니다…'
              : '마이크 버튼을 눌러 발화를 시작하세요.'}
      </p>
      {stt.error && <p className="error">{stt.error}</p>}
    </section>
  )
}
