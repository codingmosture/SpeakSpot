import type { ApiMode } from '../api/client'

export type ServerStatus = 'checking' | 'online' | 'offline' | 'mock'

const STATUS_LABEL: Record<ServerStatus, string> = {
  checking: '서버 확인 중',
  online: '서버 연결됨',
  offline: '서버 연결 끊김',
  mock: 'Mock 모드 (백엔드 미사용)',
}

export function AppHeader({ serverStatus, mode }: { serverStatus: ServerStatus; mode: ApiMode }) {
  return (
    <header className="app-header">
      <div className="brand">
        <span className="brand-mark" aria-hidden>
          ◎
        </span>
        <div>
          <h1>SpeakSpot</h1>
          <p>Follow the speech. Find the spot.</p>
        </div>
      </div>
      <span className={`status-badge ${serverStatus}`}>
        <span className="dot" aria-hidden />
        {STATUS_LABEL[serverStatus]}
        {mode === 'legacy' && ' · 초안 API'}
      </span>
    </header>
  )
}
