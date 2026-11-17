import { useEffect, useId, useRef, useState } from 'react'
import type { SessionConfig, SearchStrategy } from '../types/contracts'

interface Props {
  open: boolean
  onClose: () => void
  sttActive: boolean
  documentReady: boolean
  sessionError: string | null
  onStopStt: () => void
  showBlocks: boolean
  onToggleBlocks: (value: boolean) => void
  autoFollow: boolean
  onAutoFollowChange: (value: boolean) => void
  sessionConfig: SessionConfig
  onConfigChange: (next: SessionConfig) => void
  onApplyConfig: () => void
  configBusy: boolean
  configDirty: boolean
  sttApplyMode: 'realtime' | '1s' | '2s' | 'sentence'
  onSttApplyModeChange: (mode: 'realtime' | '1s' | '2s' | 'sentence') => void
}

export function DeveloperPanel({
  open, onClose, sttActive,
  documentReady, sessionError, onStopStt, showBlocks, onToggleBlocks, autoFollow, onAutoFollowChange, sessionConfig, onConfigChange,
  onApplyConfig, configBusy, configDirty, sttApplyMode, onSttApplyModeChange,
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
  const choice = semantic && sessionConfig.search_model === 'jev'
  const ranked = !choice
  const local = algorithm !== 'global'
  const globalPages = algorithm === 'hybrid2' || (choice && algorithm !== 'local')

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
        <p className="hint">검색 반영 주기·블록 경계·자동 페이지 이동은 변경 즉시 적용됩니다.</p>
        <h3>검색 · 음성 인식</h3>
        {sttActive && (
          <div className="dev-status">
            <p className="hint">음성 인식 중입니다. 설정 적용은 인식을 중지한 뒤 사용할 수 있습니다.</p>
            <button type="button" className="btn ghost" onClick={onStopStt}>음성 인식 중지</button>
          </div>
        )}
        <SettingSelect label="검색 알고리즘" value={algorithm} disabled={configBusy}
          options={[
            ['sliding_queue', '슬라이딩 큐 (기본)'], ['local', 'Local'], ['global', 'Global'],
            ['hybrid1', 'Hybrid 1'], ['hybrid2', 'Hybrid 2'],
          ]}
          onChange={(value) => onConfigChange({
            ...sessionConfig, search_strategy: value as SearchStrategy,
            search_model: value === 'sliding_queue'
              ? (sessionConfig.search_model === 'e5-small' ? 'e5-small' : null)
              : (sessionConfig.search_model ?? 'e5-small'),
          })} />
        <SettingSelect label="검색 모델" value={sessionConfig.search_model ?? 'none'}
          disabled={configBusy}
          options={[
            ...(!semantic ? [['none', '사용 안 함 (기존 단어 비교)']] as const : []),
            ['e5-small', semantic ? 'E5-small' : 'E5-small · 단어 의미 비교'],
            ...(semantic ? [['bge-m3', 'BGE-M3'], ['jev', 'Jev (API 키 필요)']] as const : []),
          ]}
          hint={!semantic ? 'E5를 선택하면 발화 단어와 문서 단어의 의미 유사도로 일치를 판단합니다. 슬라이딩 큐 방식은 유지됩니다.' : undefined}
          onChange={(value) => changeConfig('search_model', value === 'none' ? null : value)} />
        <SettingSelect label="음성 인식 → 검색 반영 주기" value={sttApplyMode}
          options={[
            ['realtime', '0초 · 실시간 (기본)'], ['1s', '1초마다'],
            ['2s', '2초마다'], ['sentence', '문장마다 (확정 시)'],
          ]}
          hint="자막은 즉시 표시하며 선택한 주기에 따라 검색에 반영합니다."
          onChange={(value) => onSttApplyModeChange(value as typeof sttApplyMode)} />
        <div className="dev-numeric-grid">
          {([
            ['sliding_window_words', '큐 길이 (명사·영문 용어)', 1, 100, 1, !semantic],
            ['local_radius_pages', '탐색 반경 (페이지)', 0, 20, 1, local],
            ['word_match_threshold', 'E5 단어 일치 기준', 0, 1, 0.01, !semantic && sessionConfig.search_model === 'e5-small'],
            ['min_match_score', '최소 매칭 점수', 0, 1, 0.01, ranked],
            ['backward_penalty', '뒤로 이동 감점', 0, 1, 0.01, ranked],
            ['stay_bonus', '현재 페이지 보너스', 0, 1, 0.005, ranked],
            ['switch_margin', '페이지 전환 임계차', 0, 1, 0.01, ranked],
            ['prefix_bonus', '블록 앞부분 가중치', 0, 1, 0.005, !semantic],
            ['min_margin', '후보 최소 점수차', 0, 1, 0.01, semantic && ranked],
            ['global_top_k_pages', 'Global 후보 페이지 수', 1, 100, 1, globalPages],
            ['jev_min_confidence', 'Jev 최소 신뢰도', 0, 1, 0.05, choice],
            ['max_jev_candidates', 'Jev 최대 후보 블록', 1, 240, 1, choice],
          ] as const).filter(([, , , , , visible]) => visible).map(([key, label, min, max, step]) => (
            <label className="field" key={key}>
              {label}
              <input type="number" min={min} max={max} step={step}
                value={sessionConfig[key] ?? 0}
                disabled={configBusy}
                onChange={(e) => {
                  const n = e.target.valueAsNumber
                  if (Number.isFinite(n)) changeConfig(key, Math.max(min, Math.min(max, n)))
                }} />
            </label>
          ))}
        </div>
        <div className="config-apply">
          {sessionError && <p className="error" role="alert">{sessionError} 설정을 바꿔 다시 적용할 수 있습니다.</p>}
          <p className="hint">
            {configBusy
              ? '새 검색 세션을 준비하고 있습니다.'
              : !documentReady
                ? '설정을 저장하면 PDF 업로드 시 적용됩니다.'
                : configDirty
                  ? '변경한 설정이 아직 적용되지 않았습니다.'
                  : '현재 설정과 같습니다.'}
          </p>
          <button type="button" className="btn primary" onClick={onApplyConfig}
            disabled={(!configDirty && !sessionError) || configBusy || sttActive}>
            {configBusy ? '설정 준비 중…' : documentReady ? '설정 적용 · 세션 다시 시작' : '설정 저장'}
          </button>
        </div>
        <h3>진단</h3>
        <label className="toggle">
          <input type="checkbox" checked={showBlocks}
            onChange={(e) => onToggleBlocks(e.target.checked)} />
          블록 경계 표시
        </label>
        <label className="toggle">
          <input type="checkbox" checked={autoFollow}
            onChange={(event) => onAutoFollowChange(event.target.checked)} />
          매칭 위치로 자동 페이지 이동
        </label>
      </div>
    </dialog>
  )
}


