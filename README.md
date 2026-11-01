# SpeakSpot

**Real-time Speech-Based Document Highlighting System**

SpeakSpot은 사용자의 음성을 실시간으로 분석하여 PDF 문서에서 현재 설명 중인 위치를 자동으로 찾아 하이라이트하는 시스템입니다.

## 주요 기능

- PDF 텍스트 및 좌표 추출
- STT 기반 실시간 음성 인식
- Local / Global / Hybrid 문서 탐색
- 현재 설명 중인 PDF 블록 하이라이트
- STT 및 탐색 모델 교체와 성능 비교
- 발화 기반 하이라이트 타임라인

## 기술 스택

| 구분 | 기술 |
|---|---|
| Frontend | React, TypeScript, Vite |
| Backend | Python, FastAPI, Pydantic |
| PDF Processing | PyMuPDF (예정) |
| STT | 모델 비교 후 결정 |
| Search | 모델 비교 후 결정 |
| Communication | HTTP, WebSocket (예정) |

## 프로젝트 구조

- `frontend/` — React 프론트엔드
- `backend/app/api/` — API 엔드포인트
- `backend/app/documents/` — PDF 전처리
- `backend/app/stt/` — 음성 인식
- `backend/app/search/` — 문서 탐색 알고리즘
- `backend/app/schemas/` — 공통 데이터 모델
- `experiments/` — 모델 성능 비교 및 실험
- `docs/` — 설계 문서 및 인터페이스 명세

## 개발 환경 실행

**사전 준비:** Node.js, pnpm, Python 3.11 이상

### Frontend

프로젝트 루트에서 실행합니다.

```bash
cd frontend
pnpm install
pnpm dev
```

개발 서버: http://localhost:5173

### Backend

프로젝트 루트에서 실행합니다.

```bash
cd backend
py -m venv .venv
source .venv/Scripts/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Windows Git Bash 기준입니다.

- API 서버: http://127.0.0.1:8000
- API 문서: http://127.0.0.1:8000/docs
- Health Check: http://127.0.0.1:8000/api/health

프론트엔드와 백엔드는 각각 별도 터미널에서 실행합니다.

## 협업 규칙

1. 새로운 기능은 별도의 브랜치에서 개발합니다.
2. `main` 브랜치에 직접 Push하지 않습니다.
3. Pull Request를 생성하여 코드 리뷰 후 병합합니다.
4. 공통 데이터 형식 변경 시 `docs/contracts.md`를 함께 수정합니다.
5. API Key, 가상환경, 모델 가중치, 대용량 데이터는 GitHub에 업로드하지 않습니다.

### 브랜치 이름 예시

- `feat/pdf-parser`
- `feat/stt-whisper`
- `feat/search-local`
- `feat/pdf-viewer`
- `fix/search-error`
- `chore/project-setup`

## 개발 현황

- [x] React 개발 환경 구성
- [x] FastAPI 개발 환경 구성
- [x] 프론트엔드·백엔드 통신 확인
- [ ] PDF 전처리
- [ ] STT 모델 연동
- [ ] 문서 탐색 알고리즘
- [ ] 실제 PDF 하이라이트
- [ ] 실시간 음성 처리
- [ ] 모델 비교 및 성능 평가

## 프로젝트 문서

공통 데이터 형식과 API 명세는 `docs/contracts.md`를 참고합니다.
