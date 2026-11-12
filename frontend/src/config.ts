import type { SessionConfig } from './types/contracts'

/**
 * 세션 생성 시 보내는 고정 설정 (contracts.md §2.5, §3.4 — config 필수).
 *
 * 사용자는 모델·전략을 고르지 않는다 (팀 회의 결정):
 * - 탐색: Local → 실패 시 Global → 그래도 실패하면 이전 하이라이트 유지. 조정은 backend 담당.
 * - STT·탐색 모델: 실험으로 가장 성능이 좋은 조합을 선정해 고정한다.
 *   모델 이름은 현재 contracts.md 예시값이므로, 최종 조합이 정해지면 여기 값을 갱신한다.
 */
export const DEFAULT_SESSION_CONFIG: SessionConfig = {
  stt_model: 'faster-whisper-tiny',
  search_model: 'bge-m3',
  search_strategy: 'hybrid',
  local_radius_pages: 1,
  stt_buffer_seconds: 5,
  stt_update_interval_seconds: 2,
}

export const HEALTH_POLL_MS = 10_000

/**
 * 개발 도구(STT 소스 선택, 블록 경계 표시) 표시 여부.
 * 기본값: pnpm dev 에서는 켜짐, 배포용 빌드에서는 꺼짐. VITE_DEV_TOOLS 로 덮어쓸 수 있다.
 */
export const DEV_TOOLS =
  (import.meta.env.VITE_DEV_TOOLS ?? String(import.meta.env.DEV)) === 'true'
