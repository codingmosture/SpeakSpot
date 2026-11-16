# SpeakSpot

발화와 입력 문장으로 PDF의 설명 위치를 찾고 해당 블록과 페이지를 강조합니다.

**기본 동작:** 브라우저 한국어 STT + Sliding Queue(최근 15단어, 현재 페이지 ±5).
PDF 텍스트·한국어/영어 OCR, 회전 좌표, 그림/표/불릿, 타임라인과 자동 페이지 이동을 지원합니다.
개발자 메뉴에서 서버 STT(Faster-Whisper/Google/CLOVA), Local/Global/Hybrid1·2,
E5-small/BGE-M3/Jev, 검색 가중치와 발화 반영 주기를 선택할 수 있습니다.

## 설치와 실행

프로젝트 루트에서 시작하는 Windows PowerShell 기준입니다. Node.js 22 이상과 pnpm,
Python 3.11 이상을 사용합니다. 기존 가상환경이 있으면 생성 명령은 생략합니다.

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/setup_ocr.py
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

**설치 목록은 `backend/requirements.txt` 하나입니다.** API·PDF·검색 모델·로컬/클라우드 STT·테스트 도구를 모두 포함합니다. OCR 언어 파일은 `backend/.cache/tessdata/`에 별도 설치되며 Git에 포함되지 않습니다. PyMuPDF 내장 OCR을 사용하므로 Tesseract 실행 파일을 별도로 설치할 필요는 없습니다.

별도 터미널의 프로젝트 루트에서 프론트를 실행합니다.

```powershell
cd frontend
pnpm install
pnpm dev
```

