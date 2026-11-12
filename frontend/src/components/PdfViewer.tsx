import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { Block } from '../types/contracts'
import { bboxToStyle } from '../lib/bbox'
import type { PDFDocumentProxy } from '../lib/pdf'

interface Props {
  pdf: PDFDocumentProxy | null
  pageCount: number
  pageNumber: number
  onPageChange: (page: number) => void
  /** 현재 페이지의 블록 */
  blocks: Block[]
  /** 하이라이트할 블록 (현재 페이지에 있을 때만) */
  highlight: Block | null
  /** 블록 경계 표시 (개발 도구) */
  showBlocks: boolean
  emptyMessage?: string
}

export function PdfViewer({
  pdf,
  pageCount,
  pageNumber,
  onPageChange,
  blocks,
  highlight,
  showBlocks,
  emptyMessage,
}: Props) {
  const hasDocument = pageCount > 0

  return (
    <section className="viewer" aria-label="PDF 뷰어">
      <div className="viewer-toolbar">
        <span className="viewer-label">PDF 뷰어</span>
        <div className="pager">
          <button
            type="button"
            className="btn ghost"
            disabled={!hasDocument || pageNumber <= 1}
            onClick={() => onPageChange(pageNumber - 1)}
            aria-label="이전 페이지"
          >
            ‹
          </button>
          <span className="page-indicator" aria-live="polite">
            {hasDocument ? (
              <>
                <strong>{pageNumber}</strong> / {pageCount}
              </>
            ) : (
              '-'
            )}
          </span>
          <button
            type="button"
            className="btn ghost"
            disabled={!hasDocument || pageNumber >= pageCount}
            onClick={() => onPageChange(pageNumber + 1)}
            aria-label="다음 페이지"
          >
            ›
          </button>
        </div>
      </div>

      <div className="viewer-scroll">
        {!hasDocument ? (
          <div className="viewer-empty" role="status">
            <span className="empty-document-icon" aria-hidden>▤</span>
            <h2>{emptyMessage ?? '문서에서 시작하세요'}</h2>
            <p>문서 영역의 PDF 업로드로 파일을 열면<br />음성과 텍스트로 내용을 찾을 수 있습니다.</p>
          </div>
        ) : pdf ? (
          <PdfPage pdf={pdf} pageNumber={pageNumber}>
            <BlockOverlay blocks={blocks} highlight={highlight} showBlocks={showBlocks} />
          </PdfPage>
        ) : (
          <div className="page-sheet mock">
            <span className="page-label">Page {pageNumber} · Mock</span>
            <BlockOverlay blocks={blocks} highlight={highlight} showBlocks={showBlocks} showText />
          </div>
        )}
      </div>
    </section>
  )
}

/* ---------- 실제 PDF 렌더링 (pdf.js) ---------- */

