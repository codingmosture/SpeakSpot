import type { TimelineEntry } from '../types/contracts'

const STAGE_LABEL = { local: 'Local', global: 'Global', none: '-' } as const

export function ResultCard({ entry }: { entry: TimelineEntry | null }) {
  const r = entry?.result

  return (
    <section className="panel result-card">
      <h2>최근 탐색 결과</h2>
      {!entry ? (
        <p className="muted">아직 탐색한 발화가 없습니다.</p>
      ) : (
        <>
          <blockquote>“{entry.text}”</blockquote>
          {entry.pending ? (
            <p className="muted">탐색 중…</p>
          ) : entry.error ? (
            <p className="error">{entry.error}</p>
          ) : r ? (
            <>
              {r.status === 'NONE' && (
                <p className="none-notice">일치하는 위치를 찾지 못했습니다. 이전 하이라이트를 유지합니다.</p>
              )}
              <dl className="stats">
                <Stat label="상태" value={r.status} />
                <Stat label="페이지" value={r.page ?? '-'} />
                <Stat label="블록" value={r.block_id ?? '-'} />
                <Stat label="점수" value={r.score != null ? r.score.toFixed(3) : '-'} />
                <Stat label="탐색 단계" value={STAGE_LABEL[r.stage_used]} />
                <Stat label="모델" value={r.model} />
                <Stat label="탐색 시간" value={`${r.processing_ms} ms`} />
                <Stat
                  label={entry.source === 'server-stt' ? 'STT→하이라이트' : '왕복 시간'}
                  value={entry.roundTripMs != null ? `${entry.roundTripMs} ms` : '-'}
                />
              </dl>
            </>
          ) : null}
        </>
      )}
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
