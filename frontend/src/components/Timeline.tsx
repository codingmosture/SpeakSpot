import type { Block, EntrySource, TimelineEntry } from '../types/contracts'

interface Props {
  entries: TimelineEntry[]
  activeId: string | null
  onSelect: (entry: TimelineEntry) => void
  onClear: () => void
}

const SOURCE_LABEL: Record<EntrySource, string> = {
  'browser-stt': '음성',
  manual: '텍스트',
}

function formatTime(ms: number) {
  return new Date(ms).toLocaleTimeString('ko-KR', { hour12: false })
}

function statusOf(entry: TimelineEntry) {
  if (entry.pending) return 'pending'
  if (entry.error) return 'error'
  return entry.result?.status === 'MATCH' ? 'match' : 'none'
}

export function Timeline({ entries, activeId, onSelect, onClear }: Props) {
  const done = entries.filter((e) => e.result)
  const matched = done.filter((e) => e.result?.status === 'MATCH')
  const globalCalls = done.filter((e) => e.result?.stage_used === 'global').length
  const avg = (values: number[]) =>
    values.length ? Math.round(values.reduce((a, b) => a + b, 0) / values.length) : null
  const avgProcessing = avg(done.map((e) => e.result!.processing_ms))

  return (
    <section className="panel timeline">
      <div className="timeline-head">
        <h2>타임라인 <span className="entry-count">{entries.length}</span></h2>
        {entries.length > 0 && (
          <button type="button" className="btn ghost small" onClick={onClear}>
            초기화
          </button>
        )}
      </div>

      {entries.length > 0 && (
        <p className="timeline-summary">
          발화 {entries.length} · 매칭 {matched.length} · Global 사용 {globalCalls}
          {avgProcessing != null && ` · 평균 탐색 ${avgProcessing} ms`}
        </p>
      )}

      {entries.length === 0 ? (
        <div className="timeline-empty"><p>확정된 발화가 여기에 쌓입니다</p><span>음성 인식을 시작해보세요.<br />기록을 선택하면 해당 위치로 이동합니다.</span></div>
      ) : (
        <ol className="timeline-list">
          {[...entries].reverse().map((entry) => {
            const status = statusOf(entry)
            return (
              <li key={entry.id}>
                <button
                  type="button"
                  className={`timeline-item ${status} ${entry.id === activeId ? 'active' : ''}`}
                  disabled={status !== 'match'}
                  aria-current={entry.id === activeId ? 'true' : undefined}
                  onClick={() => onSelect(entry)}
                >
                  <span className="t-meta">
                    <span>{formatTime(entry.createdAt)}</span>
                    <span>{SOURCE_LABEL[entry.source]}</span>
                  </span>
                  <span className="t-text">{entry.text}</span>
                  <span className={`t-badge ${status}`}>
                    {status === 'match' &&
                      `p.${entry.result?.page}${entry.result?.stage_used === 'global' ? ' · G' : ''}`}
                    {status === 'none' && '미검출'}
                    {status === 'pending' && '탐색 중'}
                    {status === 'error' && '오류'}
                  </span>
                </button>
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}


const STAGE_LABEL = { sliding_queue: 'Sliding Queue', local: 'Local', global: 'Global', none: '-' } as const

export function LiveMatchPanel({ entry, words, windowSize, queueEnabled, block, retainedBlock, listening, ready }: {
  entry: TimelineEntry | null
  words: string[]
  windowSize: number
  queueEnabled: boolean
  block: Block | null
  retainedBlock: Block | null
  listening: boolean
  ready: boolean
}) {
  const r = entry?.result
  const selected = block ?? retainedBlock
  const status = entry?.pending ? 'pending' : entry?.error ? 'error'
    : r?.status === 'MATCH' ? 'match' : r ? 'none' : 'idle'
  const label = { pending: '판단 중', error: '오류', match: '블록 매칭', none: '위치 미확정', idle: '대기 중' }[status]

  return (
    <section className={`panel live-match ${status}`} aria-labelledby="live-match-title">
      <div className="match-heading">
        <h2 id="live-match-title">실시간 매칭</h2>
        <span className={`live-indicator ${listening ? 'listening' : ''}`}>
          <span className="dot" aria-hidden />{listening ? '듣는 중' : '음성 대기'}
        </span>
      </div>
      {queueEnabled && <div className="match-queue">
        <div className="match-section-label"><span>{entry?.pending ? '명사 큐 갱신 중' : '비교 중인 명사'}</span><span>{words.length}<small> / {windowSize}</small></span></div>
        {words.length ? (
          <ol className="noun-chips" aria-label="실시간 명사 큐">
            {words.map((word, index) => <li key={`${index}-${word}`}>{word}</li>)}
          </ol>
        ) : <p className="queue-empty">말씀하신 명사와 영문 용어가 여기에 표시됩니다.</p>}
      </div>}
      <div className="match-decision" aria-live="polite" aria-atomic="true">
        <div className="match-section-label">
          <span className={`decision-status ${status}`}><span className="dot" aria-hidden />{label}</span>
          {r?.score != null && <span className="match-score">점수 {r.score.toFixed(3)}</span>}
        </div>
        {selected ? (
          <div className={`matched-block ${status === 'match' ? 'current' : 'retained'}`}>
            <div className="matched-block-meta"><span className="page-pill">p.{selected.page}</span>
              <span>{status === 'match' ? '선택된 블록' : '유지 중인 블록'}</span>
            </div>
            <p className="matched-block-text" key={selected.block_id}>{selected.text}</p>
          </div>
        ) : <div className="match-placeholder"><span aria-hidden>⌁</span><p>{!ready ? 'PDF를 열어 시작하세요' : listening ? '말씀하시면 문서의 블록을 찾습니다' : '음성 인식을 시작하세요'}</p></div>}
        {status === 'none' && <p className="match-note">이번 발화로 새 위치가 확정되지 않았습니다.{selected && ' 이전 강조는 유지합니다.'}</p>}
        {entry?.error && <p className="error">{entry.error}</p>}
      </div>
      {entry && <details className="match-details">
        <summary>탐색 상세</summary>
        <blockquote>“{entry.text}”</blockquote>
        {r && <dl className="stats">
          <Stat label="상태" value={r.status} />
          <Stat label="블록" value={r.block_id ?? '-'} />
          <Stat label="탐색 단계" value={STAGE_LABEL[r.stage_used]} />
          <Stat label="모델" value={r.model} />
          <Stat label="탐색 시간" value={`${r.processing_ms} ms`} />
          <Stat label="왕복 시간" value={entry.roundTripMs != null ? `${entry.roundTripMs} ms` : '-'} />
        </dl>}
      </details>}
    </section>
  )
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd title={String(value)}>{value}</dd>
    </div>
  )
}
