/**
 * 백엔드와 공유하는 공통 데이터 형식 — docs/contracts.md (v0.1) 기준.
 * 명세가 변경되면 이 파일도 함께 수정한다.
 */

/* ---------- 2.1 Document ---------- */

export type DocumentStatus = 'processing' | 'ready' | 'failed'

export interface Document {
  document_id: string
  file_name: string
  page_count: number
  status: DocumentStatus
}

/* ---------- 2.2 Block ---------- */

/** 정규화 좌표 [x0, y0, x1, y1] — 원점은 왼쪽 위, 값은 0~1 */
export type BBox = [number, number, number, number]

export type BlockType =
  | 'title'
  | 'paragraph'
  | 'bullet'
  | 'table'
  | 'caption'
  | 'equation'
  | 'other'

export interface Block {
  block_id: string
  /** 1-based */
  page: number
  text: string
  block_type: BlockType
  bbox: BBox
  /** 실제 하이라이트할 영역 목록 */
  rects: BBox[]
  keywords?: string[]
}

export interface BlocksResponse {
  document_id: string
  blocks: Block[]
}

/* ---------- 2.3 STTSegment ---------- */

export interface STTSegment {
  segment_id: string
  text: string
  start_ms: number
  end_ms: number
  is_final: boolean
}

/* ---------- 2.4 SearchResult ---------- */

export type SearchStatus = 'MATCH' | 'NONE'
export type SearchStrategy = 'local' | 'global' | 'hybrid'
export type StageUsed = 'local' | 'global' | 'none'

export interface SearchResult {
  status: SearchStatus
  document_id: string
  session_id: string
  segment_id: string | null
  /** MATCH 에서 필수, NONE 에서는 null */
  page: number | null
  block_id: string | null
  score: number | null
  strategy: SearchStrategy
  stage_used: StageUsed
  model: string
  /** 탐색 소요 시간 (STT·렌더링 제외) */
  processing_ms: number
}

/* ---------- 2.5 SessionConfig ---------- */

export interface SessionConfig {
  stt_model: string
  search_model: string
  search_strategy: SearchStrategy
  local_radius_pages: number
  stt_buffer_seconds: number
  stt_update_interval_seconds: number
}

/* ---------- 3. HTTP API ---------- */

export interface CreateSessionRequest {
  document_id: string
  config: SessionConfig
}

export interface Session {
  session_id: string
  document_id: string
  status: string
}

export interface SearchRequest {
  session_id: string
  text: string
  segment_id: string | null
}

export interface TimelineItem {
  segment_id: string | null
  time_ms: number
  status: SearchStatus
  page: number | null
  block_id: string | null
}

export interface TimelineResponse {
  session_id: string
  items: TimelineItem[]
}

/* ---------- 4. WebSocket ---------- */

export interface AudioFormat {
  encoding: 'pcm_s16le'
  sample_rate: 16000
  channels: 1
}

export type ClientMessage =
  | { type: 'session.start'; payload: AudioFormat }
  | { type: 'session.stop'; payload: Record<string, never> }

export type ServerMessage =
  | { type: 'session.ready'; payload: { session_id: string } }
  | { type: 'stt.result'; payload: STTSegment }
  | { type: 'highlight.update'; payload: SearchResult }
  | { type: 'error'; payload: { code: string; message: string } }

/* ---------- 6. 오류 ---------- */

export interface ErrorBody {
  error: {
    code: string
    message: string
    details: unknown
  }
}

/* ---------- 프론트 전용 ---------- */

export type EntrySource = 'server-stt' | 'browser-stt' | 'manual'

/** 발화 1건에 대한 타임라인 기록 */
export interface TimelineEntry {
  /** 프론트 내부 키 (segment_id 가 없는 수동 입력도 구분) */
  id: string
  segment_id: string | null
  text: string
  source: EntrySource
  createdAt: number
  pending: boolean
  result: SearchResult | null
  /** 요청~응답 왕복 시간 (HTTP) 또는 STT 확정~하이라이트 수신 시간 (WS) */
  roundTripMs: number | null
  error: string | null
}
