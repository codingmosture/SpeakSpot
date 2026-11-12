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
          ◎
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
