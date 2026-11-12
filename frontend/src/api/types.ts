import type {
  BlocksResponse,
  CreateSessionRequest,
  Document,
  SearchRequest,
  SearchResult,
  Session,
  TimelineResponse,
} from '../types/contracts'

export type ApiMode = 'server' | 'legacy' | 'mock'

/** 서버·초안·Mock 구현이 공유하는 API 인터페이스 (contracts.md §3) */
export interface SpeakSpotApi {
  readonly mode: ApiMode
  health(): Promise<unknown>
  /** 업로드 없이 바로 열 기본 문서 (Mock·초안 모드 전용) */
  initialDocument?(): Promise<Document>
  uploadDocument(file: File): Promise<Document>
  getBlocks(documentId: string, page?: number): Promise<BlocksResponse>
  createSession(body: CreateSessionRequest): Promise<Session>
  search(body: SearchRequest): Promise<SearchResult>
  /** STT 중간 인식 문장으로 임시 하이라이트만 계산한다. 큐는 확정 시에만 갱신. */
  previewSearch?(body: SearchRequest): Promise<SearchResult>
  getTimeline(sessionId: string): Promise<TimelineResponse>
  /** 실시간 STT WebSocket 주소. Mock 모드에서는 null */
  streamUrl(sessionId: string): string | null
}

export class ApiError extends Error {
  readonly status: number | null
  readonly code: string | null

  constructor(message: string, status: number | null = null, code: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}
