# SpeakSpot

브라우저에서 인식한 발화로 PDF의 설명 위치를 찾고 해당 블록과 페이지를 강조합니다.

**기본 동작:** 브라우저 한국어 STT + Sliding Queue(최근 명사·영문 용어 8개, 현재 페이지 ±5).
PDF 텍스트·한국어/영어 OCR, 회전 좌표, 그림/표/불릿, 타임라인과 자동 페이지 이동을 지원합니다.
개발자 메뉴에서 Local/Global/Hybrid1·2,
E5-small/BGE-M3/Jev, 검색 가중치와 발화 반영 주기를 선택할 수 있습니다.
슬라이딩 큐에서도 E5-small을 선택해 발화 단어와 PDF 단어의 의미 유사도로 일치를 판정할 수 있습니다.

검색 큐에는 Kiwi로 추출한 일반명사·고유명사와 영문 용어/식별자만 넣습니다.
조사·어미·동사·형용사·대명사·의존명사·단독 숫자는 제외하고, 기본 8개를 넘으면
오래된 명사부터 빠집니다. 새 명사는 비교 결과를 캐시하고 빠진 명사의 점수 기여분은
제거합니다. E5는 단어 의미 비교를 보조하며 음성 인식 원문을 교정하지 않습니다.
PDF에서 누락된 한글 공백은 실제 글자 간격을 읽어 추출 단계에서 복원합니다.

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

**설치 목록은 `backend/requirements.txt` 하나입니다.** API·PDF·검색 모델·테스트 도구를 모두 포함합니다. OCR 언어 파일은 `backend/.cache/tessdata/`에 별도 설치되며 Git에 포함되지 않습니다. PyMuPDF 내장 OCR을 사용하므로 Tesseract 실행 파일을 별도로 설치할 필요는 없습니다.

별도 터미널의 프로젝트 루트에서 프론트를 실행합니다.

```powershell
cd frontend
pnpm install
pnpm dev
```

### Windows 한 번에 실행

