# 프론트엔드와 STT 통신

## 1 현재 구현과 앞으로 연결할 부분

`/docs`의 `/live/start`는 **서버 PC의 마이크**를 엽니다. 실제 서비스에서는 사용자 브라우저가 마이크 음성을 백엔드에 보내야 합니다.

| 경로 | 현재 지원 | 앞으로 할 일 |
|---|---|---|
| `/api/stt/live/*` | 서버 PC에서 Whisper·Google·CLOVA 비교 | 개발용으로 유지. 사용자 마이크 API로 사용하지 않음 |
| `WS /api/stt/stream` | 클라이언트 PCM → faster-whisper 결과 | 프론트 캡처·리샘플링 연결 |
| 같은 WebSocket의 Google·CLOVA | **미연결** | PCM 수신 큐를 cloud_stream 어댑터 입력으로 연결하고 응답 이벤트 송신 구현 |
| 팀 `/api/v1/sessions/{session_id}/stream` | 명세상의 최종 경로 | 인증·실제 세션·문서·탐색 연결 |

`?provider=google`을 현재 WebSocket URL에 붙여도 클라우드로 전환되지 않습니다. 현재 라우터에는 제공자 선택 처리가 없습니다.

## 2 전체 흐름과 역할

```text
브라우저 마이크 → PCM 변환 → WebSocket → STT → stt.result
                                               ↓
                                    확정 문장 → 탐색 → highlight.update
```

| 담당 | 해야 할 일 |
|---|---|
| 프론트엔드 | 마이크 권한·캡처·리샘플링, WebSocket 송수신, 자막/PDF 하이라이트 표시 |
| STT | PCM 인식, 제공자 연결, STTSegment 반환 |
| API·통합 | 인증, 세션/문서 연결, 작업 자원 관리, STT→탐색 호출, 이벤트 전송 |
| 탐색 | 확정 문장으로 문서 위치 검색 |

## 3 로컬 프론트 연결 설정

백엔드는 02 문서대로 실행합니다. 서버 실행 전 같은 PowerShell에서:

```powershell
$env:STT_ALLOWED_ORIGINS = 'http://localhost:5173,http://127.0.0.1:5173'
```

실제 Vite 포트가 다르면 그 Origin을 명시합니다. 환경변수 변경 후 서버를 재시작합니다.
현재 WebSocket은 localhost 전용이며 이 설정만으로 원격 배포용 인증이 생기는 것은 아닙니다.

프론트 담당자가 `vite.config.ts`의 기존 `/api` 프록시에 `ws: true`를 추가합니다.

```ts
server: {
  proxy: {
    '/api': {
      target: 'http://127.0.0.1:8000',
      changeOrigin: true,
      ws: true,
    },
  },
}
```

백엔드가 8001이면 target도 8001로 바꿉니다. 브라우저는 자신의 Vite 주소를 통해 연결합니다.
changeOrigin은 Host를 바꾸지만 브라우저 Origin을 지우지 않으므로 위 허용 목록도 필요합니다.

## 4 실제 메시지 순서

연결 주소: `ws://localhost:5173/api/stt/stream?model=tiny&chunk_seconds=2` (Vite 프록시 기준).

1. 연결 후 클라이언트가 설정 JSON을 보냅니다.

```json
{"type":"session.start","payload":{"encoding":"pcm_s16le","sample_rate":16000,"channels":1}}
```

2. 서버 준비 응답을 받은 뒤 캡처/전송을 시작합니다.

```json
{"type":"session.ready","payload":{"session_id":"생성된-ID"}}
```

3. 클라이언트가 **바이너리 raw PCM**을 보내고 서버는 결과 JSON을 반복 반환합니다.

```json
{"type":"stt.result","payload":{"segment_id":"seg_000001","text":"안녕하세요.","start_ms":0,"end_ms":1800,"is_final":true}}
```

4. 종료 버튼을 누르면 캡처를 멈추고 남은 PCM까지 보낸 다음 종료 메시지를 보냅니다.

```json
{"type":"session.stop","payload":{}}
```

즉시 socket.close()를 호출하지 말고 마지막 결과와 서버의 정상 종료(code 1000)를 기다립니다.
오류 형식은 `{"type":"error","payload":{"code":"…","message":"…"}}`입니다.

## 5 프론트 담당자의 오디오 처리

- getUserMedia로 사용자 마이크 권한을 받습니다. 배포 환경은 HTTPS/WSS를 사용합니다.
- AudioWorklet 등으로 입력을 받고 실제 AudioContext.sampleRate를 확인합니다.
- 실제 44.1/48kHz 입력을 상태를 유지하는 리샘플러로 16kHz로 변환합니다. 이름만 16kHz로 바꾸면 안 됩니다.
- 다채널은 mono로 합치고 float32를 클리핑해 signed int16 little-endian으로 변환합니다.
- 100ms = 1600샘플 = 3200바이트씩 전송하는 것을 권장합니다. 무음도 보내야 오디오 시각이 유지됩니다.
- WebM/Opus 조각이나 WAV 헤더를 그대로 보내지 않습니다. 현재 WebSocket은 raw PCM만 받습니다.
- 입력 프레임 최대 32000바이트, 서버 대기 음성 최대 10초, 메시지 대기 15초, 테스트 최대 30분입니다.
- 중지·오류·컴포넌트 해제 시 마이크 트랙, AudioContext, 소켓을 정리합니다.

