# PDF 이미지 텍스트 OCR

기존 PDF 텍스트를 먼저 추출하고, 실제 표시되는 이미지가 있는 페이지에만
PyMuPDF의 로컬 Tesseract OCR을 적용합니다. 기본 언어는 한국어+영어입니다.
텍스트와 이미지가 섞인 페이지에서는 기존 텍스트를 보존하며 이미지 글자를 추가합니다.
인식한 문장은 기존 블록·검색·하이라이트 흐름을 그대로 사용합니다.
문서나 이미지를 외부 API로 전송하지 않습니다.

## 준비

백엔드 의존성이 설치된 환경에서 `backend` 폴더를 기준으로 실행합니다.

```sh
python scripts/setup_ocr.py
```

이 명령은 Tesseract 공식 `tessdata_fast` 저장소의 `4.1.0` 버전에서 `kor.traineddata`,
`eng.traineddata`를 받아 `backend/.cache/tessdata`에 저장합니다. 모델은 Git에 포함하지
않으며, 파서가 이 폴더를 자동으로 찾습니다. 준비 후 백엔드를 다시 시작하세요.
PyMuPDF에 포함된 OCR 기능을 사용하므로 별도의 Python OCR 패키지는 필요 없습니다.

## 설정

백엔드를 실행하는 프로세스의 환경변수로 지정합니다.

| 환경변수 | 기본값 | 용도 |
| --- | --- | --- |
| `SPEAKSPOT_OCR_ENABLED` | `true` | `false`, `0`, `off`로 OCR 비활성화 |
| `SPEAKSPOT_OCR_LANGUAGE` | `kor+eng` | Tesseract 언어 조합 |
| `SPEAKSPOT_OCR_DPI` | `200` | 인식 해상도, 72~600 |
| `SPEAKSPOT_OCR_TESSDATA` | 로컬 캐시 자동 탐색 | 언어 데이터 폴더 직접 지정 |

데이터 경로는 명시 설정 → `TESSDATA_PREFIX` → 로컬 캐시 → PyMuPDF의 시스템 탐색
순서입니다. 다른 언어를 지정하면 해당 `.traineddata`도 준비해야 합니다.

OCR을 사용할 수 없어도 기존 텍스트가 있는 페이지는 원래 텍스트로 처리하며 서버
로그에 경고를 남깁니다. 이미지뿐인 문서는 설치 안내 오류를 반환합니다.
환경 준비 전에 올린 PDF는 다시 업로드해야 OCR이 적용됩니다.

OCR은 이미지 속 **글자**를 검색 가능하게 합니다. 사진·도형 자체의 의미 설명,
복잡한 표 구조나 수식 복원까지 제공하지는 않습니다. 작은 글자·흐린 스캔은 인식이
부정확할 수 있으며, OCR 페이지는 일반 텍스트 페이지보다 처리 시간이 더 걸립니다.

## 확인

```sh
python -m pytest tests/test_documents_ocr.py -q
```

일반 회귀 테스트는 언어 데이터 없이 실행할 수 있습니다. 실제 엔진 테스트는
한국어·영어 언어 데이터가 있으면 실행하고, 없으면 건너뜁니다.