설치가 끝난 Windows 환경에서는 루트의 **[`SpeakSpot.cmd`](SpeakSpot.cmd)를 더블클릭**하면 됩니다.
`Start-SpeakSpot.ps1`이 `backend/.venv/Scripts/python.exe`로 Uvicorn을 실행하고,
`frontend`에서 `pnpm dev`를 실행한 뒤 준비가 끝나면 브라우저를 엽니다.
서버는 백그라운드에서 실행되며, 이미 실행 중이면 재사용합니다. 다른 프로그램이
8000/5173 포트를 점유하면 종료시키지 않고 오류를 안내합니다.
로그는 `.cache/launcher/`에 저장합니다. 가상환경·의존성이 없으면 먼저 위 설치를 진행하세요.
실행기는 `http://127.0.0.1:5173`을 열며 브라우저를 닫아도 서버는 계속 실행됩니다.
브라우저 없이 서버만 준비하려면 다음 명령을 사용합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-SpeakSpot.ps1 -NoBrowser
```

- 앱: [localhost:5173](http://localhost:5173)
- API: [127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- 상태: [127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health)

macOS/Linux에서는 `python3 -m venv .venv`, `.venv/bin/python`을 사용합니다.

## 사용과 설정

1. PDF 뷰어 위의 업로드 버튼 → 파일명 표시 → OCR/텍스트 처리와 검색 세션 생성
2. 마이크 인식 → 실시간 매칭 패널에서 명사 큐·판단 상태·블록 본문·페이지·점수 확인
3. 그 아래 타임라인에서 확정된 발화를 선택 → 해당 위치로 이동
4. 개발자 메뉴 → 블록 경계 표시 아래에서 자동 페이지 이동 설정
5. 검색/STT 설정 적용 → 새 세션을 만들고 큐·이력 초기화

실시간 패널은 중간 인식부터 **판단 중 / 블록 매칭 / 위치 미확정**을 갱신합니다.
선택된 블록의 본문·페이지·점수를 바로 볼 수 있고, 새 위치가 확정되지 않으면
유지 중인 블록을 구분해 표시합니다. 타임라인에는 확정된 발화가 기록됩니다.
일반 화면의 수동 텍스트 검색은 제거했으며 직접 텍스트 검색 API는 유지합니다.

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

검색 모델 첫 다운로드에는 인터넷과 시간이 필요합니다. 음성 인식은 브라우저 STT만 사용하며 브라우저·네트워크 지원에 영향을 받습니다. OCR은 이미지 속 글자를 추출하며 사진 의미나 복잡한 수식의 복원을 보장하지 않습니다.

## 구조

```text
backend/
├── app/
│   ├── main.py       # 통합 FastAPI 앱
│   ├── api/          # 문서·검색 HTTP API
│   ├── documents/    # PDF 파서·OCR·업로드 서비스
│   ├── search/       # 검색 엔진·알고리즘·모델
│   └── schemas/      # 공통 데이터 형식
├── scripts/          # OCR 데이터 준비
├── tests/            # PDF·검색·통합 회귀 테스트
└── requirements.txt # 유일한 Python 설치 목록
frontend/src/
├── App.tsx           # 문서·세션·발화 큐와 화면 연결
├── components/       # 헤더/녹음, PDF/업로드, 실시간 매칭/타임라인, 개발자 메뉴
├── hooks/            # 브라우저 STT
├── api/              # server·mock·legacy 어댑터와 인터페이스
├── lib/              # PDF·좌표·발화 유틸리티
├── types/            # 통신 계약
├── config.ts         # 기본 설정
└── index.css         # 리퀴드 글래스 UI, 반응형·접근성·PDF 스타일
docs/contracts.md     # API 계약
experiments/          # 모델 실험 자료
SpeakSpot.cmd        # Windows 더블클릭 실행
Start-SpeakSpot.ps1   # 가상환경 백엔드 + pnpm dev 준비·실행
```

문서·검색 세션·타임라인은 단일 서버 프로세스의 메모리에 저장합니다. 재시작하면 PDF를 다시 업로드해야 합니다. 업로드 원본(`backend/uploaded_pdfs/`), OCR/모델 캐시, 가상환경과 실제 `.env` 파일은 소스와 분리합니다.

## API와 상세 안내

| 경로 | 역할 |
|---|---|
| `POST /api/v1/documents` | PDF 업로드·추출·검색 등록, 최대 40 MB |
| `GET /api/v1/documents/{id}/blocks`, `/file` | 블록/좌표와 원본 |
| `POST /api/v1/sessions` | 검색 세션 |
| `POST /api/v1/search` | 확정 발화·직접 텍스트 검색 |
| `POST /api/v1/search/live` | 중간/확정 발화별 실제 큐·블록·페이지 판단 갱신 |
| `POST /api/v1/search/preview` | 세션 상태를 저장하지 않는 잠정 검색 |
| `GET /api/v1/sessions/{id}/timeline`, `/search-state` | 이력·현재 페이지·단어 큐 |
| `POST /api/v1/sessions/{id}/reset-search` | 검색 초기화 |

직접 블록 등록과 옵션 조회, 기존 `/api/mock/blocks`·`/api/search` 호환 API도 유지합니다.
자세한 형식은 [API 계약](docs/contracts.md)과 실행 중인 `/docs`를 참고하세요.
음성 인식은 브라우저 Web Speech API가 담당하며 백엔드는 오디오를 수신하지 않습니다.

- [프론트 설정과 구성](frontend/README.md)
- [PDF/OCR 설치·범위](backend/app/documents/README.md)
- [검색 기준](backend/app/search/README.md)

## 직접 확인

문서·검색 API와 브라우저 음성 인식을 다음 명령으로 확인할 수 있습니다.

```powershell
# backend/
.\.venv\Scripts\python.exe -m pytest tests -q
# frontend/
pnpm build
pnpm lint
pnpm test
```

텍스트/이미지/혼합·회전 PDF → 검색·하이라이트 → 브라우저 STT의 중간/확정 발화 →
반영 주기 변경 → 타임라인·페이지 이동 → 검색 모델 변경 순서로 확인할 수 있습니다.
기존 업로드 API 테스트는 `uploaded_pdfs/`에 파일을 쓸 수 있으므로 보관할 파일은 먼저 별도 복사합니다.
