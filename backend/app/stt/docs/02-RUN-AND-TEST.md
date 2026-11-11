# 설치와 테스트 실행

모든 명령은 Windows PowerShell 기준입니다. 클라우드 인증은 [05](05-CLOUD-STREAMING.md), 프론트 연결은 [03](03-FRONTEND-INTEGRATION.md)에 분리했습니다.

## 1 최초 설치

```powershell
cd "C:\Users\goaal\Documents\박현민\임시공간\SpeakSpot-main\SpeakSpot-main"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/app/stt/requirements/requirements-stt-dev.txt
```

이미 `.venv`가 있으면 생성 명령은 생략합니다. 활성화 없이 Python 경로를 직접 사용합니다.
Google/CLOVA를 사용할 때는 **이 터미널에서 05의 인증 설정을 먼저 수행**합니다.

## 2 서버 실행

```powershell
cd "C:\Users\goaal\Documents\박현민\임시공간\SpeakSpot-main\SpeakSpot-main"
$env:HF_HOME = "$PWD\backend\app\stt\.models"
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.api.stt_app:app --host 127.0.0.1 --port 8000 --ws-max-size 32768 --ws-max-queue 32
```

접속: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).
서버 종료는 해당 터미널에서 Ctrl+C입니다. 한 서버 프로세스만 사용합니다.
`app.main:app`은 기존 팀 앱이므로 STT 테스트에서는 **`app.api.stt_app:app`**을 사용합니다.

8000 포트가 사용 중이면 기존 서버만 종료하거나 `--port 8001`로 실행하세요. 이때 브라우저도 **http://127.0.0.1:8001/docs**로 열어야 합니다. 환경변수를 바꾼 뒤에는 서버를 재시작해야 합니다.

## 3 /docs에서 마이크 테스트

### 시작

**실시간 STT — PC 마이크 → 초록색 POST /api/stt/live/start → Try it out**을 누릅니다.
`No parameters`는 오류가 아닙니다. 그 아래 **Request body**를 수정합니다.
파란색 `/devices`는 마이크 목록이며 시작 API가 아닙니다.

Google:

```json
{"provider":"google","model":null,"language":"ko","duration_seconds":30,"input_device":null}
```

CLOVA:

```json
{"provider":"clova","model":null,"language":"ko","duration_seconds":30,"input_device":null}
```

faster-whisper:

```json
{"provider":"faster-whisper","model":"tiny","language":"ko","chunk_seconds":2,"duration_seconds":30,"input_device":null}
```

**Execute**를 누릅니다. 202는 시작 요청을 받았다는 뜻이며 인증/인식 성공을 의미하지는 않습니다.
응답의 `session_id`는 이후 조회·종료 시 사용하면 테스트를 구분할 수 있습니다.

### 결과 조회

**GET /api/stt/live/results → Try it out → Execute**.
처음에는 `after_chunk=0`, `session_id`는 비워도 됩니다. 응답은 **Server response → Response body**에 있습니다.

| state | 의미 |
|---|---|
| starting | 모델 또는 클라우드 연결 준비 중 |
| recording | 마이크 전송 중. 이때 말하기 |
| stopping | 마이크 종료 및 남은 결과 처리 중 |
| stopped | 정상 종료. 다시 하려면 새 start |
| error | 실패. error 필드 확인 |

Swagger는 자동 갱신되지 않습니다. **GET의 Execute를 다시 눌러 최신 결과를 확인**합니다.
문장은 `results[].segments[].text`, 확정 여부는 `is_final`입니다.
Google의 같은 `segment_id` 중간 결과는 기존 문장을 교체하며, 확정 결과만 탐색에 사용합니다.
Google에서는 recording 이후 비동기 인증 오류가 도착할 수도 있으므로 결과도 확인하세요.

### 종료

**POST /api/stt/live/stop → Try it out → Execute**. 기본 30초 후에는 자동 종료합니다.
종료 후에도 GET으로 마지막 결과를 확인합니다. 클라우드 최종 응답은 최대 15초 기다립니다.

### 요청 옵션

