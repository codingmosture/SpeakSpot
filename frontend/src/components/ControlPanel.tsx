import { useRef, useState } from 'react'

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
  /* 문서 */
  fileName: string | null
  documentBusy: boolean
  onFileSelect: (file: File) => void
  /* STT */
  sttSource: SttSource
  onSttSourceChange: (source: SttSource) => void
  serverSttAvailable: boolean
  stt: SttView
  /* 수동 탐색 */
  canSearch: boolean
  onSubmitText: (text: string) => void
  autoFollow: boolean
  onAutoFollowChange: (value: boolean) => void
  /* 개발 도구 */
  devTools: boolean
  showBlocks: boolean
  onToggleBlocks: (value: boolean) => void
}

export function ControlPanel(props: Props) {
  const {
    fileName,
    documentBusy,
    onFileSelect,
    sttSource,
    onSttSourceChange,
    serverSttAvailable,
    stt,
    canSearch,
    onSubmitText,
    autoFollow,
    onAutoFollowChange,
    devTools,
    showBlocks,
    onToggleBlocks,
  } = props

  const [text, setText] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)
  const sttActive = stt.listening || stt.busy

  return (
    <section className="panel control-panel">
      {/* 문서 */}
      <div className="control-group">
        <h2>문서</h2>
        <div className="file-row">
          <span className="file-name" title={fileName ?? undefined}>
            {documentBusy ? '업로드·분석 중…' : (fileName ?? '선택된 문서 없음')}
          </span>
          <button
            type="button"
            className="btn secondary"
            disabled={documentBusy || sttActive}
            onClick={() => fileInputRef.current?.click()}
          >
            PDF 업로드
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept="application/pdf"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) onFileSelect(file)
              e.target.value = ''
            }}
          />
        </div>
      </div>

      {/* 음성 인식 */}
      <div className="control-group">
        <h2>실시간 음성 인식</h2>
        <button
          type="button"
          className={`btn mic ${stt.listening ? 'recording' : ''}`}
          onClick={sttActive ? stt.stop : stt.start}
          disabled={!stt.supported || !canSearch}
        >
          <span className="mic-dot" aria-hidden />
          {stt.busy && !stt.listening
            ? '연결 중…'
            : stt.listening
              ? '인식 중지'
              : '음성 인식 시작'}
        </button>
        <p className="interim" aria-live="polite">
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
      </div>

      {/* 텍스트로 찾기 */}
      <div className="control-group">
        <h2>텍스트로 찾기</h2>
        <form
          className="text-form"
          onSubmit={(e) => {
            e.preventDefault()
            const value = text.trim()
            if (!value || !canSearch) return
            onSubmitText(value)
            setText('')
          }}
        >
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="찾을 내용을 입력하세요"
            aria-label="찾을 내용"
          />
          <button type="submit" className="btn primary" disabled={!text.trim() || !canSearch}>
            찾기
          </button>
        </form>
        <label className="toggle follow">
          <input
            type="checkbox"
            checked={autoFollow}
            onChange={(e) => onAutoFollowChange(e.target.checked)}
          />
          매칭 위치로 자동 페이지 이동
        </label>
      </div>

      {/* 개발 도구 — VITE_DEV_TOOLS=true 일 때만 표시 */}
      {devTools && (
        <div className="control-group dev-tools">
          <h2>개발 도구</h2>
          <span className="field-label">STT 소스</span>
          <div className="segmented two" role="radiogroup" aria-label="STT 소스">
            <button
              type="button"
              role="radio"
              aria-checked={sttSource === 'server'}
              className={sttSource === 'server' ? 'active' : ''}
              disabled={!serverSttAvailable || sttActive}
              title={serverSttAvailable ? '' : 'VITE_API_MODE=server 에서만 사용할 수 있습니다'}
              onClick={() => onSttSourceChange('server')}
            >
              서버 STT
            </button>
            <button
              type="button"
              role="radio"
              aria-checked={sttSource === 'browser'}
              className={sttSource === 'browser' ? 'active' : ''}
              disabled={sttActive}
              onClick={() => onSttSourceChange('browser')}
            >
              브라우저 STT
            </button>
          </div>
          <label className="toggle">
            <input
              type="checkbox"
              checked={showBlocks}
              onChange={(e) => onToggleBlocks(e.target.checked)}
            />
            블록 경계 표시
          </label>
        </div>
      )}
    </section>
  )
}
