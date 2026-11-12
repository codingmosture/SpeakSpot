import type { BBox, Block, Document } from '../types/contracts'

/*
 * Mock 모드용 고정 문서 / 블록 데이터 (contracts.md §7 — 프론트 단독 테스트).
 * 좌표는 정규화 좌표이며, 2줄 이상 블록은 rects 를 줄 단위로 나눈다.
 */

export const MOCK_DOCUMENT: Document = {
  document_id: 'doc_mock',
  file_name: '운영체제_샘플강의.pdf (Mock)',
  page_count: 3,
  status: 'ready',
}

// 부작용 없는 함수로 표시 — Mock 모드가 아닌 빌드에서 샘플 데이터가 번들에서 제거되도록
/* @__NO_SIDE_EFFECTS__ */
function block(
  page: number,
  index: number,
  block_type: Block['block_type'],
  text: string,
  bbox: BBox,
  lines = 1,
): Block {
  const [x0, y0, x1, y1] = bbox
  const lineH = (y1 - y0) / lines
  const rects: BBox[] = Array.from({ length: lines }, (_, i) => [
    x0,
    y0 + lineH * i,
    // 마지막 줄은 짧게 — 다중 행 블록의 rects 하이라이트 확인용
    i === lines - 1 && lines > 1 ? x0 + (x1 - x0) * 0.6 : x1,
    y0 + lineH * (i + 1),
  ])
  return {
    block_id: `p${String(page).padStart(4, '0')}_b${String(index).padStart(4, '0')}`,
    page,
    text,
    block_type,
    bbox,
    rects,
    keywords: [],
  }
}

export const MOCK_BLOCKS: Block[] = [
  block(1, 1, 'title', '1장. 운영체제 개요', [0.1, 0.08, 0.9, 0.14]),
  block(
    1,
    2,
    'paragraph',
    '운영체제는 컴퓨터 하드웨어를 관리하고 응용 프로그램에 서비스를 제공하는 시스템 소프트웨어이다.',
    [0.1, 0.2, 0.9, 0.3],
    2,
  ),
  block(1, 3, 'bullet', '주요 역할: 자원 관리, 프로세스 관리, 메모리 관리, 파일 시스템 관리', [0.1, 0.36, 0.9, 0.46], 2),
  block(1, 4, 'caption', '그림 1-1. 운영체제의 계층 구조', [0.25, 0.82, 0.75, 0.87]),

  block(2, 1, 'title', '2장. 프로세스', [0.1, 0.08, 0.9, 0.14]),
  block(
    2,
    2,
    'paragraph',
    '프로세스는 실행 중인 프로그램이며, 운영체제는 프로세스를 생성하고 스케줄링하며 종료한다.',
    [0.1, 0.2, 0.9, 0.3],
    2,
  ),
  block(2, 3, 'bullet', '프로세스 상태: 생성, 준비, 실행, 대기, 종료', [0.1, 0.36, 0.9, 0.42]),
  block(2, 4, 'equation', 'CPU 이용률 = CPU 실행 시간 / 전체 시간', [0.2, 0.52, 0.8, 0.58]),
  block(2, 5, 'paragraph', '문맥 교환은 CPU가 다른 프로세스로 전환할 때 상태를 저장하고 복원하는 과정이다.', [0.1, 0.66, 0.9, 0.76], 2),

  block(3, 1, 'title', '3장. 메모리 관리', [0.1, 0.08, 0.9, 0.14]),
  block(
    3,
    2,
    'paragraph',
    '가상 메모리는 물리 메모리보다 큰 주소 공간을 제공하며 페이지 단위로 관리된다.',
    [0.1, 0.2, 0.9, 0.3],
    2,
  ),
  block(3, 3, 'table', '페이지 교체 알고리즘 비교: FIFO, LRU, OPT', [0.15, 0.38, 0.85, 0.56]),
  block(3, 4, 'paragraph', '페이지 폴트가 자주 발생하면 스래싱 현상이 나타나 성능이 급격히 떨어진다.', [0.1, 0.64, 0.9, 0.74], 2),
]
