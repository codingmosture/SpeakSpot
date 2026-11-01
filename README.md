# SpeakSpot

**Real-time Speech-Based Document Highlighting System**

SpeakSpot은 실시간 음성 인식(STT)과 문서 탐색 기술을 활용하여, 발표자나 교수자가 현재 설명하고 있는 PDF 문서의 위치를 자동으로 찾아 하이라이트하는 시스템입니다.

## 1. 프로젝트 소개

강의나 발표에서는 발표자가 문서를 순서대로 설명하지 않고 이전 페이지를 다시 언급하거나 특정 그림, 수식, 문단을 설명하는 경우가 많습니다.

이 과정에서 사용자는 설명을 들으면서 동시에 문서의 해당 위치를 직접 찾아야 하는 불편함을 겪습니다.

SpeakSpot은 이러한 문제를 해결하기 위해 음성 인식 결과와 문서 내용을 분석하고, 현재 설명 중인 위치를 실시간으로 추적하여 강조하는 것을 목표로 합니다.

### 주요 기능

- **PDF 분석:** 문서의 텍스트, 블록 및 좌표 추출
- **실시간 STT:** 음성을 텍스트로 변환
- **문서 탐색:** 발화 내용에 대응하는 문서의 페이지와 블록 검색
- **하이브리드 탐색:** Local 탐색 실패 시 Global 탐색으로 전환
- **자동 하이라이트:** 현재 설명 중인 위치 강조 및 페이지 이동
- **타임라인:** 발화에 따른 문서 위치 변화 기록
- **모델 비교:** STT 및 탐색 모델을 교체하며 성능 평가

## 2. 시스템 아키텍처

```text
                  [Frontend]
               React + TypeScript
                       |
                HTTP / WebSocket
                       |
                   [FastAPI]
                       |
          +------------+------------+
          |            |            |
     PDF Processing   STT      Search Engine
          |            |            |
     Block / BBox    Text      Local Search
                                   |
                             Global Search
                            (필요 시 실행)
                                   |
                             Search Result
                                   |
                         PDF Highlight / UI
```

각 모듈은 공통 인터페이스를 기반으로 독립적으로 개발하며, 내부 모델을 교체할 수 있도록 설계합니다.

## 3. 기술 스택

| 영역 | 기술 |
|---|---|
| Frontend | React, TypeScript, Vite |
| Backend | Python, FastAPI, Pydantic |
| PDF Processing | PyMuPDF (예정) |
| STT | 모델 비교 후 결정 |
| Search Model | 모델 비교 후 결정 |
| Search Strategy | Local / Global / Hybrid |
| Communication | REST API, WebSocket (예정) |
| Collaboration | Git, GitHub |

## 4. 프로젝트 구조

```text
SpeakSpot/
├── frontend/                  # React 프론트엔드
│   └── src/
│
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI 진입점
│   │   ├── api/               # API 엔드포인트
│   │   ├── documents/         # PDF 전처리
│   │   ├── stt/               # 음성 인식
│   │   ├── search/            # 탐색 모델 및 알고리즘
│   │   └── schemas/           # 공통 데이터 형식
│   └── requirements.txt
│
├── experiments/               # 모델 실험 및 성능 비교
├── docs/
│   └── contracts.md           # 공통 인터페이스 명세
├── .gitignore
└── README.md
```

## 5. 개발 환경 설정

### 사전 준비

- Node.js 및 pnpm
- Python 3.11 이상 권장
- Git

### 5.1 Repository Clone

```bash
git clone https://github.com/codingmosture/SpeakSpot.git
cd SpeakSpot
git switch develop
```

### 5.2 Frontend 실행

프로젝트 루트에서 실행합니다.

```bash
cd frontend
pnpm install
pnpm dev
```

개발 서버: http://localhost:5173

### 5.3 Backend 실행

별도 터미널에서 프로젝트 루트로 이동한 뒤 실행합니다.

```bash
cd backend

# 가상환경 생성 (최초 1회)
py -m venv .venv

# 가상환경 활성화 (Windows Git Bash)
source .venv/Scripts/activate

# 의존성 설치
python -m pip install -r requirements.txt

# FastAPI 실행
python -m uvicorn app.main:app --reload
```

- API 서버: http://127.0.0.1:8000
- Swagger UI: http://127.0.0.1:8000/docs
- Health Check: http://127.0.0.1:8000/api/health

