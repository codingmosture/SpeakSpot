import type { ApiMode } from '../api/client'

export type ServerStatus = 'checking' | 'online' | 'offline' | 'mock'

const STATUS_LABEL: Record<ServerStatus, string> = {
  checking: '서버 확인 중',
  online: '서버 연결됨',
  offline: '서버 연결 끊김',
  mock: 'Mock 모드 (백엔드 미사용)',
}

interface Props {
  serverStatus: ServerStatus
  mode: ApiMode
  developerOpen: boolean
  onOpenDeveloper: () => void
}

export function AppHeader({ serverStatus, mode, developerOpen, onOpenDeveloper }: Props) {
  return (
    <header className="app-header">
      <div className="brand">
        <span className="brand-mark" aria-hidden>
          <svg width="27" height="27" viewBox="0 0 28 28" fill="none">
            <path d="M6 11v6m5-11v16m6-13v10m5-7v4" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
          </svg>
        </span>
        <div>
          <h1>SpeakSpot</h1>
          <p>문서와 대화를 같은 흐름으로</p>
        </div>
      </div>
      <div className="header-actions">
        <span className={`status-badge ${serverStatus}`} role="status">
          <span className="dot" aria-hidden />
          {STATUS_LABEL[serverStatus]}
          {mode === 'legacy' && ' · 초안 API'}
        </span>
        <button type="button" className="btn ghost" aria-haspopup="dialog"
          aria-controls="developer-panel" aria-expanded={developerOpen} onClick={onOpenDeveloper}>
          개발자 메뉴 <span aria-hidden>≡</span>
        </button>
      </div>
    </header>
  )
}

export interface SttView {
  supported: boolean
  listening: boolean
  interim: string
  error: string | null
  start: () => void
  stop: () => void
}

interface RecordingProps {
  stt: SttView
  canSearch: boolean
}

export function RecordingToolbar({ stt, canSearch }: RecordingProps) {
  const sttActive = stt.listening
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
        {stt.listening ? '인식 중지' : '음성 인식'}
      </button>
      <p id="stt-caption" className="interim" aria-live="polite">
        {!canSearch
          ? '문서가 준비되면 사용할 수 있습니다.'
          : !stt.supported
            ? '이 브라우저는 음성 인식을 지원하지 않습니다. (Chrome/Edge 권장)'
            : stt.listening
              ? stt.interim || '듣고 있습니다…'
              : '마이크 버튼을 눌러 발화를 시작하세요.'}
      </p>
      {stt.error && <p className="error">{stt.error}</p>}
    </section>
  )
}
