# 음성 인식과 STT 도구

의존성은 `backend/requirements.txt` 한 파일로 설치합니다. 통합 앱의 기본 음성 입력은 브라우저 STT이며, 개발자 메뉴에서 서버 STT를 선택할 수 있습니다. 서버 STT 기본 모델은 Faster-Whisper tiny입니다.

## 실행과 경로

아래 명령은 `backend/` 기준입니다. 통합 앱은 PDF와 검색까지 연결하고, 단독 앱은 STT 제공자·서버 마이크·음성 파일을 비교합니다.

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
# STT 단독 앱은 별도 포트에서 필요할 때만 실행
.\.venv\Scripts\python.exe -m uvicorn app.api.stt_app:app --port 8001
```

| 경로 | 입력과 결과 |
|---|---|
| `WS /api/v1/sessions/{id}/stream` | 통합 앱: 브라우저 PCM → STT → 검색 → 하이라이트 |
| `WS /api/stt/stream` | 단독 앱: 클라이언트 PCM → Whisper/Google/CLOVA 결과 |
| `/api/stt/live/start`, `/results`, `/stop`, `/devices` | 단독 앱: 서버 PC 마이크의 인식·상태·장치 |
| `/api/stt/transcribe` | 단독 앱: 55초 이하·10 MB 이하 WAV/WebM/MP3/M4A 파일 |

WebSocket은 `session.start → session.ready → PCM 프레임 → stt.result → session.stop` 순서입니다. PCM은 **16kHz / mono / signed 16-bit little endian**입니다. 통합 앱은 확정 결과 뒤 `highlight.update`를 추가로 받습니다. 프론트의 `useServerStt.ts`와 `pcmWorklet.ts`에 캡처·리샘플링·송수신이 구현돼 있습니다.

단독 `/docs`에서 `/live/start`를 펼치고 Try it out으로 시작 요청을 보냅니다. 이 API는 **서버 PC의 마이크**를 사용합니다.

```json
{"provider":"faster-whisper","model":"tiny","language":"ko","chunk_seconds":2,"duration_seconds":30}
```

Google/CLOVA는 `provider`를 바꾸고 `model`을 `null`로 지정합니다. Google은 최대 240초, 그 외 제공자는 최대 1800초입니다. 파일 인식의 모델은 Whisper `tiny/base/small`, Google `default`, CLOVA `domain-default`입니다. 서버 STT 모델은 프로세스 내에서 한 번에 한 작업이 사용합니다. 서로 다른 서버 프로세스는 잠금을 공유하지 않습니다.

## 파일과 CLI

| 파일 | 역할 |
|---|---|
| `config.py`, `audio.py`, `service.py` | 제공자 설정, PCM/WAV 검증, 세그먼트/측정값 |
| `providers.py`, `runtime.py` | Whisper/REST 제공자, 모델 캐시와 잠금 |
| `streaming.py`, `live.py` | PCM 청크 처리와 터미널 마이크 |
| `cloud_stream.py`, `cloud_socket.py`, `cloud_microphone.py` | 네이티브 gRPC와 WebSocket/서버 마이크 연결 |
| `proto/` | CLOVA NEST 메시지와 자동 생성 코드 |
| `tools/`, `examples/`, `tests/` | 연결·평가 도구, 예제, 회귀 테스트 |

`samples/lecture.wav`와 정답 파일은 직접 준비합니다. 아래 명령 역시 `backend/` 기준입니다.

```powershell
.\.venv\Scripts\python.exe -m app.stt.live --model tiny
.\.venv\Scripts\python.exe -m app.stt.tools.check_service app/stt/samples/lecture.wav
.\.venv\Scripts\python.exe -m app.stt.tools.stream_client app/stt/samples/lecture.wav --url "ws://127.0.0.1:8001/api/stt/stream"
.\.venv\Scripts\python.exe -m app.stt.examples.first_stt app/stt/samples/lecture.wav
.\.venv\Scripts\python.exe -m app.stt.tools.evaluate_stt app/stt/samples/lecture.wav --reference app/stt/samples/lecture.txt --model tiny --output evaluation.json
```

평가의 `--reference`는 선택입니다. 없으면 CER는 null입니다. 기존 출력 파일은 덮어쓰지 않으므로 새 이름을 사용합니다. 클라우드 순수 처리시간/RTF는 측정하지 않으며, 결과 지연에는 네트워크와 발화 확정 대기가 포함됩니다. 모의 테스트는 실제 클라우드 계정의 인증·요금·인식 품질을 보증하지 않습니다.

## 클라우드 인증

Google·CLOVA의 실시간 PCM 경로는 gRPC, 파일 인식과 일부 터미널 도구는 REST를 사용합니다. 사용할 제공자만 설정합니다. 클라우드 제공자를 선택하면 음성이 해당 서비스로 전송되고 사용료가 발생할 수 있습니다.


## 1 Google 네이티브 스트리밍

Google Cloud CLI가 설치된 PowerShell에서:

```powershell
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
$env:GOOGLE_CLOUD_PROJECT = 'YOUR_PROJECT_ID'
```

브라우저 로그인은 본인이 완료합니다. YOUR_PROJECT_ID를 사용할 Google 프로젝트 ID로 바꿉니다.
Speech-to-Text API 활성화, 결제, 로그인 계정의 호출 및 할당량 프로젝트 사용 권한이 필요합니다.
이미 서비스 계정 인증 파일을 사용하는 경우 GOOGLE_APPLICATION_CREDENTIALS에 해당 파일 경로를 지정할 수도 있습니다.

이 구현은 Google v1 `StreamingRecognize`, model=default를 사용합니다. v2 recognizer나 Chirp 구성은 아닙니다.
프로젝트 API 콘솔 링크는 인증키가 아닙니다.

### 기존 토큰이 남아 있는 경우

코드는 GOOGLE_ACCESS_TOKEN이 있으면 ADC보다 우선 사용합니다. 만료된 토큰 때문에 실패하면 제거하고 ADC를 사용하세요.

```powershell
Remove-Item Env:GOOGLE_ACCESS_TOKEN -ErrorAction SilentlyContinue
```

ADC는 Google 라이브러리가 인증을 관리하지만, 직접 넣은 액세스 토큰은 코드가 자동 갱신하지 않습니다.

## 2 CLOVA 네이티브 스트리밍

콘솔에서 **CLOVA Speech → Domain → 사용할 스트리밍 Basic 도메인의 빌더 실행 → 설정 → 연동 정보 → Secret Key 복사**로 확인합니다.
기존 키가 있으면 불필요하게 재생성하지 않습니다. 계정 Access Key/Secret Access Key나 네이버 로그인 비밀번호와 다릅니다.

PowerShell에서:

```powershell
$env:CLOVA_SPEECH_SECRET = [System.Net.NetworkCredential]::new('', (Read-Host 'CLOVA Secret Key' -AsSecureString)).Password
```

`CLOVA Secret Key:`가 나오면 **복사한 Secret Key만 붙여넣고 Enter**를 누릅니다.
명령의 `.Password`는 입력값을 꺼내는 코드이며, 비밀번호를 추가 입력하라는 뜻이 아닙니다.
키에 `Bearer `나 따옴표를 붙이지 않습니다. 코드는 인증 헤더를 직접 구성합니다.

스트리밍은 `clovaspeech-gw.ncloud.com:50051`에 TLS gRPC로 연결합니다. REST Invoke URL은 이 스트리밍 경로에 쓰이지 않습니다.
Basic 도메인 권한과 외부 50051 포트 연결이 필요합니다.

## 3 설정 후 서버를 재시작하는 이유

이미 켜진 서버는 다른 터미널에서 새로 설정한 환경변수를 전달받지 못합니다.
기존 서버를 Ctrl+C로 종료하고, 키를 설정한 터미널에서 위의 서버 실행 명령을 사용하세요.
서버가 여러 개라 헷갈리면 새 서버를 `--port 8001`로 켜고 **http://127.0.0.1:8001/docs**에서 테스트하면 구분할 수 있습니다.
8000과 8001을 혼용하지 마세요. 마이크 테스트도 한 번에 하나만 실행합니다.

## 4 오류별 확인 순서

| 오류/증상 | 확인할 내용 |
|---|---|
| CLOVA UNAUTHENTICATED | 해당 도메인 Secret Key인지 → Bearer/따옴표 없이 입력했는지 → 같은 터미널에서 서버 재시작했는지 → 올바른 포트인지 |
| 키가 맞는데도 인증 실패 | 키 재생성보다 서버 환경변수 반영 여부부터 확인. 이후 도메인 gRPC 권한/콘솔 상태 확인 |
| Google 인증 준비 오류 | ADC 로그인 또는 인증 파일 설정, 만료된 GOOGLE_ACCESS_TOKEN 제거 |
| PERMISSION_DENIED | 프로젝트/도메인 접근 권한, API 활성화, 결제·할당량 프로젝트 권한 확인 |
| RESOURCE_EXHAUSTED / No more slot | 기존 스트림 종료 후 재시도, 서비스 할당량/슬롯 확인 |
| UNAVAILABLE / TIMEOUT | 네트워크·방화벽·외부 연결 상태 확인. CLOVA는 50051 포트 사용 |
| 422 | provider/model 조합 또는 Google duration_seconds 240초 초과 확인 |
| audio_ms=0, state=error | 음성 전송 전에 준비·인증 단계에서 실패했을 가능성. error와 설정 확인 |

인증 설정은 서버를 실행할 터미널에 적용합니다. `.env`를 쓰려면 Uvicorn의 `--env-file .env` 옵션으로 명시적으로 로드합니다.

## 5 REST 파일 인식과 인증 차이

실제 gRPC는 `/live/start`와 `WS /api/stt/stream?provider=google` 또는 `provider=clova`에서 사용합니다. 기존 파일 업로드/마이크 CLI/평가 도구의 클라우드 경로는 REST 방식입니다.

| 경로 | 필요한 인증 |
|---|---|
| Google gRPC | ADC 또는 GOOGLE_ACCESS_TOKEN; GOOGLE_CLOUD_PROJECT로 할당량 프로젝트 지정 |
| Google REST | GOOGLE_ACCESS_TOKEN, GOOGLE_CLOUD_PROJECT |
| CLOVA gRPC | CLOVA_SPEECH_SECRET |
| CLOVA REST | CLOVA_SPEECH_SECRET + CLOVA_SPEECH_INVOKE_URL; REST를 지원하는 해당 도메인 |

스트리밍 도메인 가입과 REST 도메인 사용 권한을 동일하다고 가정하지 않습니다.
서버 마이크 비교에는 위의 `/live/start`, 클라이언트 음성 전송 비교에는 02의 WebSocket 도구를 사용하세요.

공식 참고:
[Google ADC](https://docs.cloud.google.com/speech-to-text/docs/v1/authentication),
[Google v1 스트리밍](https://docs.cloud.google.com/speech-to-text/docs/v1/transcribe-streaming-audio),
[CLOVA 스트리밍 빌더](https://guide.ncloud-docs.com/docs/clovaspeech-builder-streaming),
[CLOVA gRPC](https://api.ncloud-docs.com/docs/en/ai-application-service-clovaspeech-grpc).
