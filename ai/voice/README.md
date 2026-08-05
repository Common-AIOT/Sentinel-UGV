# 요구조자 음성 파이프라인

요구조자의 응답을 구조화해 관제에 전달하는 음성 서비스다. 현재 운영 경로는 다음 하나다.

```text
VISION Encounter
→ 승인된 사전 녹음 WAV
→ BRIO 100 입력 + Jetson Silero VAD
→ L40S FastAPI Qwen3-ASR-1.7B
→ GMS gpt-5.4-mini 구조화
→ 규칙 기반 위험도
→ 관제 보고 인계
```

설계, 안전 계약, 모델 선정 근거, GPU 서버와 Jetson 실행, 테스트 및 소음 실측은
루트 문서 [docs/08-AI-음성.md](../../docs/08-AI-음성.md)를 단일 기준으로 사용한다.

## 구성

| 경로 | 역할 |
|---|---|
| `sentinel_voice/` | 상태 머신, 오디오, 원격 ASR, GMS, 안전 규칙과 관제 인계 |
| `asr_server/` | L40S에서 Qwen3-ASR을 제공하는 인증 FastAPI 서버 |
| `assets/` | 승인된 사전 녹음 안내 WAV |
| `denoise/` | 관제 청취본 후처리. STT 입력에는 사용하지 않음 |
| `evaluation/` | ASR shadow, Jetson 자원과 파이프라인 평가 |
| `tools/` | 환경·오디오·사전 녹음 자산 점검과 코퍼스 수집 |
| `tests/` | 하드웨어 없이 실행 가능한 계약·상태 머신 단위 테스트 |

가중치, 개인 음성, 측정 `results/`, `.env`와 API 키는 커밋하지 않는다.

## 환경 설정

```bash
cd ai/voice
cp .env.example .env
```

`.env`에서 `GMS_KEY`, `SENTINEL_ASR_BASE_URL`, `SENTINEL_ASR_API_KEY`를 실제 팀 값으로
교체한다. 키는 코드·문서·터미널 캡처에 노출하지 않는다.

## 테스트

```bash
cd ai/voice
python -m unittest discover -s tests -v
python -m tools.validate_guide_assets
python -m tools.check_env
```

## GPU ASR 서버

```bash
cd ai/voice
python3.12 -m venv .venv-gpu-asr
source .venv-gpu-asr/bin/activate
python -m pip install -U pip
python -m pip install -r asr_server/requirements.txt
cp asr_server/.env.example .env.gpu-asr
set -a
source .env.gpu-asr
set +a
python -m asr_server
```

```bash
curl -sS http://127.0.0.1:18100/health
```

기본 bind는 `127.0.0.1`이다. Jetson 직접 연결을 위한 격리 개발망 시험에서만
`0.0.0.0`과 평문 HTTP 예외를 사용하고, 장기 운영은 TLS 또는 승인된 터널 뒤에 둔다.

## Jetson 실행

```bash
cd /home/orin/projects/S15P11A301/ai/voice
/home/orin/projects/S15P11A301/.venv/bin/python -m tools.check_env --load
/home/orin/projects/S15P11A301/.venv/bin/python -u -m sentinel_voice.pipeline
```

실제 장치 확인부터 ROS 2 연동, 합격 기준과 장애 분류까지의 전체 절차는
[docs/08-AI-음성.md](../../docs/08-AI-음성.md#336-jetson-실행-가이드)를 따른다.

## 현재 제한

- 동적 TTS를 사용하지 않는다.
- Jetson에 Whisper 또는 로컬 LLM을 올리지 않는다.
- DeepFilterNet 결과를 STT 입력으로 사용하지 않는다.
- 사투리와 다국어는 현재 검증 범위가 아니다.
- 전체 로봇 스택과 소음 환경 성능은 실제 Jetson에서 계속 측정한다.
