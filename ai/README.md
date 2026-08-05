# AI

재난 현장의 요구조자를 탐지하고 음성으로 상태를 파악하는 AI 코드다.
탐지와 음성은 독립적으로 배포할 수 있으며 ROS 연동 계약으로 연결된다.

## 구성

- [`detection/`](detection/) — 사람 탐지·추적·위치 추정과 Pose 판정
- [`voice/`](voice/) — Jetson 음성 세션, L40S Qwen3-ASR FastAPI 서버,
  GMS 구조화, 사전 녹음 안내 재생
- [`voice/denoise/`](voice/denoise/) — 블랙박스 오디오의 서버 측 잡음 제거.
  온라인 STT 입력에는 사용하지 않는다.

## 음성 운영 구성

```text
Jetson: 마이크 → Silero VAD → 원격 ASR 요청 → GMS 구조화 → 규칙 위험도 → 관제 보고
GPU 서버: FastAPI → Qwen3-ASR-1.7B
안내 음성: 승인된 사전 녹음 WAV 재생
```

- STT 실패를 요구조자의 무응답으로 분류하지 않는다.
- LLM은 사실을 구조화하며, 위험도는 규칙으로 산출한다.
- 원음, 세션 기록, API 키와 모델 가중치는 커밋하지 않는다.

음성 실행·설정·검증 방법은 [`voice/README.md`](voice/README.md)를 따른다.
