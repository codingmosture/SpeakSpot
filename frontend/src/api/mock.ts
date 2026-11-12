import type {
  Block,
  Document,
  SearchResult,
  SessionConfig,
  StageUsed,
  TimelineItem,
} from '../types/contracts'
import { MOCK_BLOCKS, MOCK_DOCUMENT } from './mockData'
import { ApiError, type SpeakSpotApi } from './types'

/*
 * 백엔드 없이 프론트를 단독 검증하기 위한 Mock API (contracts.md §7).
 * 실제 서버와 같은 인터페이스·응답 형식을 따른다.
 * 탐색 로직은 테스트용 단순 문자 bigram 매칭이며 실제 탐색 모델과 무관하다.
 */

const MATCH_THRESHOLD = 0.3

interface MockSession {
  document_id: string
  config: SessionConfig
  currentPage: number | null
  startedAt: number
  timeline: TimelineItem[]
}

// 처음 사용할 때 만든다 — Mock 모드가 아닌 빌드에서 샘플 데이터가 번들에서 제거되도록
let documentStore: Map<string, { doc: Document; blocks: Block[] }> | null = null
const documents = () =>
  (documentStore ??= new Map([
    [MOCK_DOCUMENT.document_id, { doc: MOCK_DOCUMENT, blocks: MOCK_BLOCKS }],
  ]))
const sessions = /* @__PURE__ */ new Map<string, MockSession>()
let docSeq = 0
let sessionSeq = 0

const delay = (min: number, max: number) =>
  new Promise((r) => setTimeout(r, min + Math.random() * (max - min)))

function bigrams(text: string) {
  const s = text.replace(/[^\p{L}\p{N}]/gu, '').toLowerCase()
  const set = new Set<string>()
  for (let i = 0; i < s.length - 1; i++) set.add(s.slice(i, i + 2))
  return set
}

/** 발화 bigram 중 블록에 포함된 비율 */
function score(query: Set<string>, text: string) {
  if (query.size === 0) return 0
  const target = bigrams(text)
  let hit = 0
  for (const g of query) if (target.has(g)) hit++
  return hit / query.size
}

function best(query: Set<string>, candidates: Block[]) {
  let top: { block: Block; score: number } | null = null
  for (const block of candidates) {
    const s = score(query, block.text)
    if (!top || s > top.score) top = { block, score: s }
  }
  return top && top.score >= MATCH_THRESHOLD ? top : null
}

function requireDocument(documentId: string) {
  const entry = documents().get(documentId)
  if (!entry) throw new ApiError('해당 PDF 문서를 찾을 수 없습니다.', 404, 'DOCUMENT_NOT_FOUND')
  return entry
}

function requireSession(sessionId: string) {
  const session = sessions.get(sessionId)
  if (!session) throw new ApiError('해당 세션을 찾을 수 없습니다.', 404, 'SESSION_NOT_FOUND')
  return session
}

export const mockApi: SpeakSpotApi = {
  mode: 'mock',

  async health() {
    return { status: 'ok', mode: 'mock' }
  },

  async initialDocument() {
    return MOCK_DOCUMENT
  },

  async uploadDocument(file) {
    if (file.type && file.type !== 'application/pdf') {
      throw new ApiError('PDF 파일만 업로드할 수 있습니다.', 415, 'UNSUPPORTED_MEDIA_TYPE')
    }
    await delay(200, 400)
    // 업로드한 PDF 위에 샘플 블록을 그대로 겹쳐 하이라이트 동작만 확인한다
    const { loadPdf } = await import('../lib/pdf')
    const pdf = await loadPdf(file)
    const doc: Document = {
      document_id: `doc_mock_${++docSeq}`,
      file_name: file.name,
      page_count: pdf.numPages,
      status: 'ready',
    }
    void pdf.destroy()
    documents().set(doc.document_id, {
      doc,
      blocks: MOCK_BLOCKS.filter((b) => b.page <= doc.page_count),
    })
    return doc
  },

  async getBlocks(documentId, page) {
    await delay(50, 150)
    const { blocks } = requireDocument(documentId)
    return {
      document_id: documentId,
      blocks: page != null ? blocks.filter((b) => b.page === page) : blocks,
    }
  },

  async createSession({ document_id, config }) {
    requireDocument(document_id)
    await delay(50, 120)
    const session_id = `sess_mock_${String(++sessionSeq).padStart(3, '0')}`
    sessions.set(session_id, {
      document_id,
      config,
      currentPage: null,
      startedAt: Date.now(),
      timeline: [],
    })
    return { session_id, document_id, status: 'created' }
  },

  async search({ session_id, text, segment_id }) {
    const session = requireSession(session_id)
    const { blocks } = requireDocument(session.document_id)
    const { search_strategy: strategy, search_model: model, local_radius_pages: radius } =
      session.config

    const started = performance.now()
    await delay(60, 220)

    const query = bigrams(text)
    const center = session.currentPage ?? 1
    const local = () =>
      best(query, blocks.filter((b) => Math.abs(b.page - center) <= radius))
    const global = () => best(query, blocks)

    let found: ReturnType<typeof best> = null
    let stage: StageUsed = 'none'
    if (strategy === 'local') {
      found = local()
      if (found) stage = 'local'
    } else if (strategy === 'global') {
      found = global()
      if (found) stage = 'global'
    } else {
      // Hybrid: Local 성공 → MATCH, 실패 → Global, Global 도 실패 → NONE
      found = local()
      if (found) stage = 'local'
      else {
        found = global()
        if (found) stage = 'global'
      }
    }

    const base = {
      document_id: session.document_id,
      session_id,
      segment_id,
      strategy,
      model: model || 'mock',
      processing_ms: Math.round(performance.now() - started),
    }
    const result: SearchResult = found
      ? {
          ...base,
          status: 'MATCH',
          page: found.block.page,
          block_id: found.block.block_id,
          score: Math.round(found.score * 1000) / 1000,
          stage_used: stage,
        }
      : { ...base, status: 'NONE', page: null, block_id: null, score: null, stage_used: 'none' }

    if (result.status === 'MATCH') session.currentPage = result.page
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
    const session = requireSession(sessionId)
    return { session_id: sessionId, items: [...session.timeline] }
  },

  streamUrl: () => null,
}
