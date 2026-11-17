# SpeakSpot 공통 인터페이스 명세서

> **버전:** v0.1 (초안)  
> **작성일:** 2026-11-01  
> **목적:** 4명이 프론트엔드·PDF 전처리·STT·탐색 알고리즘을 병렬 개발할 수 있도록 입출력 형식을 먼저 통일한다.  
> **원칙:** **외부 데이터 형식은 고정, 내부 모델·알고리즘은 교체 가능.**

## 0. 이번 버전에 확정할 사항

- 프론트엔드: React + TypeScript, 백엔드: Python + FastAPI를 기준으로 개발한다.
- 한 저장소에서 `frontend/`, `backend/`, `docs/`, `experiments/`를 관리한다.
- 모든 데이터 교환은 UTF-8 JSON을 사용한다. PDF 업로드는 `multipart/form-data`를 사용하며, 음성 인식은 브라우저가 담당한다.
- **PDF 페이지는 1부터 시작**한다. 예: 첫 페이지 `1`.
- **모든 PDF 좌표는 페이지의 표시 방향을 기준으로 정규화된 `[x0, y0, x1, y1]`** 형식을 사용한다. 원점은 **왼쪽 위**, 값은 **0~1**이며 `x0 < x1`, `y0 < y1`이다.
- `block_id`는 **한 document 내에서 유일**해야 한다. 예: `p0005_b0003`. 같은 PDF를 다른 설정으로 재처리한 경우 ID가 달라질 수 있다.
- 탐색 결과의 외부 상태는 **`MATCH` / `NONE`** 두 개로 통일한다. **`OUT_OF_LOCAL`은 탐색 모듈 내부 판단값**이며 프론트엔드에는 보내지 않는다.
- 프론트엔드는 `NONE`을 받았을 때 **마지막 하이라이트를 유지**한다. 미검출 상태를 별도 문구로 표시할 수 있다.
- 한 번의 검색 요청에 어떤 모델과 전략을 쓸지 설정하되, **모델과 전략은 서로 독립적으로 교체**한다.

## 1. 시스템 흐름과 모듈 책임

```text
[PDF 업로드] → [PDF 전처리] → [Document / Block 데이터]
                                           ↓
[마이크] → [STT] → [발화 텍스트] → [탐색 엔진] → [SearchResult]
                                           ↑            ↓
                                      [세션 상태]   [프론트 하이라이트]
```

| 모듈 | 입력 | 출력 | 책임 범위 |
|---|---|---|---|
| 프론트엔드 | PDF, 음성, SearchResult | 렌더링, 하이라이트, 타임라인 | PDF 파싱·AI 판단 로직을 넣지 않는다. |
| PDF 전처리 | PDF 파일 | Document, Block[] | 텍스트/좌표 추출, 블록 분리, 메타데이터 생성 |
| 브라우저 STT | 마이크 | 인식 텍스트 | 브라우저 음성 인식과 검색 요청 |
| 탐색 | Document, 발화, 세션 상태 | SearchResult | Local/Global/Hybrid, 후보 점수화, 실패 판단 |
| API·통합 | HTTP | 규격화된 응답 | 업로드, 검색 세션, 모듈 호출 연결 |

## 2. 공통 데이터 형식

### 2.1 Document

```json
{
  "document_id": "doc_001",
  "file_name": "lecture.pdf",
  "page_count": 32,
  "status": "ready"
}
```

- `document_id`: 문자열. 업로드한 문서를 참조하는 ID.
- `page_count`: 양의 정수.
- `status`: `processing` / `ready` / `failed`. 초기 구현은 동기 처리를 허용하므로 보통 `ready`를 반환한다.

### 2.2 Block (PDF 전처리 결과)

```json
{
  "block_id": "p0005_b0003",
  "page": 5,
  "text": "운영체제는 프로세스를 관리한다.",
  "block_type": "paragraph",
  "bbox": [0.10, 0.20, 0.80, 0.32],
  "rects": [[0.10, 0.20, 0.80, 0.25], [0.10, 0.27, 0.56, 0.32]],
  "keywords": ["운영체제", "프로세스"]
}
```

| 필드 | 자료형 | 필수 | 설명 |
|---|---|---|---|
| `block_id` | string | O | 문서 내 유일 ID |
| `page` | integer | O | 1-based 페이지 |
| `text` | string | O | 탐색에 사용할 블록 텍스트 |
| `block_type` | string | O | `title`, `paragraph`, `bullet`, `table`, `caption`, `equation`, `other` |
| `bbox` | number[4] | O | 전체 블록을 감싸는 정규화 좌표 |
| `rects` | number[4][] | O | 실제 하이라이트할 영역 목록. 단일 블록은 `[bbox]` 가능 |
| `keywords` | string[] | X | 검색 보조 키워드. 초기에는 빈 배열 허용 |

