import { httpApi } from './http'
import { legacyApi } from './legacy'
import { mockApi } from './mock'
import type { SpeakSpotApi } from './types'

export { ApiError, type ApiMode, type SpeakSpotApi } from './types'

/**
 * VITE_API_MODE (frontend/.env.example 참고)
 * - mock   : 백엔드 없이 프론트 내장 Mock API (contracts.md §7) — 테스트 시 명시적 선택
 * - legacy : backend 초안 API (/api/mock/blocks, /api/search) — v1 완성 전 연동 테스트용
 * - server : backend /api/v1 (contracts.md §3, §4) — 기본값
 *
 * 루트 .gitignore 가 .env.* 를 제외하므로 env 파일 없이도 동작하도록 기본값을 코드에 둔다.
 *
 * 빌드 시 import.meta.env.VITE_API_MODE 가 문자열로 치환되므로,
 * 선택되지 않은 모드의 코드(Mock 데이터 등)는 번들에서 제거된다.
 */
const MODE = import.meta.env.VITE_API_MODE ?? 'server'

if (MODE !== 'server' && MODE !== 'mock' && MODE !== 'legacy') {
  console.warn(`알 수 없는 VITE_API_MODE "${MODE}" — server 모드로 동작합니다.`)
}

export const api: SpeakSpotApi =
  MODE === 'mock' ? mockApi : MODE === 'legacy' ? legacyApi : httpApi
