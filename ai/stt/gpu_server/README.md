# GPU ASR server

Jira: `S15P11A301-267`

Jetson에서 모델 추론을 제거하고, A301에 배정된 L40S의 물리 GPU 3에서 ASR을
서빙하는 독립 FastAPI 서비스다. 운영 후보는 Qwen3-ASR-1.7B이며 같은 HTTP
계약을 유지한 채 `large-v3` 계열 faster-whisper로 교체할 수 있다.

## 안전 경계

- `/v1/asr`는 Bearer 또는 `X-API-Key` 인증 없이는 동작하지 않는다.
- 원음과 전사문은 로그에 남기지 않는다. 업로드는 임시 파일로만 디코딩하고 요청
  종료 시 성공·실패와 무관하게 삭제한다.
- 허용 입력은 WAV/FLAC/OGG, mono/stereo, 8~48 kHz, 기본 30초·16 MiB 이하이다.
- 공유 GPU에서 기본 동시 추론은 1건이다. 대기 한도를 넘으면 HTTP 429와
  `ASR_OVERLOADED`를 반환한다.
- GPU 서버 포트를 인터넷에 직접 공개하지 않는다. 기본 바인딩은
  `127.0.0.1:18100`이며 TLS reverse proxy 또는 SSH tunnel 뒤에서만 사용한다.

## API 계약

```bash
curl -sS http://127.0.0.1:18100/health

curl -sS http://127.0.0.1:18100/v1/asr \
  -H "Authorization: Bearer $ASR_API_KEY" \
  -H "X-Request-ID: encounter-123-count" \
  -F "language=ko" \
  -F "audio=@sample.wav;type=audio/wav"
```

성공 응답:

```json
{
  "api_version": "v1",
  "request_id": "encounter-123-count",
  "text": "저 혼자 있어요",
  "language": "ko",
  "confidence": null,
  "duration_seconds": 1.82,
  "inference_ms": 243.5,
  "backend": "qwen3-asr",
  "model": "Qwen/Qwen3-ASR-1.7B"
}
```

클라이언트가 분기해야 하는 오류 코드는 다음과 같다.

| HTTP | code | 의미 |
|---:|---|---|
| 401 | `UNAUTHORIZED` | 인증 실패 |
| 413 | `AUDIO_TOO_LARGE`, `AUDIO_TOO_LONG` | 요청 제한 초과 |
| 415 | `UNSUPPORTED_AUDIO_TYPE`, `INVALID_AUDIO` | 지원하지 않거나 깨진 오디오 |
| 422 | `UNSUPPORTED_LANGUAGE`, `UNSUPPORTED_SAMPLE_RATE` | 계약 위반 |
| 429 | `ASR_OVERLOADED` | 공유 GPU 동시성 한도 초과, 재시도 가능 |
| 503 | `MODEL_NOT_READY`, `MODEL_INFERENCE_FAILED` | 모델 준비/추론 실패, 재시도 가능 |

오류 본문은 모두 `{"error":{"code", "message", "request_id", "retryable"}}`
형식이다. Jetson은 이 오류를 요구조자의 `NO_RESPONSE`로 바꾸면 안 된다.

## L40S Device3 실행

Qwen 공식 패키지는 별도 Python 환경을 권장한다. GPU 서버 홈에서 다음과 같이
설치한다.

```bash
cd /path/to/S15P11A301/ai/stt
python3.12 -m venv .venv-gpu-asr
source .venv-gpu-asr/bin/activate
python -m pip install -U pip
python -m pip install -r gpu_server/requirements.txt

cp gpu_server/.env.example .env.gpu-asr
set -a
source .env.gpu-asr
set +a
python -m gpu_server
```

GPU 서버의 NVIDIA 570 드라이버는 CUDA 12.8 런타임과 호환된다. 따라서
`requirements.txt`는 PyTorch `2.11.0+cu128`을 고정한다. 이 고정이 없으면 기본
PyPI가 CUDA 13 빌드를 선택해 `torch.cuda.is_available()`이 `False`가 될 수 있다.
설치 직후 다음 명령으로 Device3 접근을 확인한다.

```bash
CUDA_VISIBLE_DEVICES=3 python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

`ASR_CUDA_VISIBLE_DEVICES=3`은 모델 라이브러리 import 전에 적용된다. 따라서
프로세스 내부의 `cuda:0`은 물리 Device3을 가리킨다. 시작 전후로 반드시
다음을 확인한다.

```bash
nvidia-smi -i 3
curl -sS http://127.0.0.1:18100/health
```

현재 서버는 공유 L40S이고 기존 프로세스가 메모리와 연산량을 사용할 수 있다.
OOM 또는 P95 지연 상승 시 동시성을 늘리지 말고, 먼저 기존 프로세스 소유자를
확인한다. 타 팀 프로세스를 종료하지 않는다. 모델 load 실패 시 health는
`degraded`와 `MODEL_LOAD_FAILED`를 반환하므로 Jetson 트래픽을 연결하지 않는다.

Qwen3-ASR 대신 비교용 Whisper를 띄우려면 별도 환경에서 다음을 사용한다.

```bash
python -m pip install -r gpu_server/requirements-whisper.txt
export ASR_BACKEND=whisper
export ASR_MODEL_ID=large-v3
export ASR_DTYPE=float16
python -m gpu_server
```

## 검증

모델 가중치나 GPU 없이 계약 테스트를 실행할 수 있다.

```bash
cd ai/stt
python -m unittest tests.test_gpu_server -v
python -m unittest discover -s tests -v
```

실제 L40S 전사, P50/P95, WER/CER와 핵심 슬롯 평가는
`S15P11A301-269`, 전체 자원·E2E 판정은 기존 `S15P11A301-261`에서 기록한다.
