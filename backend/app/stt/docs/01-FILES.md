# 파일별 명세와 코드 흐름

아래 경로는 `backend/app` 기준입니다. API는 요청/응답, schemas는 데이터 형식, stt는 음성 처리와 실험 도구를 담당합니다.

## 1 구조

```text
api/                 stt_app.py, stt.py, stt_live.py, stt_stream.py
schemas/             stt_models.py, models.py
stt/
  config.py          설정
  audio.py           PCM와 WAV 처리
  providers.py       로컬 모델과 REST 어댑터
  service.py         공통 결과·세션 시각
  runtime.py         모델 캐시·실행 잠금
  live.py            터미널 마이크와 공용 대기열
  streaming.py       PCM 청크 처리
  cloud_stream.py    Google·CLOVA gRPC
  cloud_microphone.py  마이크와 gRPC 연결
  cloud_socket.py    WebSocket과 gRPC 사이의 대기열·작업 스레드
  proto/             CLOVA 메시지 정의
  tools/             파일 전송·평가 도구
  examples/          학습 예제
  tests/             자동 테스트
  requirements/      설치 목록
  docs/              설명서
```

## 2 API와 데이터 형식

| 파일 | 역할 | 수정할 상황 |
|---|---|---|
| `api/stt_app.py` | STT 전용 FastAPI 앱, 라우터 등록, 종료 처리 | API 등록이나 앱 실행 구성 변경 |
| `api/stt.py` | 파일 업로드 → PCM 변환 → 인식 결과 반환 | 파일 크기/길이 제한, 업로드 지원 변경 |
| `api/stt_live.py` | /docs에서 마이크 시작·상태/결과 조회·종료. 제공자에 따라 로컬/클라우드 처리 분기 | 실험 API, 결과 보관, 상태 표시 변경 |
| `api/stt_stream.py` | WebSocket PCM 수신, 입력 검증, 대기열 제한, 결과 이벤트 송신 | 프론트엔드/팀 세션 연결 |
| `schemas/stt_models.py` | `STTSegment`와 `StartRequest` 정의, 제공자별 설정 검증 | 외부 데이터 형식 또는 요청 옵션 변경 |
| `schemas/models.py` | 기존 PDF·검색 형식 유지. STTSegment는 stt_models에서 재노출 | 팀 공통 형식 변경 시 협의 |

`STTSegment`의 실제 정의는 `stt_models.py` 한 곳입니다. 기존 팀 코드의 `from app.schemas.models import STTSegment`도 호환됩니다. 기존 `mock.py`, `main.py`를 STT 단독 파일로 덮어쓰지 않습니다.

## 3 STT 처리 파일

| 파일 | 핵심 책임 |
|---|---|
| `stt/__init__.py` | STTConfig, STTService를 외부에 노출 |
| `stt/config.py` | 제공자·모델·언어·장치·정밀도·beam_size·VAD·제한시간 설정 |
| `stt/audio.py` | 16kHz mono PCM16 검증, 길이 계산, WAV 읽기/생성 |
| `stt/providers.py` | faster-whisper와 Google/CLOVA **REST** 어댑터. 청크 기준 Utterance 반환. Whisper 시각을 입력 길이 안으로 보정 |
| `stt/service.py` | Utterance → STTSegment, 세션 시각/ID 누적, 처리시간·RTF 계산. 세션마다 인스턴스 하나 |
| `stt/runtime.py` | 서버 프로세스의 모델 캐시와 작업 잠금. 업로드/마이크/WebSocket 작업 충돌 방지 |
| `stt/live.py` | `PCMChunks`, `MicrophoneBuffer`, 마지막 음성 처리, 마이크 CLI. API에서도 공용 부품 재사용 |
| `stt/streaming.py` | `StreamingSTT.feed(pcm)`로 청크 처리, `finish()`로 마지막 음성 처리. 네트워크와 독립적 |
| `stt/cloud_stream.py` | `GoogleStreaming`, `ClovaStreaming`: 네이티브 gRPC 인증·송수신. Google 중간/확정 결과와 CLOVA 발화 결과를 공통 형식으로 변환 |
| `stt/cloud_socket.py` | WebSocket PCM을 100ms 이하로 나눠 gRPC에 전달. 입력/결과 대기열 제한, 중간·확정 결과, 연결 종료 처리 |
| `stt/cloud_microphone.py` | 100ms 마이크 PCM 전송과 클라우드 결과 수신을 동시에 수행. 자동 종료·수동 종료·마지막 응답 대기 |

