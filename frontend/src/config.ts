import type { SessionConfig } from './types/contracts'

/**
 * 세션 생성 시 보내는 기본 설정 (contracts.md §2.5, §3.4 — config 필수).
 *
 * 기본값은 슬라이딩 큐이며 개발자 메뉴에서 실험 설정을 변경할 수 있다.
 * - 기본 탐색: 최근 15개 단어를 사용하는 슬라이딩 큐, 현재 페이지 ±5페이지만 검색.
 * - 슬라이딩 큐는 임베딩 모델을 사용하지 않으므로 search_model은 null이다.
 * - STT 모델은 현재 faster-whisper tiny로 고정하며, 필요 시 변경한다.
 */
export const DEFAULT_SESSION_CONFIG: SessionConfig = {
  stt_model: 'faster-whisper-tiny',
  search_model: null,
  search_strategy: 'sliding_queue',
  local_radius_pages: 5,
  sliding_window_words: 15,
  stt_buffer_seconds: 5,
  stt_update_interval_seconds: 2,
  min_match_score: 0.34,
  backward_penalty: 0.08,
  stay_bonus: 0.025,
  switch_margin: 0.05,
  prefix_bonus: 0.035,
  min_margin: 0,
  global_top_k_pages: 3,
  jev_min_confidence: 0,
  max_jev_candidates: 120,
}

export const HEALTH_POLL_MS = 10_000

/**
 * 개발 도구(STT 소스 선택, 블록 경계 표시) 표시 여부.
 * 기본값: pnpm dev 에서는 켜짐, 배포용 빌드에서는 꺼짐. VITE_DEV_TOOLS 로 덮어쓸 수 있다.
 */
export const DEV_TOOLS =
  (import.meta.env.VITE_DEV_TOOLS ?? String(import.meta.env.DEV)) === 'true'
