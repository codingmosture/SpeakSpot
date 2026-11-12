import type {
  Block,
  Document,
  SearchResult,
  SessionConfig,
  TimelineItem,
} from '../types/contracts'
import { ApiError, type SpeakSpotApi } from './types'

/*
 * backend 초안 API 어댑터 (임시).
 *
 *   GET  /api/mock/blocks  → Block[]  (bbox 만 있음)
 *   POST /api/search       → { status, page, block_id, score, model }
 *
 * 초안 응답을 contracts.md v0.1 형식으로 변환해서, 프론트가 backend 초안과
 * 바로 연동 테스트를 할 수 있게 한다. backend 가 /api/v1 을 구현하면
 * VITE_API_MODE=server 로 바꾸고 이 파일은 삭제한다.
 *
 * 초안에 없는 기능(업로드, 세션, 타임라인)은 프론트에서 흉내 낸다.
 */

const API_ORIGIN = import.meta.env.VITE_API_ORIGIN ?? ''
const LEGACY_DOCUMENT_ID = 'doc_001'

interface LegacyBlock {
  block_id: string
  page: number
  text: string
  bbox: [number, number, number, number]
}

interface LegacySearchResult {
  status: 'MATCH' | 'NONE'
  page: number | null
  block_id: string | null
  score: number | null
  model: string
}

interface LocalSession {
  document_id: string
  config: SessionConfig
  startedAt: number
  timeline: TimelineItem[]
}

const sessions = new Map<string, LocalSession>()
const documents = new Map<string, Document>()
let sessionSeq = 0
let segmentSeq = 0
let docSeq = 0
let blocksCache: Promise<Block[]> | null = null

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_ORIGIN}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init.headers },
    })
  } catch {
    throw new ApiError('서버에 연결할 수 없습니다.')
  }
  if (!response.ok) throw new ApiError(`${path} 요청 실패 (${response.status})`, response.status)
  return (await response.json()) as T
}

/** 초안 Block → 명세 Block (block_type / rects / keywords 채움) */
function toBlock(b: LegacyBlock): Block {
  return { ...b, block_type: 'other', rects: [b.bbox], keywords: [] }
}

function loadBlocks() {
  blocksCache ??= request<LegacyBlock[]>('/api/mock/blocks')
    .then((list) => list.map(toBlock))
    .catch((err) => {
      blocksCache = null
      throw err
    })
  return blocksCache
}

function requireSession(sessionId: string) {
  const session = sessions.get(sessionId)
  if (!session) throw new ApiError('해당 세션을 찾을 수 없습니다.', 404, 'SESSION_NOT_FOUND')
  return session
}

export const legacyApi: SpeakSpotApi = {
  mode: 'legacy',

  health: () => request('/api/health'),

  // 초안 블록이 있는 페이지까지를 문서로 간주
  async initialDocument() {
    const blocks = await loadBlocks()
    const doc: Document = {
      document_id: LEGACY_DOCUMENT_ID,
      file_name: 'backend 초안 Mock 문서',
      page_count: Math.max(1, ...blocks.map((b) => b.page)),
      status: 'ready',
    }
    documents.set(doc.document_id, doc)
    return doc
  },

  // 업로드 API 가 없으므로 로컬에서 페이지 수만 읽고, 초안 블록을 겹쳐 표시한다
  async uploadDocument(file) {
    if (file.type && file.type !== 'application/pdf') {
      throw new ApiError('PDF 파일만 업로드할 수 있습니다.', 415, 'UNSUPPORTED_MEDIA_TYPE')
    }
    const { loadPdf } = await import('../lib/pdf')
    const pdf = await loadPdf(file)
    const doc: Document = {
      document_id: `${LEGACY_DOCUMENT_ID}_local_${++docSeq}`,
      file_name: file.name,
      page_count: pdf.numPages,
      status: 'ready',
    }
    void pdf.destroy()
    documents.set(doc.document_id, doc)
    return doc
  },

  async getBlocks(documentId, page) {
    const doc = documents.get(documentId)
    const blocks = (await loadBlocks()).filter(
      (b) => !doc || b.page <= doc.page_count,
    )
    return {
      document_id: documentId,
      blocks: page != null ? blocks.filter((b) => b.page === page) : blocks,
    }
  },

  async createSession({ document_id, config }) {
    const session_id = `sess_legacy_${String(++sessionSeq).padStart(3, '0')}`
    sessions.set(session_id, { document_id, config, startedAt: Date.now(), timeline: [] })
    return { session_id, document_id, status: 'created' }
  },

  async search({ session_id, text, segment_id }) {
    const session = requireSession(session_id)
    const started = performance.now()

    const raw = await request<LegacySearchResult>('/api/search', {
      method: 'POST',
      body: JSON.stringify({
        // 초안은 문서 ID 를 하나만 알고 있으므로 고정값 사용
        document_id: LEGACY_DOCUMENT_ID,
        session_id,
        segment_id: segment_id ?? `seg_${String(++segmentSeq).padStart(6, '0')}`,
        text,
      }),
    })

    const strategy = session.config.search_strategy
    const matched = raw.status === 'MATCH' && raw.page != null && raw.block_id != null
    const result: SearchResult = {
      status: matched ? 'MATCH' : 'NONE',
      document_id: session.document_id,
      session_id,
      segment_id,
      page: matched ? raw.page : null,
      block_id: matched ? raw.block_id : null,
      score: matched ? raw.score : null,
      strategy,
      // 초안은 사용 단계를 알려주지 않으므로 선택한 전략으로 추정 (hybrid → local)
      stage_used: matched ? (strategy === 'global' ? 'global' : 'local') : 'none',
      model: raw.model,
      // 초안은 처리 시간을 알려주지 않으므로 왕복 시간으로 대체
      processing_ms: Math.round(performance.now() - started),
    }

    session.timeline.push({
      segment_id,
      time_ms: Date.now() - session.startedAt,
      status: result.status,
      page: result.page,
      block_id: result.block_id,
    })
    return result
  },

  async getTimeline(sessionId) {
    return { session_id: sessionId, items: [...requireSession(sessionId).timeline] }
  },

  streamUrl: () => null,
}