**좌표 규칙**

1. 원점 `(0,0)`은 PDF가 사용자에게 표시되는 페이지의 왼쪽 위이며, `(1,1)`은 오른쪽 아래다.
2. 페이지 크기가 바뀌어도 프론트에서 `x * 화면너비`, `y * 화면높이`로 대응할 수 있어야 한다.
3. 두 줄 이상인 블록은 `bbox` 한 개만 색칠하면 빈 공간까지 강조될 수 있으므로 가능하면 `rects`를 사용한다.
4. PDF 회전·CropBox 등의 영향은 **PDF 처리 담당과 뷰어 담당이 동일 샘플 PDF로 검증**한다. 변환이 필요한 경우 어느 쪽이 보정하는지 합의하고 이 문서를 수정한다.

### 2.3 브라우저 음성 인식

브라우저 Web Speech API가 인식한 텍스트를 프론트에서 검색 API로 전달합니다.
서버는 오디오를 수신하거나 음성을 인식하지 않습니다.

### 2.4 SearchResult (탐색 결과·하이라이트 이벤트 공용)

성공 시:

```json
{
  "status": "MATCH",
  "document_id": "doc_001",
  "session_id": "sess_001",
  "segment_id": "seg_000012",
  "page": 5,
  "block_id": "p0005_b0003",
  "score": 0.87,
  "strategy": "hybrid",
  "stage_used": "local",
  "model": "bge-m3",
  "processing_ms": 184
}
```

실패 시:

```json
{
  "status": "NONE",
  "document_id": "doc_001",
  "session_id": "sess_001",
  "segment_id": "seg_000013",
  "page": null,
  "block_id": null,
  "score": null,
  "strategy": "hybrid",
  "stage_used": "none",
  "model": "bge-m3",
  "processing_ms": 205
}
```

- `segment_id`는 직접 텍스트 검색에서는 `null`이 가능하다.
- `status`: `MATCH` / `NONE`.
- `page`, `block_id`: `MATCH`에서 필수이고, `NONE`에서 **반드시 `null`**이다.
- `score`: 모델 원시 점수 또는 정규화 점수. **모델 간 점수 크기를 직접 비교하지 않는다.** 값이 없는 모델은 `null`을 허용한다.
- `strategy`: `local` / `global` / `hybrid`.
- `stage_used`: 실제로 선택된 단계 `local` / `global` / `none`.
- `model`: 탐색 구현체 식별자. 모델별 설정·버전은 실험 기록에서 별도 보관한다.
- `processing_ms`: 탐색에 소요된 시간(음성 인식 및 화면 렌더링 시간은 포함하지 않음).
- 프론트는 `MATCH`일 때 `block_id`를 사용해 해당 `Block.rects`를 강조한다. `NONE`이면 기존 하이라이트를 유지한다.

### 2.5 SessionConfig (모델과 탐색 전략 설정)

```json
{
  "search_model": "bge-m3",
  "search_strategy": "hybrid",
  "local_radius_pages": 1
}
```

- `search_model`과 `search_strategy`는 **서로 독립된 설정**이다.
- `sliding_queue`는 `search_model: null`(기존 비교) 또는 `"e5-small"`(단어 의미 비교)을 지원한다.
  E5를 선택해도 큐·탐색 반경·점수·전환 방식은 유지하며 문장 전체를 랭킹하지 않는다.
  `word_match_threshold`는 개별 단어 코사인 유사도의 일치 기준(0~1, 기본 0.90)이다.
  기존 표기·조사 일치는 이 기준과 관계없이 유지한다.
- 구현체 이름은 예시이며 실제 설치·검증한 모델만 등록한다.
- 최초 설정값은 실험으로 변경할 수 있으므로 한 곳(`config.py`)에서 관리한다.

## 3. HTTP API 계약

> 기준 경로: `/api/v1`. 본문은 별도 설명이 없으면 JSON이다.

### 3.1 `POST /api/v1/documents` — PDF 업로드

- 요청: `multipart/form-data`, 필드명 `file`, MIME `application/pdf`.
- 성공: `201 Created`, 본문은 **Document** (`2.1` 참고).
- 초기 구현은 처리 완료 후 `ready`를 반환한다. 큰 PDF 때문에 비동기 처리가 필요하면 별도 합의 후 `202 Accepted` 흐름을 추가한다.

### 3.2 `GET /api/v1/documents/{document_id}/blocks` — 블록 조회

- 선택 쿼리: `page=5` (없으면 전체 페이지의 블록).
- 성공: `200 OK`.

