# Google와 CLOVA 인증 및 오류 해결

이 문서는 클라우드 인증만 다룹니다. Request body와 실행 순서는 [02](02-RUN-AND-TEST.md)에 있습니다.
사용할 제공자만 설정하면 됩니다. 환경변수는 **서버를 실행하는 같은 터미널**에 적용합니다.
클라우드를 선택하면 음성이 해당 서비스로 전송되고 사용료가 발생할 수 있습니다.

## 1 Google 네이티브 스트리밍

Google Cloud CLI가 설치된 PowerShell에서:

```powershell
gcloud auth application-default login
gcloud auth application-default set-quota-project project-f43e74b2-7c4e-4962-b76
$env:GOOGLE_CLOUD_PROJECT = 'project-f43e74b2-7c4e-4962-b76'
```

브라우저 로그인은 본인이 완료합니다. 프로젝트 ID는 현재 사용자가 제공한 프로젝트이며 다른 프로젝트를 사용하면 바꿉니다.
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
기존 서버를 Ctrl+C로 종료하고, 키를 설정한 터미널에서 02의 서버 실행 명령을 사용하세요.
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

인증키를 채팅·Request body·소스·Git에 넣지 않습니다. `.env` 자동 로딩은 구현하지 않았습니다.

## 5 REST 파일 인식과 인증 차이

실제 gRPC는 `/live/start`와 `WS /api/stt/stream?provider=google` 또는 `provider=clova`에서 사용합니다. 기존 파일 업로드/마이크 CLI/평가 도구의 클라우드 경로는 REST 방식입니다.

| 경로 | 필요한 인증 |
|---|---|
| Google gRPC | ADC 또는 GOOGLE_ACCESS_TOKEN; GOOGLE_CLOUD_PROJECT로 할당량 프로젝트 지정 |
| Google REST | GOOGLE_ACCESS_TOKEN, GOOGLE_CLOUD_PROJECT |
| CLOVA gRPC | CLOVA_SPEECH_SECRET |
| CLOVA REST | CLOVA_SPEECH_SECRET + CLOVA_SPEECH_INVOKE_URL; REST를 지원하는 해당 도메인 |

스트리밍 도메인 가입과 REST 도메인 사용 권한을 동일하다고 가정하지 않습니다.
서버 마이크 비교에는 02 문서의 `/live/start`, 클라이언트 음성 전송 비교에는 02의 WebSocket 도구를 사용하세요.

공식 참고:
[Google ADC](https://docs.cloud.google.com/speech-to-text/docs/v1/authentication),
[Google v1 스트리밍](https://docs.cloud.google.com/speech-to-text/docs/v1/transcribe-streaming-audio),
[CLOVA 스트리밍 빌더](https://guide.ncloud-docs.com/docs/clovaspeech-builder-streaming),
[CLOVA gRPC](https://api.ncloud-docs.com/docs/en/ai-application-service-clovaspeech-grpc).