가상환경 종료:

```bash
deactivate
```

프론트엔드와 백엔드는 각각 별도 터미널에서 실행해야 합니다.

## 6. API

현재는 실제 AI 모델 대신 Mock API를 사용하여 기본 데이터 흐름을 검증합니다.

| Method | Endpoint | 설명 |
|---|---|---|
| GET | `/api/health` | 서버 상태 확인 |
| GET | `/api/mock/blocks` | 샘플 PDF 블록 반환 |
| POST | `/api/search` | 발화 텍스트 기반 Mock 탐색 |

실제 PDF 업로드, 실시간 STT 및 세션 관리 API는 이후 추가할 예정입니다.

API 요청·응답과 모듈 간 공통 데이터 구조는 `docs/contracts.md`를 참고합니다.

## 7. Git 브랜치 전략

SpeakSpot은 `main`, `develop`, `feat/*` 브랜치를 사용합니다.

| 브랜치 | 역할 |
|---|---|
| `main` | 안정화된 최종 코드 |
| `develop` | 개발 기능 통합 및 테스트 |
| `feat/*` | 개별 기능 개발 |
| `fix/*` | 버그 수정 |
| `chore/*` | 환경 설정 및 기타 작업 |

### 개발 흐름

```text
feat/* ── Pull Request ──> develop
                              |
                         통합 테스트
                              |
                         Pull Request
                              |
                              v
                             main
```

### 7.1 새로운 기능 개발

모든 기능 개발은 최신 `develop` 브랜치에서 시작합니다.

```bash
git switch develop
git pull origin develop

git switch -c feat/기능명
```

예시:

```text
feat/pdf-parser
feat/stt-whisper
feat/search-local
feat/pdf-viewer
```

### 7.2 작업 내용 Push

```bash
git add .
git commit -m "feat: implement feature"
git push -u origin feat/기능명
```

### 7.3 Pull Request

GitHub에서 다음과 같이 PR을 생성합니다.

- **Base:** `develop`
- **Compare:** `feat/기능명`

PR에는 구현 내용, 테스트 결과, 특이사항을 작성합니다.

코드 검토 및 통합 테스트 후 `develop`에 병합합니다.

### 7.4 main 병합

`develop`에서 기능 통합 및 테스트가 완료되면 `main`으로 Pull Request를 생성합니다.

- `main` 직접 Push는 관리자만 허용합니다.
- 일반 기능 개발은 반드시 `develop`을 기준으로 진행합니다.
- `main` 병합은 팀장이 최종 확인합니다.

## 8. 협업 규칙

1. 새로운 기능은 개별 브랜치에서 개발합니다.
2. `main`과 `develop`에는 직접 Push하지 않고 PR을 사용합니다.
3. PR 병합 전 실행 및 테스트 결과를 확인합니다.
4. 공통 데이터 형식을 변경하면 관련 Schema와 명세서를 함께 수정합니다.
5. API Key 및 환경변수는 `.env`로 관리하고 GitHub에 업로드하지 않습니다.
6. 대용량 모델 파일과 임시 데이터는 Git으로 관리하지 않습니다.
7. 다른 모듈에 영향을 주는 변경 사항은 팀원들과 사전에 공유합니다.

## 9. 개발 현황

### 초기 환경

- [x] React + TypeScript 개발 환경
- [x] FastAPI 개발 환경
- [x] 프론트엔드·백엔드 연결
- [x] 공통 데이터 Schema 초안
- [x] Mock 탐색 API 및 프론트 연동

### 핵심 기능

- [ ] PDF 텍스트 및 좌표 추출
- [ ] PDF 블록 전처리
- [ ] STT 모델 연동
- [ ] 탐색 모델 연동
- [ ] Local 탐색 구현
- [ ] Global 탐색 구현
- [ ] Hybrid 탐색 구현
- [ ] 실제 PDF 하이라이트
- [ ] 실시간 음성 스트리밍
- [ ] 타임라인 기록

### 실험 및 평가

- [ ] STT 모델 성능 비교
- [ ] 탐색 모델 정확도 비교
- [ ] 탐색 전략별 정확도·지연 측정
- [ ] 전체 파이프라인 성능 평가

---

**SpeakSpot — Follow the speech. Find the spot.**