type SelectOption = readonly [value: string, label: string, disabled?: boolean]

/** Render options inside the dialog; native select popups can fail in embedded browsers. */
function SettingSelect({ label, value, options, onChange, disabled = false, hint }: {
  label: string; value: string; options: readonly SelectOption[]
  onChange: (value: string) => void; disabled?: boolean; hint?: string
}) {
  const id = useId()
  const [expanded, setExpanded] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const visible = expanded && !disabled
  const close = () => { setExpanded(false); trigger.current?.focus() }

  useEffect(() => {
    if (visible) {
      const selected = list.current?.querySelector<HTMLButtonElement>('[aria-selected="true"]:not(:disabled)')
      const target = selected ?? list.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')
      target?.focus()
    }
  }, [visible])

  return (
    <div className="field">
      <span id={`${id}-label`}>{label}</span>
      <div className="setting-select"
        onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setExpanded(false) }}
        onKeyDown={(event) => {
          if (event.key === 'Escape' && visible) { event.preventDefault(); event.stopPropagation(); close(); return }
          if (event.key === 'Tab' && visible) { close(); return }
          if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
          event.preventDefault()
          if (!visible) { setExpanded(true); return }
          const buttons = Array.from(list.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? [])
          const current = buttons.indexOf(document.activeElement as HTMLButtonElement)
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
            : (current + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length
          buttons[next]?.focus()
        }}>
        <button ref={trigger} type="button" className="select-trigger" role="combobox"
          aria-labelledby={`${id}-label`} aria-controls={`${id}-options`} aria-expanded={visible}
          aria-haspopup="listbox" disabled={disabled} onClick={() => setExpanded(!visible)}>
          {options.find(([key]) => key === value)?.[1] ?? value}<span aria-hidden>⌄</span>
        </button>
        {visible && (
          <div ref={list} id={`${id}-options`} className="select-options" role="listbox" aria-labelledby={`${id}-label`}>
            {options.map(([key, text, unavailable]) => (
              <button key={key} type="button" role="option" tabIndex={-1} aria-selected={value === key}
                className="select-option" disabled={unavailable} onClick={() => { onChange(key); close() }}>
                {text}<span aria-hidden>{value === key ? '✓' : ''}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      {hint && <small className="hint">{hint}</small>}
    </div>
  )
}
