import type { EntrySource, TimelineEntry } from '../types/contracts'

interface Props {
  entries: TimelineEntry[]
  activeId: string | null
  onSelect: (entry: TimelineEntry) => void
  onClear: () => void
}

const SOURCE_LABEL: Record<EntrySource, string> = {
  'server-stt': '서버 음성',
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
        <div className="timeline-empty"><p>대화의 흐름을 기록합니다</p><span>음성 인식을 시작하거나 내용을 검색하세요.<br />기록을 선택하면 해당 위치로 이동합니다.</span></div>
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
