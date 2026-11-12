# SpeakSpot Frontend

React + TypeScript + Vite. API·데이터 형식은 [`docs/contracts.md`](../docs/contracts.md) (v0.1)를 따릅니다.

## 실행

```bash
pnpm install
pnpm dev        # http://localhost:5173
pnpm build      # 타입 체크 + 프로덕션 빌드
pnpm lint
```

### API 모드 (`VITE_API_MODE`)

| 모드 | 설명 |
|---|---|
| `mock` (개발 기본값) | 백엔드 없이 프론트 내장 Mock API 사용 (contracts.md §7). 샘플 3페이지 문서로 시작 |
| `legacy` | backend 초안 API(`/api/mock/blocks`, `/api/search`)를 호출하고 응답을 contracts.md 형식으로 변환. backend 를 같이 띄워 연동 테스트할 때 사용 |
| `server` (배포 기본값) | backend `/api/v1` 사용 (업로드·세션·WebSocket STT). backend 가 명세를 구현하면 이 모드로 전환 |

`legacy`/`server` 모드는 Vite 프록시가 `/api` (WebSocket 포함)를 `127.0.0.1:8000` 으로 전달합니다.
기본값은 `pnpm dev` 이면 `mock`, `pnpm build` 이면 `server` 이며 env 파일이 없어도 동작합니다.
바꾸려면 `.env.example` 을 복사해 `.env.development.local` 로 저장하고 값을 수정한 뒤 다시 실행하세요.
(루트 `.gitignore` 규칙에 따라 `.env.*` 파일은 git 에 올라가지 않고, `.env.example` 만 올라갑니다.)

```bash
# frontend/.env.development.local
VITE_API_MODE=legacy
```

### 개발 도구 (`VITE_DEV_TOOLS`)

`pnpm dev` 에서는 기본으로 켜져 있어 화면 오른쪽 아래에 **개발 도구** 칸이 보입니다.
- STT 소스 선택: 서버 STT (WebSocket) / 브라우저 STT (Web Speech API, 테스트용)
- 블록 경계 표시: 블록 위치와 `block_id` 확인

실제 사용자 화면을 보려면 `.env.development.local` 에 `VITE_DEV_TOOLS=false` 를 넣고 다시 실행하세요.
배포용 빌드(`pnpm build`)에서는 기본으로 꺼집니다.
STT 소스 기본값은 `server` 모드에서 서버 STT, 그 외 모드에서 브라우저 STT 입니다.

> `legacy` 모드 제약: 초안에는 업로드·세션·STT 스트리밍이 없어 프론트에서 흉내 냅니다.
> `stage_used` 는 선택한 전략으로 추정하고, `processing_ms` 는 왕복 시간으로 대신합니다.
> v1 이 완성되면 `src/api/legacy.ts` 를 삭제하세요.

## 동작 흐름

1. **PDF 업로드** → `POST /api/v1/documents` → `GET /documents/{id}/blocks`
   - 화면 렌더링은 업로드한 로컬 파일을 pdf.js 로 그림 (프론트는 PDF 파싱을 하지 않음)
2. **세션 생성** → `POST /api/v1/sessions` (문서가 바뀌면 새 세션, 설정은 `config.ts` 고정값 — 사용자가 모델·전략을 고르지 않음)
3. **탐색**
   - 서버 STT: `WS /api/v1/sessions/{id}/stream` 으로 16 kHz mono PCM s16le 전송 → `stt.result`, `highlight.update` 수신
   - 브라우저 STT (테스트용, Chrome/Edge): Web Speech API 확정 문장을 `POST /api/v1/search` 로 전송
   - 텍스트로 찾기 (사용자 기능): `POST /api/v1/search`
4. **하이라이트**: `MATCH` 이면 `block_id` 의 `rects` 를 강조하고 해당 페이지로 이동. `NONE` 이면 이전 하이라이트 유지

## 구조

```text
src/
├── types/contracts.ts        # contracts.md 의 공통 타입
├── api/
│   ├── client.ts             # VITE_API_MODE 에 따라 api 구현 선택
│   ├── http.ts               # 실제 백엔드 구현
│   ├── legacy.ts             # backend 초안 API 어댑터 (임시)
│   ├── mock.ts, mockData.ts  # Mock 구현 + 샘플 블록
│   └── types.ts              # SpeakSpotApi 인터페이스, ApiError
├── hooks/
│   ├── useServerStt.ts       # WebSocket 스트리밍 STT
│   └── useSpeechRecognition.ts  # 브라우저 STT
├── lib/
│   ├── pcmWorklet.ts         # 마이크 → 16 kHz PCM 변환 AudioWorklet
│   ├── pdf.ts                # pdf.js 로더 (지연 로드)
│   └── bbox.ts               # 정규화 좌표 → CSS
├── components/               # PdfViewer, ControlPanel, ResultCard, Timeline, AppHeader
├── config.ts                 # 세션 고정 설정 (모델·전략)
└── App.tsx                   # 상태 관리 및 흐름 연결
```

## 참고

- `config.ts` 의 모델 이름은 contracts.md 예시값입니다. 실험으로 최종 STT·탐색 모델 조합이 정해지면 갱신하세요.
- Mock 탐색은 테스트용 문자 bigram 매칭이며 실제 탐색 모델과 무관합니다.
- OneDrive 폴더에서 빌드가 아무 메시지 없이 멈추면 `dist/` 를 지우고 다시 빌드하세요.

# SpeakSpot 통합 실행

기본 프론트엔드 모드는 **server**입니다. `mock`은 프론트만 시험할 때 명시적으로 선택합니다.

1. 백엔드(`backend/`): `python -m pip install -r requirements-integrated.txt` 후 `python -m uvicorn app.main:app --reload`
2. 프론트(`frontend/`): `pnpm install` 후 `pnpm dev`
3. http://localhost:5173 에서 PDF를 업로드하면 자동으로 블록을 등록하고 검색 세션을 생성합니다.
4. 텍스트 입력 → 검색, 마이크 → STT → 검색 → PDF 하이라이트를 확인할 수 있습니다.

기본값은 검색 Hybrid1+BGE-M3, STT Faster-Whisper tiny입니다. BGE-M3 첫 다운로드와 임베딩, Whisper 첫 다운로드에는 시간이 걸릴 수 있으며 인터넷이 필요합니다. CPU 성능에 따라 처리시간이 길어질 수 있습니다. 클라우드 STT는 서버 측 인증이 별도로 필요합니다.

프론트 단독 실행은 `VITE_API_MODE=mock` 설정을 사용합니다.

개별 STT 테스트 서버 `app.api.stt_app:app`도 계속 유지됩니다. 실제 통합 API는 `app.main:app`에 연결되어 있습니다.