```json
{
  "document_id": "doc_001",
  "blocks": [
    {
      "block_id": "p0005_b0003",
      "page": 5,
      "text": "운영체제는 프로세스를 관리한다.",
      "block_type": "paragraph",
      "bbox": [0.10, 0.20, 0.80, 0.32],
      "rects": [[0.10, 0.20, 0.80, 0.32]],
      "keywords": []
    }
  ]
}
```

### 3.3 `GET /api/v1/documents/{document_id}/file` — 원본 PDF 조회

- 성공: `200 OK`, `Content-Type: application/pdf`.
- 프론트 PDF 뷰어에서 동일한 원본 파일을 열 때 사용한다.

### 3.4 `POST /api/v1/sessions` — 세션 생성

요청:

```json
{
  "document_id": "doc_001",
  "config": {
    "search_model": "bge-m3",
    "search_strategy": "hybrid",
    "local_radius_pages": 1
  }
}
```

성공: `201 Created`.

```json
{
  "session_id": "sess_001",
  "document_id": "doc_001",
  "status": "created"
}
```

- 세션은 현재 블록, 가장 최근 발화 및 타임라인을 관리한다.
- 이미 생성된 세션의 모델 변경은 초기 버전에 포함하지 않는다. 설정을 바꾸려면 새 세션을 만든다.

### 3.5 `POST /api/v1/search` — 텍스트 기반 탐색 테스트

요청:

```json
{
  "session_id": "sess_001",
  "text": "운영체제는 프로세스를 관리합니다.",
  "segment_id": null
}
```

- 성공: `200 OK`, 본문은 **SearchResult** (`2.4` 참고).
- 이 엔드포인트는 실제 STT를 연결하기 전에 탐색 모델·알고리즘·프론트 UI를 검증하는 용도이다.
- `session_id`로 연결된 문서와 탐색 설정을 사용한다.

### 3.6 `GET /api/v1/sessions/{session_id}/timeline` — 타임라인 조회

성공: `200 OK`.

```json
{
  "session_id": "sess_001",
  "items": [
    {
      "segment_id": "seg_000012",
      "time_ms": 15000,
      "status": "MATCH",
      "page": 5,
      "block_id": "p0005_b0003"
    }
  ]
}
```

- `time_ms`는 발화 종료 시각을 기준으로 한 세션 내 밀리초이다.
- 초기에는 성공한 하이라이트 변경만 저장해도 되지만, 평가용 로그에는 `NONE`까지 기록하는 편이 좋다.

## 4. 브라우저 STT와 실시간 검색

음성 인식은 브라우저에서 실행합니다. 중간/확정 가설을 `POST /api/v1/search/live`로
전달하며 `session_id`, `utterance_id`, `sequence`, `text`, `is_final`을 사용합니다.
응답은 SearchResult에 `buffer_words`, `current_page`, `sequence`를 추가합니다.
미확정 가설은 교체하고 확정 이벤트는 한 번만 반영합니다. 오디오 업로드와 서버
STT WebSocket은 제공하지 않습니다. `NONE`이면 기존 하이라이트를 유지합니다.

## 5. 탐색 모델 교체 계약

### 5.1 탐색 모델과 탐색 전략은 분리한다

- **모델**: 주어진 문장과 후보 블록들을 비교하여 순위/점수를 반환한다. 예: Jev 계열 후보 선택, BGE-M3 임베딩 유사도.
- **전략**: 어떤 후보 블록을 언제 모델에 제공할지 결정한다. 예: `local`, `global`, `hybrid`.
- **탐색 엔진**: 전략 실행 후 `SearchResult`를 만들어 API에 반환한다.

권장 Python 함수 형태(구현 강제 아님):

```python
# search/models/base.py
from typing import Protocol

class SearchModel(Protocol):
    def prepare(self, document_id: str, blocks: list[dict]) -> None:
        ...  # 임베딩 캐시 등 선택적 사전 작업

    def rank(self, query: str, candidates: list[dict]) -> list[dict]:
        ...  # [{"block_id": "p0005_b0003", "score": 0.87}, ...]
```

- 모델별 응답에서 점수를 제공하지 못하면 `score: None`을 허용한다.
- `OUT_OF_LOCAL`은 후보 선택형 모델의 특수 선택지이거나 점수·연속성 기반 판정일 수 있다. **모든 모델이 같은 방식으로 `OUT_OF_LOCAL`을 출력할 필요는 없다.**
- 모델별 실패 기준(임계값·마진·연속성)은 모델별 설정으로 관리한다. 점수 분포가 다른 모델에 동일 임계값을 강제하지 않는다.
- Hybrid는 **Local 성공 → MATCH**, **Local 실패 → Global**, **Global도 실패 → NONE** 흐름을 사용한다.

