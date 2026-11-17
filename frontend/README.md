# SpeakSpot Frontend

React + TypeScript + Vite. 설치·전체 실행은 [루트 README](../README.md)에 있습니다.

```sh
pnpm install
pnpm dev      # localhost:5173
pnpm build    # 타입 확인 + 배포 빌드
pnpm lint
pnpm test
pnpm preview
```

## 설정

`.env.example`을 `.env.development.local`로 복사한 뒤 필요할 때만 수정합니다.

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `VITE_API_MODE` | `server` | 실제 API. `mock`은 샘플, `legacy`는 초안 API 호환 |
| `VITE_API_ORIGIN` | 같은 주소 | 미지정 시 Vite가 `/api` HTTP를 `127.0.0.1:8000`으로 전달 |
| `VITE_DEV_TOOLS` | 개발 `true`, 배포 `false` | 개발자 메뉴 노출 |

초기 입력은 브라우저 한국어 STT입니다. 음성 인식은 브라우저 STT만 사용합니다. 세션 설정은 `src/config.ts`, 통신 형식은 [계약](../docs/contracts.md)을 따릅니다.
설정 적용 시 새 세션이 생기고 큐와 이력은 초기화됩니다. PDF는 유지합니다.
기본 큐는 명사·영문 용어 8개입니다. 업로드 버튼과 파일명은 PDF 뷰어 상단에,
실시간 명사 큐·판단 상태·선택 블록은 오른쪽 패널에 표시하며 타임라인은 그 아래에 둡니다.
자동 페이지 이동은 개발자 메뉴의 블록 경계 표시 아래에서 변경합니다.
Windows에서는 설치 후 루트의 `SpeakSpot.cmd`로 백엔드 가상환경과 `pnpm dev`를 함께 실행할 수 있습니다.
실행기는 `http://127.0.0.1:5173`을 열고 이미 실행 중인 서버를 재사용합니다.
이 화면은 음성 인식을 중심으로 사용하며 수동 텍스트 검색 영역은 표시하지 않습니다.

## 코드

- `App.tsx`: 문서·검색 세션·실시간 발화 큐·결과 연결
- `components/AppHeader.tsx`: 브랜드·상태·개발자 버튼·녹음 도구
- `components/Timeline.tsx`: 실시간 명사 큐·블록 판단·측정값과 확정 발화 이력
- `components/PdfViewer.tsx`: PDF 업로드·파일명·렌더링·하이라이트
- `components/DeveloperPanel.tsx`: 검색 설정·블록 경계·자동 페이지 이동
- `hooks/`: 브라우저 음성 인식
- `api/`: server·mock·legacy. HTTP 전송과 오류 처리는 `http.ts` 재사용
- `index.css`: 반투명 유리·빛 반사·부드러운 음영. 작은 화면과 모션 감소 설정 지원

브라우저 중간 발화는 `/search/live`에서 최신 가설로 교체하고, 확정 이벤트는 한 번만 반영합니다.
실시간·1초·2초·문장 단위 반영을 선택할 수 있습니다. `NONE`이면 이전 하이라이트를 유지합니다.
실시간 패널은 요청 중에는 `판단 중`, `MATCH`에는 선택된 블록과 점수,
`NONE`에는 `위치 미확정`과 유지 중인 블록을 구분해 표시합니다. 탐색 상세에서
발화 원문·블록 ID·모델·탐색 시간·왕복 시간을 확인할 수 있습니다.

PDF의 실제 종이는 불투명 흰색으로 유지해 읽기와 좌표 강조에 영향을 주지 않습니다.
리퀴드 글래스는 주변 패널·도구·배경·설정 창에 적용합니다. 추가 UI 패키지나 외부 이미지 요청은 없습니다.
