import { useEffect, useRef } from 'react'
import type { SessionConfig, SearchStrategy } from '../types/contracts'
import type { SttSource } from './RecordingToolbar'

interface Props {
  open: boolean
  onClose: () => void
  sttActive: boolean
  sttSource: SttSource
  onSttSourceChange: (source: SttSource) => void
  serverSttAvailable: boolean
  canSearch: boolean
  showBlocks: boolean
  onToggleBlocks: (value: boolean) => void
  sessionConfig: SessionConfig
  onConfigChange: (next: SessionConfig) => void
  onApplyConfig: () => void
  configBusy: boolean
  configDirty: boolean
  liveWords: string[]
  sttApplyMode: 'realtime' | '1s' | '2s' | 'sentence'
  onSttApplyModeChange: (mode: 'realtime' | '1s' | '2s' | 'sentence') => void
}

export function DeveloperPanel({
  open, onClose, sttActive, sttSource, onSttSourceChange, serverSttAvailable,
  canSearch, showBlocks, onToggleBlocks, sessionConfig, onConfigChange,
  onApplyConfig, configBusy, configDirty, liveWords, sttApplyMode, onSttApplyModeChange,
}: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = dialogRef.current
    if (!open || !dialog) return
    dialog.showModal()
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      dialog.close()
      document.body.style.overflow = previousOverflow
    }
  }, [open])

  const changeConfig = <K extends keyof SessionConfig>(key: K, value: SessionConfig[K]) =>
    onConfigChange({ ...sessionConfig, [key]: value })
  const algorithm = sessionConfig.search_strategy
  const semantic = algorithm !== 'sliding_queue'

  return (
    <dialog ref={dialogRef} id="developer-panel" className="developer-drawer"
      aria-labelledby="developer-title" aria-describedby="developer-description"
      onCancel={onClose}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return
        const rect = event.currentTarget.getBoundingClientRect()
        if (event.clientX < rect.left || event.clientX > rect.right ||
            event.clientY < rect.top || event.clientY > rect.bottom) onClose()
      }}>
      <div className="drawer-header">
        <div><p className="eyebrow">WORKSPACE SETTINGS</p><h2 id="developer-title">개발자 메뉴</h2></div>
        <button type="button" className="btn ghost icon-button" aria-label="개발자 메뉴 닫기" onClick={onClose} autoFocus>×</button>
      </div>
      <div className="drawer-body">
        <p className="hint" id="developer-description">세션 설정을 적용하면 타임라인이 초기화되고 새 검색 세션이 시작됩니다. PDF는 유지됩니다.</p>
        <p className="hint">STT 소스, 검색 반영 주기, 블록 경계 표시는 변경 즉시 적용됩니다.</p>
        <h3>검색 · 음성 인식</h3>
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
        <label className="field">
          STT → 검색 큐 반영 주기
          <select value={sttApplyMode}
            onChange={(e) => onSttApplyModeChange(e.target.value as typeof sttApplyMode)}>
            <option value="realtime">0초 · 실시간 (기본)</option>
            <option value="1s">1초마다</option>
            <option value="2s">2초마다</option>
            <option value="sentence">문장마다 (확정 시)</option>
          </select>
          <small className="hint">브라우저 STT가 인식 결과를 전달한 뒤 검색에 반영하는 주기입니다. 자막은 항상 즉시 표시됩니다. 서버 Whisper에는 적용되지 않습니다.</small>
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
            ['stt_buffer_seconds', '서버 Whisper 음성 버퍼 (초)', 1, 30, 0.5],
            ['stt_update_interval_seconds', '서버 Whisper 갱신 간격 (초)', 0.2, 10, 0.2],
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
        <div className="config-apply">
          <p className="hint">
            {configBusy
              ? '새 검색 세션을 준비하고 있습니다.'
              : !canSearch
                ? '문서가 준비되면 설정을 적용할 수 있습니다.'
                : configDirty
                  ? '변경한 설정이 아직 적용되지 않았습니다.'
                  : '현재 세션 설정과 같습니다.'}
          </p>
          <button type="button" className="btn primary" onClick={onApplyConfig}
            disabled={!configDirty || configBusy || sttActive || !canSearch}>
            {configBusy ? '새 세션 생성 중…' : '설정 적용 · 세션 다시 시작'}
          </button>
        </div>
        <h3>진단</h3>
        <label className="toggle">
          <input type="checkbox" checked={showBlocks}
            onChange={(e) => onToggleBlocks(e.target.checked)} />
          블록 경계 표시
        </label>
        <div className="dev-queue">
          <strong>실시간 단어 큐 ({liveWords.length}/{sessionConfig.sliding_window_words ?? 15})</strong>
          <p>{liveWords.length ? liveWords.join(' · ') : '마이크를 켜면 최근 단어가 표시됩니다.'}</p>
        </div>
      </div>
    </dialog>
  )
}