function PdfPage({
  pdf,
  pageNumber,
  children,
}: {
  pdf: PDFDocumentProxy
  pageNumber: number
  children: ReactNode
}) {
  const wrapperRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [{ width, height }, setSize] = useState({ width: 0, height: 0 })
  // 페이지와 뷰어 크기 중 하나가 바뀌면 다시 렌더링한다.
  const [renderedKey, setRenderedKey] = useState('')
  const renderKey = `${pageNumber}:${width}:${height}`
  const rendering = width > 0 && height > 0 && renderedKey !== renderKey

  // 패딩을 제외한 표시 공간의 너비와 높이를 함께 추적한다.
  useEffect(() => {
    const el = wrapperRef.current?.parentElement
    if (!el) return
    const observer = new ResizeObserver(([entry]) => {
      setSize({
        width: Math.floor(entry.contentRect.width),
        height: Math.floor(entry.contentRect.height),
      })
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  // 페이지 렌더링
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || width <= 0 || height <= 0) return
    if (pageNumber < 1 || pageNumber > pdf.numPages) return

    let cancelled = false
    let renderTask: { cancel: () => void; promise: Promise<void> } | null = null

    void (async () => {
      try {
        const page = await pdf.getPage(pageNumber)
        if (cancelled) return

        // getViewport 는 페이지 회전(/Rotate)이 반영된 "표시 방향" 기준이므로
        // 정규화 좌표(contracts.md §2.2)를 그대로 겹칠 수 있다.
        const base = page.getViewport({ scale: 1 })
        // 세로·가로·회전 페이지 모두 비율을 유지하며 한 페이지 전체를 표시한다.
        const cssScale = Math.min(width / base.width, height / base.height)
        const dpr = window.devicePixelRatio || 1
        const viewport = page.getViewport({ scale: cssScale * dpr })

        canvas.width = Math.floor(viewport.width)
        canvas.height = Math.floor(viewport.height)
        canvas.style.width = `${Math.floor(viewport.width / dpr)}px`
        canvas.style.height = `${Math.floor(viewport.height / dpr)}px`

        renderTask = page.render({ canvas, viewport })
        await renderTask.promise
      } catch (err) {
        if ((err as Error)?.name !== 'RenderingCancelledException') console.error(err)
      } finally {
        if (!cancelled) setRenderedKey(renderKey)
      }
    })()

    return () => {
      cancelled = true
      renderTask?.cancel()
    }
  }, [pdf, pageNumber, width, height, renderKey])

  return (
    <div ref={wrapperRef} className="page-sheet pdf">
      <canvas ref={canvasRef} />
      {rendering && <div className="page-loading">렌더링 중…</div>}
      {children}
    </div>
  )
}

/* ---------- 블록 경계 / 하이라이트 오버레이 ---------- */

function BlockOverlay({
  blocks,
  highlight,
  showBlocks,
  showText = false,
}: {
  blocks: Block[]
  highlight: Block | null
  showBlocks: boolean
  showText?: boolean
}) {
  const highlightRef = useRef<HTMLElement | null>(null)

  // 하이라이트된 영역이 화면에 보이도록 스크롤
  useEffect(() => {
    highlightRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' })
  }, [highlight])

  // 실제 PDF: backend 가 준 rects(줄 단위 좌표)를 칠한다. 비어 있으면 bbox 로 대체.
  // Mock 페이지(showText): 글자를 브라우저가 직접 줄바꿈하므로 좌표 대신 글자 자체에 칠한다.
  const highlightRects =
    highlight && !showText
      ? highlight.rects.length > 0
        ? highlight.rects
        : [highlight.bbox]
      : []

  return (
    <div className="block-layer">
      {(showBlocks || showText) &&
        blocks.map((block) => (
          <div
            key={block.block_id}
            className={['pdf-block', showBlocks && 'outlined', showText && 'with-text']
              .filter(Boolean)
              .join(' ')}
            title={`${block.block_id} · ${block.block_type}\n${block.text}`}
            style={bboxToStyle(block.bbox)}
          >
            {showText &&
              (block.block_id === highlight?.block_id ? (
                <mark
                  // 하이라이트 블록이 바뀔 때 애니메이션을 다시 재생
                  key={highlight.block_id}
                  ref={(el) => {
                    highlightRef.current = el
                  }}
                  className="text-highlight"
                >
                  {block.text}
                </mark>
              ) : (
                block.text
              ))}
            {showBlocks && <span className="block-tag">{block.block_id}</span>}
          </div>
        ))}

      {highlightRects.map((rect, i) => (
        <div
          // 하이라이트 블록이 바뀔 때 애니메이션을 다시 재생하도록 key 에 block_id 포함
          key={`${highlight?.block_id}-${i}`}
          ref={
            i === 0
              ? (el) => {
                  highlightRef.current = el
                }
              : undefined
          }
          className="highlight-rect"
          style={bboxToStyle(rect)}
        />
      ))}
    </div>
  )
}