## 6 통신 코드 예시

아래는 **송수신 뼈대**입니다. 실제 마이크 캡처·리샘플링·화면 코드는 포함하지 않았습니다.

```ts
const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
const ws = new WebSocket(`${protocol}://${location.host}/api/stt/stream?model=tiny`);
let ready = false;
const segments = new Map();
const searched = new Set();

ws.onopen = () => ws.send(JSON.stringify({
  type: 'session.start',
  payload: { encoding: 'pcm_s16le', sample_rate: 16000, channels: 1 },
}));

ws.onmessage = ({ data }) => {
  const event = JSON.parse(data);
  if (event.type === 'session.ready') {
    ready = true; // 이때 마이크 캡처 시작
  } else if (event.type === 'stt.result') {
    const segment = event.payload;
    segments.set(segment.segment_id, segment); // 같은 ID는 교체
    if (segment.is_final && !searched.has(segment.segment_id)) {
      searched.add(segment.segment_id);
      // 팀의 확정 문장 탐색 흐름에 전달
    }
    // Map의 현재 값을 UI에 반영
  } else if (event.type === 'error') {
    ready = false;
    // 오류 표시 + 마이크/AudioContext 정리
  }
};
ws.onclose = ws.onerror = () => {
  ready = false;
  // 마이크 트랙/AudioContext 정리, 종료 또는 연결 오류 표시
};

// 이미 16kHz mono로 변환된 int16 샘플만 전달
function sendPcm16(samples: Int16Array) {
  if (!ready || ws.readyState !== WebSocket.OPEN) return;
  if (samples.length === 0 || samples.length > 16000) throw new Error('잘못된 프레임 크기');
  if (ws.bufferedAmount > 320000) throw new Error('전송 지연: 캡처 중단 후 재시작 필요');
  const buffer = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buffer);
  samples.forEach((sample, i) => view.setInt16(i * 2, sample, true));
  ws.send(buffer);
}

function finishAfterFlushingAudio() {
  if (ready && ws.readyState === WebSocket.OPEN) {
    ready = false;
    ws.send(JSON.stringify({ type: 'session.stop', payload: {} }));
  }
}
```

실제 구현은 예외를 잡아 캡처를 정리해야 합니다. 새 세션에서는 Map/Set도 초기화합니다.
현재 Whisper WebSocket은 확정 결과만 반환하지만, 향후 Google 중간 결과를 연결할 때도 같은 ID를 교체하도록 설계하면 됩니다.

파일 업로드만 연결할 때는 FormData에 `file`, `provider`, `model`을 넣어 POST `/api/stt/transcribe`로 전송합니다. 브라우저가 multipart 경계를 만들도록 Content-Type을 직접 지정하지 않습니다. 이 경로는 실시간 캡처 스트림이 아닙니다.

## 7 Google·CLOVA와 팀 서비스 연결 시 남은 작업

`cloud_stream.py`의 어댑터는 PCM 반복자와 결과 콜백을 받습니다. 지금은 cloud_microphone.py가 서버 마이크를 그 반복자에 넣습니다.
브라우저용으로는 API 담당자와 함께 **WebSocket 수신 PCM 큐 → 클라우드 어댑터 → 결과 송신 큐**를 연결해야 합니다.
기존 `serve_stt_socket`은 STTService 기반 청크 처리이므로 네이티브 클라우드 어댑터를 그대로 끼워 넣을 수 없습니다.
수신/송신 동시 처리, 중단/역압/자원 해제, Google 240초 제한과 새 세션 정책도 함께 처리해야 합니다.

팀의 최종 세션 라우터에서는 인증과 실제 session_id 확인 후 모델을 선택하고, 확정 결과만 탐색으로 전달합니다.
현재 테스트용 UUID는 팀 문서 세션을 생성한 것이 아닙니다. 실습용 main.py를 덮어쓰지 말고 필요한 라우터/서비스만 통합하세요.
단일 프로세스 작업 잠금, 다중 사용자 큐, 재연결 복구, 인증과 배포는 별도 설계가 필요합니다.

참고: [AudioWorklet](https://developer.mozilla.org/en-US/docs/Web/API/AudioWorklet), [Vite 프록시](https://vite.dev/config/server-options#server-proxy), [FastAPI WebSocket](https://fastapi.tiangolo.com/advanced/websockets/).