### 5.2 평가 시 지켜야 할 규칙

- 탐색 모델 비교에는 **동일한 PDF 블록 JSON과 정답 발화 텍스트**를 사용한다.
- STT 비교에는 **동일한 녹음 파일**을 사용한다.
- 전체 시스템 비교에는 실제 음성→STT→탐색→UI 지연을 측정한다.
- 측정 항목: 블록 정확도, 페이지 정확도, 잘못된 하이라이트 비율, Global 호출률, 처리시간, 비용.
- 모델 버전, 전략, 주요 파라미터, 테스트 데이터 버전을 `experiments/` 아래에 기록한다.

## 6. 오류 처리·보안 규칙

HTTP 오류 본문:

```json
{
  "error": {
    "code": "DOCUMENT_NOT_FOUND",
    "message": "해당 PDF 문서를 찾을 수 없습니다.",
    "details": null
  }
}
```

| 상태 코드 | 사용 예시 |
|---|---|
| `400 Bad Request` | 잘못된 설정값, 지원하지 않는 모델 이름 |
| `404 Not Found` | 없는 문서/세션 ID |
| `413 Content Too Large` | PDF 파일 크기 제한 초과 |
| `415 Unsupported Media Type` | PDF가 아닌 파일 업로드 |
| `422 Unprocessable Content` | JSON 필드 형식 검증 실패 |
| `500 Internal Server Error` | 예기치 않은 서버 오류 |

- API 키, 토큰은 **프론트 번들에 포함하지 않고** 백엔드 환경변수에서 관리한다.
- 업로드 파일 크기와 PDF 페이지 수 상한을 정한다. 실험용 파일은 실제 문서 보안·저작권에 유의한다.
- 외부 공개 배포 전에는 사용자 인증, 업로드 접근 권한, 파일 삭제 정책, 호출 제한 등을 별도로 설계해야 한다.
- `NONE`은 정상적인 탐색 결과이지 서버 오류가 아니다. HTTP 오류로 표현하지 않는다.

## 7. 병렬 개발을 위한 Mock 테스트

각 담당자는 실제 모듈을 연결하기 전에 아래 조건을 충족해야 한다.

| 담당 | 단독 완료 기준 |
|---|---|
| 프론트 | 고정 `Block[]`와 `SearchResult`로 페이지 자동 이동·강조가 작동한다. |
| 문서 처리 | 샘플 PDF에서 `Block[]`를 생성하고 블록 ID, 텍스트, 좌표가 유효하다. |
| 브라우저 STT | 인식 텍스트와 중간·확정 여부를 검색 API에 전달한다. |
| 탐색 | 고정 `Block[]`와 텍스트를 받아 `SearchResult`를 반환한다. |
| 통합 | PDF 업로드 → 블록 조회 → 텍스트 탐색 → 하이라이트까지 한 번에 동작한다. |

**검증할 대표 상황:** 정상 매칭, 존재하지 않는 내용(`NONE`), 이전 페이지로 이동, 몇 페이지 건너뛰기, 다중 행 블록, 회전된 PDF, STT 중간 결과 중복, API 오류.

## 8. 아직 확정하지 않은 항목

아래는 실험 또는 구현 상황에 따라 결정한다. **이 문서의 필수 계약과 혼동하지 않는다.**

- 브라우저 음성 인식 지원 범위
- 탐색 최종 모델 및 `OUT_OF_LOCAL`·`NONE` 판단 임계값
- Local 검색 범위(±1페이지/±2페이지 등)와 Global 후보 수
- 오디오 버퍼 길이·갱신 간격, 중간 STT 결과의 탐색 사용 여부
- 복잡한 PDF의 블록 분할 방식(수식·표·다단 레이아웃)
- 데이터 저장 방식(JSON 파일/SQLite/DB) 및 업로드 비동기 처리
- 인증·배포·동시 사용자 처리 방식

## 9. 변경 규칙

1. 이 문서는 GitHub의 `docs/contracts.md`에 저장한다.
2. **기존 필드 삭제·이름 변경·타입 변경**은 담당자 전원 합의 후 PR로 반영한다.
3. 필드를 추가할 때는 가능한 한 선택 항목으로 추가해 기존 개발 코드를 깨지 않도록 한다.
4. API 예시 JSON과 실제 구현이 달라지면 반드시 명세 문서도 함께 수정한다.
5. 이 문서의 `v0.1`은 팀 합의를 위한 **제안 초안**이며, 회의에서 확정한 뒤 `v1.0`으로 승격한다.