| 필드 | 설명 |
|---|---|
| provider | faster-whisper / google / clova |
| model | null이면 tiny / default / domain-default 자동 선택. Google·CLOVA에 tiny를 넣으면 오류 |
| language | ko / en / ja |
| chunk_seconds | Whisper 청크 길이 0.5~10초. Google·CLOVA는 사용하지 않음 |
| duration_seconds | 기본 30초. Google 최대 240초, 나머지 최대 1800초 |
| input_device | null은 Windows 기본 마이크. `/devices`에서 확인한 id로 지정 가능 |

### 결과 필드

| 필드 | 설명 |
|---|---|
| transport | Whisper는 chunked-local, Google/CLOVA는 native-grpc |
| partial | Google의 최근 미확정 문장. 없으면 null |
| last_chunk_id | 마지막 응답 묶음 번호. 다음 조회의 after_chunk에 사용 |
| history_truncated | 최근 150개보다 오래된 결과가 제거됐으면 true |
| summary | 누적 음성 길이 등. 클라우드 순수 처리시간/RTF는 알 수 없어 null |
| metrics.result_delay_ms | 클라우드 결과 끝 시각에서 수신까지의 근사 지연. 네트워크·발화 확정 대기 포함 |

## 4 파일 업로드 테스트

**POST /api/stt/transcribe → Try it out**에서 file, provider, model을 선택합니다.
Whisper는 tiny/base/small, Google은 default, CLOVA는 domain-default입니다.
55초 이하·10MB 이하의 해독 가능한 WAV/WebM/MP3/M4A 등을 사용합니다.
파일 업로드는 녹음 후 처리이며, Google/CLOVA도 기존 REST 인증을 사용합니다. gRPC 실시간 비교는 위 마이크 API에서 합니다.

## 5 터미널 도구

아래는 모두 **backend 폴더**에서 실행합니다. `app/stt/samples/lecture.wav`는 본인이 준비할 예시 경로입니다.
도구 입력 WAV는 16kHz / mono / PCM16이어야 합니다.

```powershell
# 서버 없이 PC 마이크 → 터미널
..\.venv\Scripts\python.exe -m app.stt.live --model tiny

# 파일 하나로 STTService 확인
..\.venv\Scripts\python.exe -m app.stt.tools.check_service app/stt/samples/lecture.wav

# 서버를 켜둔 상태에서 WAV를 실제 시간 간격으로 WebSocket 전송
..\.venv\Scripts\python.exe -m app.stt.tools.stream_client app/stt/samples/lecture.wav

# 선택 학습 예제
..\.venv\Scripts\python.exe -m app.stt.examples.first_stt app/stt/samples/lecture.wav

# 파일 처리속도와 정답 기반 문자 오류율 평가
..\.venv\Scripts\python.exe -m app.stt.tools.evaluate_stt app/stt/samples/lecture.wav --reference app/stt/samples/lecture.txt --model tiny --output app/stt/experiments/tiny.json

# 자동 테스트
..\.venv\Scripts\python.exe -m unittest discover -s app/stt/tests -p "test_*.py" -v
```

평가의 --reference는 선택입니다. 없으면 CER는 null입니다. 기존 결과 파일은 덮어쓰지 않으므로 새 파일명을 사용합니다.
터미널 마이크와 API 마이크를 동시에 실행하지 마세요. 서로 다른 프로세스는 잠금을 공유하지 않습니다.

## 6 자주 막히는 부분

| 증상 | 확인할 내용 |
|---|---|
| Request body가 안 보임 | `/live/start`를 펼쳤는지, Try it out을 눌렀는지 확인 |
| state가 안 보임 | `/results` Execute 후 Server response의 Response body 확인 |
| STT API가 없음 | app.api.stt_app:app 실행 여부와 브라우저 포트 확인 |
| 409 또는 다른 작업 진행 중 | 기존 작업 종료 후 다시 시작 |
| 422 | provider/model 조합, Google 240초 제한, 필드 오타 확인 |
| 마이크 오류 | Windows 권한, 입력장치 id, 16kHz mono 지원 확인 |
| UNAUTHENTICATED | [05 인증 오류 해결](05-CLOUD-STREAMING.md) 참고 |
| 이전 결과만 보임 | GET Execute 재실행, 올바른 서버 포트/session_id 확인 |
