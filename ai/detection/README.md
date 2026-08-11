# Detection — 객체탐지와 쓰러짐 판정

카메라 영상에서 **사람을 찾고, 추적하고, 쓰러졌는지 판정하는** 서브프로젝트입니다.
독립 실행에서는 자세 판정 이벤트와 증빙을 로컬에 저장하고, ROS 통합 실행에서는
확정된 사람 후보만 Mission Manager에 전달합니다.
재난 현장의 요구조자 탐색이 목적이라 **놓치는 것(false negative)을 가장 큰 리스크로 둡니다.**

```
프레임 ─▶ Detect(person) ─▶ BoT-SORT(trackId) ─┬─▶ 조건부 Pose ─▶ 자세 판정 ─▶ 이벤트
                                                └─▶ 장애물 표시(초당 1회, 추적 없음)
```

Pose는 매 프레임 돌지 않습니다. 같은 사람이 3프레임 연속 잡히면 그때부터 **약 2 FPS**로만
실행합니다(명세 `07-AI-탐지.md` 25.2). Orin Nano 8GB를 SLAM·Nav2·WebRTC와 나눠 쓰기 때문입니다.

## 독립 실행에서 무엇을 내놓는가

`python -m src.main` 독립 실행에서는 사람이 약 1초간 안정적으로 관측되면 로컬
`events.jsonl`에 `ENCOUNTER_CONFIRMED` 이벤트를 만듭니다.

```jsonc
{
  "messageType": "ENCOUNTER_CONFIRMED",
  "data": {
    "personCount": 2,
    "persons": [
      { "trackId": 1, "poseStatus": "FALLEN", "fallenScore": 0.806,
        "signalCount": 2, "observedSec": 2.83, "confidence": 0.894 }
    ]
  }
}
```

- **`poseStatus` 는 `NORMAL` / `FALLEN` 두 값뿐입니다.** 2026-08-03 명세 개정으로
  `POSSIBLE_FALLEN` · `POSE_UNKNOWN` 이 사라졌습니다. 관제 화면을 만들 때 2값 기준으로 하세요.
- **의료 판정이 아닙니다.** `fallenScore`(0~1)와 `signalCount`(판정에 실제로 쓴 신호 수)를
  함께 주는 이유가 그것입니다. `signalCount` 가 낮으면 근거가 얇다는 뜻입니다.
- 시야를 벗어난 사람의 **정밀 재식별은 하지 않습니다.** 독립 실행 이벤트는 같은
  track에 대해 15초 쿨다운을 적용합니다.

산출물은 `--output` 아래에 실행별 타임스탬프 폴더로 쌓입니다.

```
runs/<이름>/20260805_143012/
├── events.jsonl      # 독립 실행용 로컬 이벤트. ROS·관제 보고로 자동 전송되지 않음
├── events/           # 이벤트 증빙 이미지 (골격·bbox overlay 포함)
└── frames.jsonl      # --frame-log 를 줬을 때만. 용량이 큽니다
```

## 빠르게 돌려보기

**Python 3.10.** 저장소 루트가 아니라 `ai/detection` 에서 실행합니다.

```bash
cd ai/detection
pip install -r requirements.txt
```

모델 가중치는 **Git 에 없습니다**(`.gitignore` 가 `*.pt` / `*.onnx` 를 막습니다).
인터넷이 되면 Ultralytics 가 최초 실행 때 알아서 받습니다. 안 되면 `models/` 에 직접 둡니다.

```
models/yolo26n.pt          # Detect
models/yolo26n-pose.pt     # Pose
```

웹캠으로 확인:

```bash
python -m src.main --source 0 --show --output runs/webcam
```

영상 파일로:

```bash
python -m src.main --source data/pose_test/E02_041.mp4 --output runs/lying
```

종료는 `q` 또는 `ESC`. 실행하면 콘솔에 `avg_fps` 와 이벤트 수가 찍힙니다.

## 실행 프로파일은 두 벌입니다 — 섞지 않습니다

| | 개발 PC | Jetson (로봇) |
|---|---|---|
| 파이프라인 | `configs/pipeline.yaml` | `configs/pipeline.jetson.yaml` |
| 트래커 | `configs/tracker_sentinel.yaml` | `configs/tracker_jetson.yaml` |
| 카메라 | 노트북 웹캠, backend `auto` | USB 1280x720 MJPG, backend `v4l2` |
| 해상도 | `imgsz: 640` | `imgsz: 640`, Pose `320`, `quantize: 16` |
| 용도 | 동작 확인 | 실제 탑재 |

