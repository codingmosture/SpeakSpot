import type { BBox } from '../types/contracts'

/**
 * 정규화 좌표(0~1, 왼쪽 위 원점)를 페이지 대비 % 스타일로 변환한다.
 * contracts.md §2.2 — 프론트는 `x * 화면너비`, `y * 화면높이` 로만 대응한다.
 */
export function bboxToStyle([x0, y0, x1, y1]: BBox) {
  return {
    left: `${x0 * 100}%`,
    top: `${y0 * 100}%`,
    width: `${(x1 - x0) * 100}%`,
    height: `${(y1 - y0) * 100}%`,
  }
}
