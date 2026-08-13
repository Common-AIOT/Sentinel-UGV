# AI

재난 현장의 요구조자를 탐지하고 음성으로 상태를 파악하는 AI 코드다.
탐지와 음성은 독립적으로 배포할 수 있으며 ROS 연동 계약으로 연결된다.

## 구성

- [`detection/`](detection/) — 사람 탐지·추적·위치 추정과 Pose 판정
- [`voice/`](voice/) — Jetson 음성 세션, L40S Qwen3-ASR FastAPI 서버,
  GMS 구조화, 사전 녹음 안내 재생
- [`voice/denoise/`](voice/denoise/) — 블랙박스 오디오의 서버 측 잡음 제거.
  온라인 STT 입력에는 사용하지 않는다.

## 두 프로젝트의 레이아웃이 다르다

같은 `ai/` 아래인데 구조가 갈린다. 새로 들어온 사람이 가장 먼저 걸리는 자리라
적어 둔다 (S15P11A301-377).

| | `voice/` | `detection/` |
|---|---|---|
| 코드 위치 | `sentinel_voice/` (정식 패키지) | `src/` (평면) |
| 패키지 설정 | 있다 | **없다** |
| 실행 | 패키지 이름으로 import | `python -m src.main`, `python -m src.ros_main` |
| 작업 디렉터리 | 무관 | **`ai/detection` 고정 필요** |

`detection/` 이 `src` 를 패키지 이름처럼 쓰기 때문에 생기는 대가가 둘이다.
`cwd` 를 고정하지 않으면 import 가 깨지고 — 그래서
[`detection.launch.py`](../jetson/ros2_ws/src/sentinel_bringup/launch/detection.launch.py)
가 `.venv` 파이썬을 `ExecuteProcess` 로 부르면서 `cwd` 를 함께 넘긴다 — 다른
프로젝트에서 이 코드를 라이브러리로 가져다 쓸 수 없다.

**고치지 않고 남겨 둔다.** `src/` 를 패키지 이름으로 바꾸면 launch·시험·설정
주석·`AGENTS.md` 가 함께 움직이는데, 그 launch 경로를 검증하려면 젯슨 실기가
필요하다. 시연이 끝난 시점에 실기 검증 없이 건드릴 값이 아니다. 되살릴 때
바뀌어야 하는 자리는 위 표의 「실행」 행이 전부다.

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
