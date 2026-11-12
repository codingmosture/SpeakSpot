import { useRef, useState } from 'react'
import type { SessionConfig, SearchStrategy } from '../types/contracts'

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
  showBlocks: boolean
  onToggleBlocks: (value: boolean) => void
  sessionConfig: SessionConfig
  onConfigChange: (next: SessionConfig) => void
  onApplyConfig: () => void
  configBusy: boolean
  configDirty: boolean
  liveWords: string[]
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
    showBlocks,
    onToggleBlocks,
    sessionConfig,
    onConfigChange,
    onApplyConfig,
    configBusy,
    configDirty,
    liveWords,
  } = props

  const [text, setText] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)
  const sttActive = stt.listening || stt.busy
  const changeConfig = <K extends keyof SessionConfig>(key: K, value: SessionConfig[K]) =>
    onConfigChange({ ...sessionConfig, [key]: value })
  const algorithm = sessionConfig.search_strategy
  const semantic = algorithm !== 'sliding_queue'

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

      {/* 개발자 메뉴: 기본 설정을 유지하고, 실험할 때만 펼쳐 조절한다. */}
      {(
        <details className="control-group dev-tools dev-details">
          <summary>개발자 메뉴 · 검색 / STT 설정</summary>
          <p className="hint">설정은 새 검색 세션을 생성할 때 적용됩니다. 기존 PDF는 다시 업로드할 필요 없습니다.</p>
          <label className="field">
            검색 알고리즘
            <select value={algorithm} disabled={sttActive || configBusy}
              onChange={(e) => {
                const strategy = e.target.value as SearchStrategy
                onConfigChange({
                  ...sessionConfig,
                  search_strategy: strategy,
                  search_model: strategy === 'sliding_queue' ? null : (sessionConfig.search_model ?? 'e5-small'),
                })
              }}>
              <option value="sliding_queue">슬라이딩 큐 (기본)</option>
              <option value="local">Local</option>
              <option value="global">Global</option>
              <option value="hybrid1">Hybrid 1</option>
              <option value="hybrid2">Hybrid 2</option>
            </select>
          </label>
          <label className="field">
            검색 모델
            <select value={sessionConfig.search_model ?? 'none'} disabled={!semantic || sttActive || configBusy}
              onChange={(e) => changeConfig('search_model', e.target.value)}>
              <option value="none" disabled>사용 안 함 (슬라이딩 큐)</option>
              <option value="e5-small">E5-small</option>
              <option value="bge-m3">BGE-M3</option>
              <option value="jev">Jev (API 키 필요)</option>
            </select>
          </label>
          <label className="field">STT 소스
            <select value={sttSource} disabled={sttActive || configBusy}
              onChange={(e) => onSttSourceChange(e.target.value as SttSource)}>
              <option value="browser">브라우저 STT (기본)</option>
              <option value="server" disabled={!serverSttAvailable}>서버 STT (Whisper)</option>
            </select>
          </label>
          <label className="field">서버 STT 모델
            <select value={sessionConfig.stt_model} disabled={sttActive || configBusy}
              onChange={(e) => changeConfig('stt_model', e.target.value)}>
              <option value="faster-whisper-tiny">Faster Whisper tiny</option>
              <option value="faster-whisper-base">Faster Whisper base</option>
              <option value="faster-whisper-small">Faster Whisper small</option>
              <option value="faster-whisper-medium">Faster Whisper medium</option>
            </select>
          </label>
          <div className="dev-numeric-grid">
            {([
              ['sliding_window_words', '큐 길이 (단어)', 1, 100, 1],
              ['local_radius_pages', '탐색 반경 (페이지)', 0, 20, 1],
              ['min_match_score', '최소 매칭 점수', 0, 1, 0.01],
              ['backward_penalty', '뒤로 이동 감점', 0, 1, 0.01],
              ['stay_bonus', '현재 페이지 보너스', 0, 1, 0.005],
              ['switch_margin', '페이지 전환 임계차', 0, 1, 0.01],
              ['prefix_bonus', '블록 앞부분 가중치', 0, 1, 0.005],
              ['min_margin', '후보 최소 점수차', 0, 1, 0.01],
              ['global_top_k_pages', 'Global 후보 페이지 수', 1, 100, 1],
              ['jev_min_confidence', 'Jev 최소 신뢰도', 0, 1, 0.05],
              ['max_jev_candidates', 'Jev 최대 후보 블록', 1, 240, 1],
              ['stt_buffer_seconds', 'STT 음성 버퍼 (초)', 1, 30, 0.5],
              ['stt_update_interval_seconds', 'STT 갱신 간격 (초)', 0.2, 10, 0.2],
            ] as const).map(([key, label, min, max, step]) => (
              <label className="field" key={key}>
                {label}
                <input type="number" min={min} max={max} step={step}
                  value={sessionConfig[key] ?? 0}
                  disabled={sttActive || configBusy}
                  onChange={(e) => {
                    const n = e.target.valueAsNumber
                    if (Number.isFinite(n)) changeConfig(key, Math.max(min, Math.min(max, n)))
                  }} />
              </label>
            ))}
          </div>
          <button type="button" className="btn primary" onClick={onApplyConfig}
            disabled={!configDirty || configBusy || sttActive || !canSearch}>
            {configBusy ? '새 세션 생성 중…' : '설정 적용 · 세션 다시 시작'}
          </button>
          <label className="toggle">
            <input type="checkbox" checked={showBlocks}
              onChange={(e) => onToggleBlocks(e.target.checked)} />
            블록 경계 표시
          </label>
          <div className="dev-queue">
            <strong>실시간 단어 큐 ({liveWords.length}/{sessionConfig.sliding_window_words ?? 15})</strong>
            <p>{liveWords.length ? liveWords.join(' · ') : '마이크를 켜면 최근 단어가 표시됩니다.'}</p>
          </div>
        </details>
      )}
    </section>
  )
}