**판정 로직은 두 프로파일이 같습니다.** 노트북에서 확인한 동작이 로봇에서 달라지면
확인의 의미가 없기 때문입니다. 다른 것은 카메라와 추론 해상도뿐입니다.

```bash
python -m src.main --config configs/pipeline.jetson.yaml --source 0 --output runs/jetson
```

**임계값은 전부 YAML 에 있습니다.** 코드에 하드코딩하지 않습니다. 각 값 옆 주석에
왜 그 값인지와 실측 근거가 적혀 있으니, 바꾸기 전에 읽어보세요.

## ROS2 로 붙일 때

로봇에서는 카메라를 직접 열지 않습니다. `usb_cam` 이 장치를 단독 점유하므로
**토픽을 구독**해야 스트리밍과 AI 가 같은 카메라를 함께 씁니다.

```bash
source /opt/ros/humble/setup.bash
python -m src.ros_main --config configs/pipeline.jetson.yaml \
    --topic /camera/image_raw/compressed --output runs/jetson_topic
```

확정된 후보는 `/perception/person_candidates` 로 발행합니다
(계약: `common/schemas/person-candidates.schema.json`).
**encounter 는 발행하지 않습니다** — 그 권한은 Mission Manager 에 있습니다
(명세 `04-자율주행.md` 26.1 단일 권한 원칙).

ROS 통합 출력은 `observedAt`, `frameId`, 그리고 후보별 `trackId`, `confidence`,
`box`, `position`입니다. 현재 `human_localizer`가 없어 `position`은 항상 `null`이며,
독립 실행 이벤트의 `poseStatus`, `fallenScore`, `signalCount`는 이 토픽이나 관제
encounter에 실리지 않습니다. Mission Manager는 활성 encounter가 있으면 새 후보의
track ID를 그 encounter에 합치지만, encounter 사이의 1m/15초 지도 기반 중복 제거는
현재 구현되어 있지 않습니다.

## 자주 걸리는 것

| 증상 | 원인 |
|---|---|
| `ImportError: relative import` | `python src/main.py` 로 실행했습니다. **`python -m src.main`** 이어야 합니다 |
| 추적 시작부터 `ImportError` | `lap` 미설치. BoT-SORT 의 매칭 솔버인데 Ultralytics 가 안 끌고 옵니다 |
| 설정의 상대 경로를 못 찾음 | cwd 문제가 아닙니다. `_resolve_path()` 가 **`ai/detection` 기준**으로 해석합니다. systemd·ROS2 launch 로 띄우면 cwd 가 루트라서 이 동작에 의존합니다 |
| 720p 에서 FPS 급락 | `fourcc: MJPG` 확인. 무압축 YUY2 는 USB 대역폭에 걸립니다 |
| 헤드리스에서 `--show` | 쓰지 마세요. Jetson 에서 미검증입니다 |

## 디렉터리

```
src/         파이프라인 구현 모듈 13개
configs/     실행 프로파일 4개. 파이프라인 2개 × 트래커 2개
scripts/     bench_jetson.py 하나. 설정 A/B 로 FPS 와 탐지력을 같이 잽니다
tests/       python tests/test_posture_persistence.py  (52건)
models/      가중치 — Git 추적 안 함
data/        원본은 read-only 로 취급. Git 커밋 금지
runs/        추론 산출물 — Git 추적 안 함
```

## 더 읽을 것

- **[`AGENTS.md`](AGENTS.md)** — 이 서브프로젝트의 규범 문서입니다. 설계 결정, 명세와 다른 부분과
  그 사유, 미해결 이슈 목록이 전부 여기 있습니다. **코드를 고치기 전에 §0 과 §35 를 보세요.**
- [`../../docs/07-AI-탐지.md`](../../docs/07-AI-탐지.md) — 프로젝트 명세. `docs/` 가 규범이고
  `AGENTS.md` 와 충돌하면 `docs/` 를 따릅니다. 특히
  - **§25.2** 조건부 Pose와 검증된 임계값 — 정답 2,658건 대조 결과.
    **부동(inactivity) 신호 3개는 아직 미검증입니다**
  - **§25.3** 학습 데이터 조사·선정 이력
  - **§25.4** **파인튜닝을 왜 안 쓰는지** — 3개 데이터셋 × 4가지 방법을 시도했고
    사전학습 `yolo26n` 이 전부 이겼습니다
  - **§25.5** Jetson 실행과 성능 기준