`providers.py`와 `cloud_stream.py`는 용도가 다릅니다. 전자는 파일/청크를 인식하는 어댑터, 후자는 연결을 유지하며 음성을 계속 주고받는 어댑터입니다.

## 4 도구와 프로토콜

| 파일 | 역할 |
|---|---|
| `stt/tools/stream_client.py` | WAV 헤더를 제거하고 100ms씩 실제 시간 간격으로 WebSocket에 전송 |
| `stt/tools/evaluate_stt.py` | 같은 WAV 반복 평가, 처리시간/RTF/선택적 CER를 JSON에 기록 |
| `stt/tools/check_service.py` | WAV 한 개로 STTService 반환값 확인 |
| `stt/examples/first_stt.py` | faster-whisper를 직접 호출하는 최초 학습 예제. 서버 의존성 없음 |
| `stt/proto/nest.proto` | CLOVA NEST gRPC 메시지와 서비스 규격 원본 |
| `stt/proto/nest_pb2.py` | proto에서 생성한 Python 메시지 코드. 직접 편집하지 않음 |
| 각 하위 `__init__.py` | Python 패키지 구분과 모듈 import/실행 지원 |
| `stt/.gitignore` | STT 폴더의 모델 캐시·샘플·실험 결과·환경변수·Python 캐시 제외 |

## 5 설치 목록과 테스트

| 파일 | 포함 내용 |
|---|---|
| `requirements/requirements-stt.txt` | 기본 인식 패키지 |
| `requirements/requirements-stt-live.txt` | 기본 패키지 + 마이크 |
| `requirements/requirements-stt-web.txt` | API·마이크·WebSocket·클라우드 gRPC SDK |
| `requirements/requirements-stt-dev.txt` | 웹 실행 패키지 + 테스트 클라이언트 |
| `tests/test_live.py` | 음성 보존, 대기열 초과, 마지막 청크 처리 |
| `tests/test_live_api.py` | 마이크 API 상태, 중복 시작, 결과 보관, 입력 검증 |
| `tests/test_streaming.py` | 모델 캐시, 시각, WebSocket, 중단, 세션/Origin/schema 호환 |
| `tests/test_cloud_socket.py` | 두 클라우드의 WebSocket 요청·중간/최종 결과·마지막 음성·오류·중단·시간 제한 검증 |
| `tests/test_cloud_stream.py` | Google SDK 요청·중간/확정 ID, CLOVA 모의 gRPC, 클라우드 종료·오류 |
| `tests/test_evaluation.py` | 한국어 문자 오류율 계산 |

## 6 코드를 읽는 순서

1. `stt_models.py`에서 입출력 형태를 확인합니다.
2. `config.py → audio.py → providers.py → service.py`로 기본 인식을 이해합니다.
3. 로컬 실시간은 `live.py → streaming.py → stt_stream.py`로 읽습니다.
4. 클라우드 실시간은 `cloud_stream.py → cloud_microphone.py → stt_live.py`로 읽습니다.

흐름 예시:

```text
/docs → stt_live.py → PC 마이크
  ├─ faster-whisper → PCMChunks → STTService → providers.py
  └─ google/clova   → cloud_microphone.py → cloud_stream.py → 외부 gRPC
                    ↓
              결과 보관 → GET /results

프론트 PCM → stt_stream.py
  ├─ Whisper → StreamingSTT → STTService → STTSegment
  └─ Google/CLOVA → CloudSocketBridge → cloud_stream → STTSegment
```
