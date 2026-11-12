import type { ErrorBody } from '../types/contracts'
import { ApiError, type SpeakSpotApi } from './types'

const API_ORIGIN = import.meta.env.VITE_API_ORIGIN ?? ''
const V1 = `${API_ORIGIN}/api/v1`
const DEFAULT_TIMEOUT_MS = 15_000

function isErrorBody(value: unknown): value is ErrorBody {
  return (
    typeof value === 'object' &&
    value !== null &&
    'error' in value &&
    typeof (value as ErrorBody).error?.message === 'string'
  )
}

async function request<T>(
  url: string,
  init: RequestInit = {},
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  const isForm = init.body instanceof FormData

  let response: Response
  try {
    response = await fetch(url, {
      ...init,
      signal: controller.signal,
      headers: isForm ? init.headers : { 'Content-Type': 'application/json', ...init.headers },
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new ApiError('요청 시간이 초과되었습니다.')
    }
    throw new ApiError('서버에 연결할 수 없습니다.')
  } finally {
    clearTimeout(timer)
  }

  const body: unknown = await response.json().catch(() => null)

  if (!response.ok) {
    // contracts.md §6 오류 본문 형식
    if (isErrorBody(body)) {
      throw new ApiError(body.error.message, response.status, body.error.code)
    }
    throw new ApiError(`요청 실패 (${response.status})`, response.status)
  }

  return body as T
}

export const httpApi: SpeakSpotApi = {
  mode: 'server',

  health: () => request(`${API_ORIGIN}/api/health`, {}, 3_000),

  uploadDocument: (file) => {
    const form = new FormData()
    form.append('file', file)
    return request(`${V1}/documents`, { method: 'POST', body: form }, 120_000)
  },

  getBlocks: (documentId, page) => {
    const query = page != null ? `?page=${page}` : ''
    return request(`${V1}/documents/${encodeURIComponent(documentId)}/blocks${query}`)
  },

  createSession: (body) =>
    request(`${V1}/sessions`, { method: 'POST', body: JSON.stringify(body) }, 300_000),

  search: (body) => request(`${V1}/search`, { method: 'POST', body: JSON.stringify(body) }),

  getTimeline: (sessionId) =>
    request(`${V1}/sessions/${encodeURIComponent(sessionId)}/timeline`),

  streamUrl: (sessionId) => {
    const base = API_ORIGIN
      ? new URL(API_ORIGIN)
      : new URL(window.location.href)
    const protocol = base.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${base.host}/api/v1/sessions/${encodeURIComponent(sessionId)}/stream`
  },
}
