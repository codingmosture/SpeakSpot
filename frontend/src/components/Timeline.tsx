import type { EntrySource, TimelineEntry } from '../types/contracts'

interface Props {
  entries: TimelineEntry[]
  activeId: string | null
  onSelect: (entry: TimelineEntry) => void
  onClear: () => void
}

const SOURCE_ICON: Record<EntrySource, string> = {
  'server-stt': '🎙',
  'browser-stt': '🎤',
  manual: '⌨',
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
        <h2>타임라인</h2>
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
        <p className="muted">발화가 기록되면 여기에 표시됩니다.</p>
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
                  onClick={() => onSelect(entry)}
                >
                  <span className="t-meta">
                    <span>{formatTime(entry.createdAt)}</span>
                    <span>{SOURCE_ICON[entry.source]}</span>
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
