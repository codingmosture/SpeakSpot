import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError } from './api/client'
import { AppHeader, type ServerStatus } from './components/AppHeader'
import { ControlPanel, type SttSource, type SttView } from './components/ControlPanel'
import { PdfViewer } from './components/PdfViewer'
import { ResultCard } from './components/ResultCard'
import { Timeline } from './components/Timeline'
import { DEFAULT_SESSION_CONFIG, DEV_TOOLS, HEALTH_POLL_MS } from './config'
import { useServerStt } from './hooks/useServerStt'
import { useSpeechRecognition } from './hooks/useSpeechRecognition'
import type { PDFDocumentProxy } from './lib/pdf'
import type {
  Block,
  Document,
  EntrySource,
  SearchResult,
  Session,
  STTSegment,
  TimelineEntry,
} from './types/contracts'

const errorMessage = (err: unknown, fallback: string) =>
  err instanceof ApiError ? err.message : fallback

function App() {
  /* ---------- 서버 상태 ---------- */
  const [health, setHealth] = useState<ServerStatus>('checking')
  const serverStatus: ServerStatus = api.mode === 'mock' ? 'mock' : health

  useEffect(() => {
    if (api.mode === 'mock') return
    let alive = true
    const check = () =>
      api
        .health()
        .then(() => alive && setHealth('online'))
        .catch(() => alive && setHealth('offline'))
    void check()
    const timer = setInterval(check, HEALTH_POLL_MS)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  const [notice, setNotice] = useState<string | null>(null)

  /* ---------- 문서 · 블록 · PDF ---------- */
  const [activeDoc, setActiveDoc] = useState<Document | null>(null)
  const [blocks, setBlocks] = useState<Block[]>([])
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null)
  const [documentBusy, setDocumentBusy] = useState(false)

  const [pageNumber, setPageNumber] = useState(1)

  // Mock·초안 모드에서는 업로드 없이 기본 문서로 시작
  useEffect(() => {
    if (!api.initialDocument) return
    let cancelled = false
    api
      .initialDocument()
      .then(async (doc) => {
        const res = await api.getBlocks(doc.document_id)
        if (cancelled) return
        setActiveDoc(doc)
        setBlocks(res.blocks)
        setPageNumber(res.blocks[0]?.page ?? 1)
      })
      .catch((err) => !cancelled && setNotice(errorMessage(err, '기본 문서 블록을 불러오지 못했습니다.')))
    return () => {
      cancelled = true
    }
  }, [])

  const blockMap = useMemo(() => new Map(blocks.map((b) => [b.block_id, b])), [blocks])

  /* ---------- 실시간 미확정 발화 하이라이트 ----------
   * STT의 중간 문장은 계속 수정된다. 실제 검색 큐에는 확정 문장만 한 번 추가하고,
   * 중간 문장은 /search/preview로 별도 조회하여 임시로 하이라이트한다.
   */
  const [previewResult, setPreviewResult] = useState<SearchResult | null>(null)
  const previewVersion = useRef(0)
  const previewTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const previewText = useRef('')
  const previewLastRequest = useRef(0)
  const clearPreview = useCallback(() => {
    previewVersion.current++
    previewText.current = ''
    if (previewTimer.current !== null) {
      clearTimeout(previewTimer.current)
      previewTimer.current = null
    }
    setPreviewResult(null)
  }, [])

  /* ---------- 뷰어 ---------- */
  const [highlight, setHighlight] = useState<{ blockId: string; entryId: string } | null>(null)
  const [showBlocks, setShowBlocks] = useState(false)
  const [autoFollow, setAutoFollow] = useState(true)

  const pageBlocks = useMemo(
    () => blocks.filter((b) => b.page === pageNumber),
    [blocks, pageNumber],
  )
  const committedHighlightBlock = highlight ? (blockMap.get(highlight.blockId) ?? null) : null
  const livePreviewBlock = previewResult?.status === 'MATCH' && previewResult.block_id
    ? (blockMap.get(previewResult.block_id) ?? null)
    : null
  const highlightBlock = livePreviewBlock ?? committedHighlightBlock

  /* ---------- 타임라인 ---------- */
  const [entries, setEntries] = useState<TimelineEntry[]>([])
  const seqOf = useRef(new Map<string, number>())
  const nextSeq = useRef(0)
  const lastAppliedSeq = useRef(0)
  const sttFinalAt = useRef(new Map<string, number>())

  const resetTimeline = useCallback(() => {
    setEntries([])
    setHighlight(null)
    clearPreview()
    seqOf.current.clear()
    sttFinalAt.current.clear()
    lastAppliedSeq.current = 0
  }, [clearPreview])

  /* ---------- 세션 (contracts.md §3.4) ---------- */
  const [sessionState, setSessionState] = useState<{
    key: string
    session: Session | null
    error: string | null
  } | null>(null)

  const sessionKey = activeDoc?.document_id ?? null
  const sessionReady = sessionState?.key === sessionKey ? sessionState : null
  const session = sessionReady?.session ?? null

  // 문서가 바뀌면 새 세션 생성. 설정은 고정값(config.ts) — 모델·전략은 사용자가 고르지 않는다.
  useEffect(() => {
    if (!activeDoc || !sessionKey || activeDoc.status !== 'ready') return
    let cancelled = false
    const timer = setTimeout(() => {
      api
        .createSession({ document_id: activeDoc.document_id, config: DEFAULT_SESSION_CONFIG })
        .then((s) => !cancelled && setSessionState({ key: sessionKey, session: s, error: null }))
        .catch(
          (err) =>
            !cancelled &&
            setSessionState({
              key: sessionKey,
              session: null,
              error: errorMessage(err, '세션을 생성하지 못했습니다.'),
            }),
        )
    }, 300)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [activeDoc, sessionKey])

  const sessionRef = useRef<Session | null>(null)
  const autoFollowRef = useRef(autoFollow)
  useEffect(() => {
    sessionRef.current = session
    autoFollowRef.current = autoFollow
  }, [session, autoFollow])

  /* ---------- 결과 반영 ---------- */

  const addEntry = useCallback(
    (id: string, text: string, source: EntrySource, segment_id: string | null) => {
      seqOf.current.set(id, ++nextSeq.current)
      setEntries((prev) => [
        ...prev,
        {
          id,
          segment_id,
          text,
          source,
          createdAt: Date.now(),
          pending: true,
          result: null,
          roundTripMs: null,
          error: null,
        },
      ])
    },
    [],
  )

  const applyResult = useCallback(
    (id: string, result: SearchResult, roundTripMs: number | null) => {
      setEntries((prev) =>
        prev.map((e) => (e.id === id ? { ...e, pending: false, result, roundTripMs } : e)),
      )
      // NONE 이면 기존 하이라이트 유지 (contracts.md §2.4)
      if (result.status !== 'MATCH' || result.block_id == null || result.page == null) return
      // 늦게 도착한 이전 발화의 결과가 최신 하이라이트를 덮어쓰지 않도록
      const seq = seqOf.current.get(id) ?? 0
      if (seq < lastAppliedSeq.current) return
      lastAppliedSeq.current = seq
      setHighlight({ blockId: result.block_id, entryId: id })
      if (autoFollowRef.current) setPageNumber(result.page)
    },
    [],
  )

  /* ---------- 텍스트 탐색 (contracts.md §3.5) ---------- */

  const requestSeq = useRef(0)
  const runSearch = useCallback(
    async (text: string, source: EntrySource) => {
      const current = sessionRef.current
      if (!current) return
      const id = `req_${++requestSeq.current}`
      addEntry(id, text, source, null)

      const started = performance.now()
      try {
        const result = await api.search({ session_id: current.session_id, text, segment_id: null })
        applyResult(id, result, Math.round(performance.now() - started))
      } catch (err) {
        setEntries((prev) =>
          prev.map((e) =>
            e.id === id
              ? { ...e, pending: false, error: errorMessage(err, '탐색 요청에 실패했습니다.') }
              : e,
          ),
        )
      }
    },
    [addEntry, applyResult],
  )

  /* ---------- STT ---------- */

  const [sttSource, setSttSource] = useState<SttSource>('browser')
  const streamUrl = sttSource === 'server' && session ? api.streamUrl(session.session_id) : null

  // 서버 STT (WebSocket, contracts.md §4)
  const handleSegment = useCallback(
    (segment: STTSegment) => {
      const id = `stt_${segment.segment_id}`
      if (seqOf.current.has(id)) return // 확정 결과는 한 번만 처리
      sttFinalAt.current.set(id, performance.now())
      addEntry(id, segment.text, 'server-stt', segment.segment_id)
    },
    [addEntry],
  )

  const handleHighlight = useCallback(
    (result: SearchResult) => {
      if (result.session_id !== sessionRef.current?.session_id) return
      const id = result.segment_id ? `stt_${result.segment_id}` : `ws_${++requestSeq.current}`
      if (!seqOf.current.has(id)) {
        addEntry(id, '(발화 텍스트 없음)', 'server-stt', result.segment_id)
      }
      const finalAt = sttFinalAt.current.get(id)
      applyResult(id, result, finalAt != null ? Math.round(performance.now() - finalAt) : null)
    },
    [addEntry, applyResult],
  )

  const serverStt = useServerStt({
    url: streamUrl,
    onSegment: handleSegment,
    onHighlight: handleHighlight,
  })

  // 중간 결과는 화면에만 반영한다. 250ms 간격으로 마지막 버전만 보내고
  // 먼저 전송한 요청이 나중에 도착하더라도 오래된 하이라이트로 덮지 않는다.
  const requestPreview = useCallback((text: string, version: number) => {
    const current = sessionRef.current
    if (!current || !api.previewSearch) return
    const sessionId = current.session_id
    previewLastRequest.current = performance.now()
    void api.previewSearch({ session_id: sessionId, text, segment_id: null })
      .then((result) => {
        if (version !== previewVersion.current || sessionRef.current?.session_id !== sessionId) return
        setPreviewResult(result.status === 'MATCH' ? result : null)
        if (result.status === 'MATCH' && result.page != null && autoFollowRef.current) {
          setPageNumber(result.page)
        }
      })
      .catch(() => {
        if (version === previewVersion.current) setPreviewResult(null)
      })
  }, [])

  const handleInterim = useCallback((text: string) => {
    previewVersion.current++
    previewText.current = text.trim()
    if (!previewText.current) {
      clearPreview()
      return
    }
    if (previewTimer.current !== null) return
    const delay = Math.max(0, 250 - (performance.now() - previewLastRequest.current))
    previewTimer.current = setTimeout(() => {
      previewTimer.current = null
      if (previewText.current) requestPreview(previewText.current, previewVersion.current)
    }, delay)
  }, [clearPreview, requestPreview])

  useEffect(() => () => {
    previewVersion.current++
    if (previewTimer.current !== null) clearTimeout(previewTimer.current)
  }, [])

  // 브라우저 STT: 중간 문장으로 즉시 미리보기, 확정 문장은 실제 15단어 큐에 한 번만 반영.
  const browserStt = useSpeechRecognition({
    onInterim: handleInterim,
    onFinal: useCallback((text: string) => {
      clearPreview()
      void runSearch(text, 'browser-stt')
    }, [clearPreview, runSearch]),
  })

  const stt: SttView =
    sttSource === 'server'
      ? {
          supported: serverStt.supported,
          listening: serverStt.listening,
          busy: serverStt.phase === 'connecting' || serverStt.phase === 'stopping',
          interim: serverStt.interim,
          error: serverStt.error,
          start: () => void serverStt.start(),
          stop: () => void serverStt.stop(),
        }
      : { ...browserStt, busy: false }

  /* ---------- 문서 업로드 (contracts.md §3.1, §3.2) ---------- */

  async function handleFileSelect(file: File) {
    setDocumentBusy(true)
    setNotice(null)
    try {
      const { loadPdf } = await import('./lib/pdf') // pdf.js 는 필요할 때만 로드
      const pdfTask = loadPdf(file)
      let doc: Document
      try {
        doc = await api.uploadDocument(file)
      } catch (err) {
        void pdfTask.then((p) => p.destroy(), () => undefined) // 업로드 실패 시 열어 둔 PDF 정리
        throw err
      }
      const localPdf = await pdfTask

      if (doc.status === 'failed') {
        void localPdf.destroy()
        setNotice('PDF 처리에 실패했습니다.')
        return
      }
      const { blocks } = await api.getBlocks(doc.document_id)

      void pdf?.destroy()
      setPdf(localPdf)
      setActiveDoc(doc)
      setBlocks(blocks)
      setPageNumber(1)
      resetTimeline()
      if (doc.status === 'processing') setNotice('문서를 처리 중입니다. 잠시 후 다시 시도하세요.')
    } catch (err) {
      setNotice(errorMessage(err, 'PDF 파일을 열 수 없습니다.'))
    } finally {
      setDocumentBusy(false)
    }
  }

  /* ---------- 타임라인 선택 ---------- */

  function handleSelectEntry(entry: TimelineEntry) {
    const r = entry.result
    if (r?.status !== 'MATCH' || r.block_id == null || r.page == null) return
    clearPreview()
    setHighlight({ blockId: r.block_id, entryId: entry.id })
    setPageNumber(r.page)
  }

  // 결과 카드는 가장 최근 발화를 표시 (NONE 이어도 하이라이트는 이전 것을 유지)
  const currentEntry = entries.at(-1) ?? null

  const notices = [notice, sessionReady?.error].filter((n): n is string => !!n)

  return (
    <div className="app">
      <AppHeader serverStatus={serverStatus} mode={api.mode} />

      {notices.length > 0 && (
        <div className="notice error" role="alert">
          {notices.map((n) => (
            <p key={n}>{n}</p>
          ))}
        </div>
      )}

      <main className="layout">
        <PdfViewer
          pdf={pdf}
          pageCount={activeDoc?.page_count ?? 0}
          pageNumber={pageNumber}
          onPageChange={setPageNumber}
          blocks={pageBlocks}
          highlight={highlightBlock?.page === pageNumber ? highlightBlock : null}
          showBlocks={DEV_TOOLS && showBlocks}
          emptyMessage={documentBusy ? 'PDF 업로드·분석 중…' : undefined}
        />

        <aside className="sidebar">
          <ControlPanel
            fileName={activeDoc?.file_name ?? null}
            documentBusy={documentBusy}
            onFileSelect={(file) => void handleFileSelect(file)}
            sttSource={sttSource}
            onSttSourceChange={setSttSource}
            serverSttAvailable={api.mode === 'server'}
            stt={stt}
            canSearch={session !== null}
            onSubmitText={(text) => void runSearch(text, 'manual')}
            autoFollow={autoFollow}
            onAutoFollowChange={setAutoFollow}
            devTools={DEV_TOOLS}
            showBlocks={showBlocks}
            onToggleBlocks={setShowBlocks}
          />
          <ResultCard entry={currentEntry} />
          <Timeline
            entries={entries}
            activeId={highlight?.entryId ?? null}
            onSelect={handleSelectEntry}
            onClear={resetTimeline}
          />
        </aside>
      </main>
    </div>
  )
}

export default App