- 앱: [localhost:5173](http://localhost:5173)
- API: [127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- 상태: [127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health)

macOS/Linux에서는 `python3 -m venv .venv`, `.venv/bin/python`을 사용합니다.

## 사용과 설정

1. PDF 업로드 → OCR/텍스트 처리 → 검색 세션 자동 생성
2. 텍스트로 찾기 또는 마이크 인식 → 매칭 블록 강조와 페이지 이동
3. 타임라인 항목 선택 → 이전 설명 위치 확인
4. 개발자 메뉴 → 검색/STT 설정. 설정 적용 시 새 세션을 만들고 큐·이력 초기화

프론트 API 기본값은 개발·배포 모두 `server`입니다. 백엔드 없이 화면만 확인하려면
`frontend/.env.development.local`에 `VITE_API_MODE=mock`을 넣습니다. 기존 `legacy` 모드도 유지합니다.
`VITE_DEV_TOOLS`는 개발자 메뉴 노출, `VITE_API_ORIGIN`은 별도 백엔드 주소입니다.

백엔드는 실행 프로세스의 환경변수를 읽습니다. `.env`를 쓸 때는 Uvicorn에
`--env-file .env`를 명시합니다. 환경변수 변경 후 서버를 재시작합니다.

| 설정 | 용도 |
|---|---|
| `SPEAKSPOT_OCR_ENABLED`, `SPEAKSPOT_OCR_LANGUAGE`, `SPEAKSPOT_OCR_DPI` | OCR 활성화·언어·해상도 |
| `SPEAKSPOT_OCR_TESSDATA` / `TESSDATA_PREFIX` | OCR 데이터 경로, 미지정 시 로컬 캐시 탐색 |
| `SEARCH_E5_SMALL_*`, `SEARCH_BGE_M3_*` | 임베딩 모델 ID·장치·배치 크기 |
| `TYPESAFE_API_KEY`, `TYPESAFE_JEV_*` | Jev 인증·요청 설정 |
| Google/CLOVA 인증과 `STT_ALLOWED_ORIGINS` | [STT 안내](backend/app/stt/README.md) |

모델 첫 다운로드에는 인터넷과 시간이 필요합니다. 브라우저 STT는 브라우저·네트워크 지원에,
서버 STT는 장치 성능에 영향을 받습니다. OCR은 이미지 속 글자를 추출하며 사진 의미나 복잡한 수식의 복원을 보장하지 않습니다.

## 구조

```text
backend/
├── app/
│   ├── main.py       # 통합 FastAPI 앱
│   ├── api/          # 문서·검색·STT HTTP/WebSocket, STT 단독 앱
│   ├── documents/    # PDF 파서·OCR·업로드 서비스
│   ├── search/       # 검색 엔진·알고리즘·모델
│   ├── stt/          # 음성 제공자·스트리밍·CLI·테스트·README
│   └── schemas/      # 공통 데이터 형식
├── scripts/          # OCR 데이터 준비
├── tests/            # PDF·검색·통합 회귀 테스트
└── requirements.txt # 유일한 Python 설치 목록
frontend/src/
├── App.tsx           # 문서·세션·발화 큐와 화면 연결
├── components/       # 헤더/녹음, 검색/업로드, PDF, 타임라인/결과, 개발자 메뉴
├── hooks/            # 브라우저/서버 STT
├── api/              # server·mock·legacy 어댑터와 인터페이스
├── lib/              # PDF·PCM·좌표·발화 유틸리티
├── types/            # 통신 계약
├── config.ts         # 기본 설정
└── index.css         # 리퀴드 글래스 UI, 반응형·접근성·PDF 스타일
docs/contracts.md     # API 계약
experiments/          # 모델 실험 자료
```

문서·검색 세션·타임라인은 단일 서버 프로세스의 메모리에 저장합니다. 재시작하면 PDF를 다시 업로드해야 합니다. 업로드 원본(`backend/uploaded_pdfs/`), OCR/모델 캐시, 가상환경과 실제 `.env` 파일은 소스와 분리합니다.

## API와 상세 안내

| 경로 | 역할 |
|---|---|
| `POST /api/v1/documents` | PDF 업로드·추출·검색 등록, 최대 40 MB |
| `GET /api/v1/documents/{id}/blocks`, `/file` | 블록/좌표와 원본 |
| `POST /api/v1/sessions` | 검색 세션 |
| `POST /api/v1/search`, `/live`, `/preview` | 확정·실시간·미리보기 검색 |
| `GET /api/v1/sessions/{id}/timeline`, `/search-state` | 이력·현재 페이지·단어 큐 |
| `POST /api/v1/sessions/{id}/reset-search` | 검색 초기화 |
| `WS /api/v1/sessions/{id}/stream` | PCM → STT → 검색/하이라이트 |

직접 블록 등록과 옵션 조회, 기존 `/api/mock/blocks`·`/api/search` 호환 API도 유지합니다.
자세한 형식은 [API 계약](docs/contracts.md)과 실행 중인 `/docs`를 참고하세요.

- [프론트 설정과 구성](frontend/README.md)
- [PDF/OCR 설치·범위](backend/app/documents/README.md)
- [검색 기준](backend/app/search/README.md)
- [STT 단독 앱·마이크·CLI·클라우드 인증](backend/app/stt/README.md)

## 직접 확인

리팩토링 작업에서는 테스트·빌드·서버 실행·패키지 재설치를 하지 않았습니다.
Python 문법·로컬 참조와 TypeScript 타입에 대한 정적 점검만 수행합니다.

```powershell
# backend/
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m unittest discover -s app/stt/tests -p "test_*.py" -v
# frontend/
pnpm build
pnpm lint
pnpm test
```

텍스트/이미지/혼합·회전 PDF → 검색·하이라이트 → 브라우저 STT의 중간/확정 발화 →
반영 주기 변경 → 타임라인·페이지 이동 → 서버 STT·선택 모델 순서로 확인할 수 있습니다.
기존 업로드 API 테스트는 `uploaded_pdfs/`에 파일을 쓸 수 있으므로 보관할 파일은 먼저 별도 복사합니다.
