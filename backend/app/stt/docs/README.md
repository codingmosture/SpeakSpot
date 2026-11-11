# SpeakSpot STT 안내서

이 문서는 STT 담당자가 코드를 이해하고 테스트한 뒤 팀의 프론트엔드·API 담당자에게 연결 방법을 전달하기 위한 안내서입니다. 기준일은 2026-11-11입니다.

## 무엇부터 읽으면 되나요

| 하고 싶은 일 | 읽을 문서 |
|---|---|
| 파일 역할과 코드 흐름 이해 | [01 파일별 명세](01-FILES.md) |
| 서버 실행과 /docs 테스트 | [02 실행과 테스트](02-RUN-AND-TEST.md) |
| 브라우저 마이크와 STT 연결 | [03 프론트엔드 통신](03-FRONTEND-INTEGRATION.md) |
| 검증한 범위와 남은 작업 확인 | [04 검증과 팀 통합](04-VALIDATION.md) |
| Google·CLOVA 인증 설정 또는 오류 해결 | [05 클라우드 인증](05-CLOUD-STREAMING.md) |

처음 실행한다면 **02의 설치 → 클라우드 사용 시 05의 인증 → 02의 서버 실행·테스트** 순서로 진행하세요.

## 현재 작업 위치

```text
C:\Users\goaal\Documents\박현민\임시공간\SpeakSpot-main\SpeakSpot-main
```

STT 소스는 `backend/app/api`, `backend/app/schemas`, `backend/app/stt`에 있습니다.
STT 단독 서버 진입점은 **`app.api.stt_app:app`**입니다. `app.main:app`은 기존 팀 앱으로, 실행하면 다른 API가 보일 수 있습니다.

## 현재 지원 범위

| 실행 경로 | 음성 입력 위치 | faster-whisper | Google·CLOVA |
|---|---|---|---|
| `/api/stt/live/*` — /docs 마이크 테스트 | 서버 PC 마이크 | 짧은 청크 반복 추론 | **실제 gRPC 스트리밍** |
| `/api/stt/stream` — WebSocket | 클라이언트가 보낸 PCM | 지원 | **현재 미연결** |
| `/api/stt/transcribe` — 파일 업로드 | 업로드한 파일 | 지원 | REST 파일 인식 |
| `python -m app.stt.live` — 터미널 | 실행 PC 마이크 | 지원 | 기존 REST 청크 호출 |
| `evaluate_stt` — 평가 도구 | WAV 파일 | 지원 | 기존 REST 청크 호출 |

**Google·CLOVA 실제 스트리밍 비교는 현재 `/docs`의 마이크 테스트에서 합니다.** 이 기능이 브라우저 사용자 마이크까지 자동으로 연결된 상태는 아닙니다. 프론트엔드 연결 범위는 03 문서를 참고하세요.
