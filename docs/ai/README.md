# AI 영역 문서

재난 현장에서 사용하는 AI 기능의 조사·테스트·구현 기록입니다.
설계 기준 명세는 [04-자율주행-AI.md](../04-자율주행-AI.md)이며,
이 디렉터리는 그 설계를 실측으로 검증하고 구현으로 옮기는 과정의 근거 문서를 모읍니다.

| 하위 영역 | 문서 | 용도 |
|---|---|---|
| 객체탐지 | [../04-자율주행-AI.md](../04-자율주행-AI.md) 25.7 | Jetson 실행·검증 절차, 성능 조정, 흔한 오류 (2026-07-30 통합) |
| 객체탐지 | [detection/dataset_selection.md](detection/dataset_selection.md) | 학습·검증 데이터셋 조사와 선정 근거 |
| 객체탐지 | [detection/requirements.md](detection/requirements.md) | 객체탐지 MVP 요구사항 초안·이월 항목 |
| STT·LLM·TTS | [`ai/stt/docs/README.md`](../../ai/stt/docs/README.md) | 설계·안전 정책·실행·검증 통합 문서 (단일 기준) |

- 구현 코드는 저장소 루트의 `ai/` 아래에 관리합니다.
- 성능 수치는 측정 환경, commit, 하드웨어 버전과 함께 기록합니다(문서 유지 원칙 5).
