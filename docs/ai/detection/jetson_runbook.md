# Sentinel UGV Detection - Jetson Orin Nano 실행 가이드

이 문서는 Jetson Orin Nano 8GB에서 `ai/detection` 객체탐지 파이프라인을 실행하기 위한 실무용 runbook이다.

현재 코드는 **USB 카메라를 직접 열어 단독 검증**하는 방식까지 준비되어 있다. 전체 로봇 통합 단계에서는 프로젝트 명세에 따라 `usb_cam` ROS2 노드가 카메라를 단독으로 열고, AI 노드는 `/camera/image_raw/compressed` 토픽을 구독하는 방식으로 감싸야 한다.

---

## 1. 전제 조건

Jetson에 아래 라이브러리가 이미 설치되어 있다고 가정한다.

```bash
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
python -c "import ultralytics, cv2, yaml, lap; print('ok')"
```

첫 번째 명령에서 반드시 `torch.cuda.is_available()`가 `True`여야 한다.
두 번째 명령이 실패하면 누락된 패키지를 Jetson 환경에 맞게 먼저 설치해야 한다.

표준 JetPack 구성에서는 ByteTrack이 쓰는 `lap`만 누락된 경우가 많다. `lap`은 순수 pip 설치로 해결된다 (aarch64 wheel 제공, 실측 0.5.13).

```bash
pip install lap
```

---

## 2. 저장소 위치

예시는 저장소가 홈 디렉터리에 있다고 가정한다.

```bash
cd ~/S15P11A301/ai/detection
```

다른 경로에 clone했다면 `ai/detection` 디렉터리로 이동하면 된다.

---

## 3. 모델 파일 준비

인터넷이 되는 Jetson이면 Ultralytics가 최초 실행 시 자동으로 가중치를 다운로드한다.

인터넷이 안 되거나 자동 다운로드를 피하려면 아래 위치에 모델 파일을 직접 배치한다.

```text
ai/detection/models/yolo26n.pt
ai/detection/models/yolo26n-pose.pt
```

모델 파일은 Git에 커밋하지 않는다.

---

## 4. USB 카메라 확인

카메라 장치가 잡혔는지 확인한다.

```bash
ls /dev/video*
```

보통 USB 카메라는 `/dev/video0` 또는 `/dev/video1`로 잡힌다.

간단히 OpenCV에서 열리는지 확인하려면:

```bash
python - <<'PY'
import cv2

for idx in range(4):
    cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
    ok, frame = cap.read()
    cap.release()
    if ok:
        print(f"camera {idx}: OK {frame.shape[1]}x{frame.shape[0]}")
    else:
        print(f"camera {idx}: not available")
PY
```

---

## 5. 기본 실행

USB 카메라 0번을 사용해 Jetson 프로파일로 실행한다.

```bash
python -m src.main \
  --config configs/pipeline.jetson.yaml \
  --source 0 \
  --device 0 \
  --output runs/jetson
```

화면 미리보기가 필요하면 `--show`를 추가한다.

```bash
python -m src.main \
  --config configs/pipeline.jetson.yaml \
  --source 0 \
  --device 0 \
  --show \
  --output runs/jetson
```

종료는 미리보기 창에서 `q` 또는 `ESC`를 누른다.

---

## 6. 모델 파일을 직접 지정해서 실행

가중치를 `models/`에 직접 넣은 경우:

```bash
python -m src.main \
  --config configs/pipeline.jetson.yaml \
  --source 0 \
  --device 0 \
  --model models/yolo26n.pt \
  --pose-model models/yolo26n-pose.pt \
  --output runs/jetson
```

미리보기 포함:

```bash
python -m src.main \
  --config configs/pipeline.jetson.yaml \
  --source 0 \
  --device 0 \
  --model models/yolo26n.pt \
  --pose-model models/yolo26n-pose.pt \
  --show \
  --output runs/jetson
```

---

## 7. 다른 카메라 번호 시도

`--source 0`에서 카메라가 열리지 않으면 번호를 바꿔본다.

```bash
python -m src.main --config configs/pipeline.jetson.yaml --source 1 --device 0 --show --output runs/jetson
python -m src.main --config configs/pipeline.jetson.yaml --source 2 --device 0 --show --output runs/jetson
```

---

## 8. 결과 확인

실행 결과는 실행 시각별 하위 폴더에 저장된다.

```text
runs/jetson/YYYYMMDD_HHMMSS/
├── events.jsonl
└── events/
    └── *.jpg
```

`events.jsonl`은 백엔드 명세 31-5 공통 JSON 봉투 형식의 이벤트 로그다.

예시 확인:

```bash
ls runs/jetson
latest=$(ls -td runs/jetson/* | head -1)
cat "$latest/events.jsonl"
ls "$latest/events"
```

---

## 9. 상태값 의미

`poseStatus`는 프로젝트 명세 25.6 기준 3값을 사용한다.

| 값 | 의미 |
|---|---|
| `STANDING` | 사람 자세가 수직에 가까움 |
| `POSSIBLE_FALLEN` | 쓰러졌을 가능성이 있음. 의료적 판정이 아니라 관제 강조용 |
| `POSE_UNKNOWN` | 사람은 있지만 관절 정보가 부족해 자세 판정 불가 |

이벤트 트리거는 `POSSIBLE_FALLEN`이 아니라 **사람을 약 1초 안정적으로 관측한 것**이다. 자세는 이벤트의 속성으로 기록된다.

---

## 10. Jetson 프로파일 주요 설정

Jetson 실행 기본 설정 파일:

```text
configs/pipeline.jetson.yaml
```

핵심 설정:

```yaml
camera:
  backend: auto
  width: 1280
  height: 720
  fourcc: MJPG

detector:
  model: models/yolo26n.pt
  tracker: configs/tracker_jetson.yaml

pose:
  model: models/yolo26n-pose.pt
```

`backend: auto`는 Linux/Jetson에서 V4L2를 우선 사용한다.

---

## 11. 트래커 설정

Jetson 기본 트래커 설정:

```text
configs/tracker_jetson.yaml
```

로봇용 설정은 프로젝트 전체 명세에 맞춰 **ReID를 끈 상태**다.

```yaml
with_reid: False
proximity_thresh: 0.5
```

개발 PC 실험용 `configs/tracker_sentinel.yaml`은 ReID를 켠 설정이므로, Jetson 기본 실행에는 사용하지 않는다.

---

## 12. 성능 관련 주의

Jetson Orin Nano 8GB는 SLAM, Nav2, 카메라, AI 추론이 자원을 공유한다.

우선 아래 순서로 확인한다.

1. `pipeline.jetson.yaml` 기본값으로 실행
2. 화면 좌상단 FPS 확인
3. FPS가 부족하면 `pose.target_fps`, `pose.imgsz`, `detector.imgsz`를 조정
4. 그래도 부족하면 TensorRT 변환 검토

현재 코드는 조건부 Pose를 사용한다.

```text
person 3프레임 연속 감지 → Pose 활성
Pose 약 2FPS
3초 미감지 → Pose 캐시 중단
```

따라서 매 프레임 Pose를 실행하지 않는다.

---

## 13. 흔한 오류

### `ModuleNotFoundError: No module named src`

`ai/detection` 디렉터리 밖에서 실행했거나 `python src/main.py`로 실행한 경우다.

반드시 아래처럼 실행한다.

```bash
cd ~/S15P11A301/ai/detection
python -m src.main --config configs/pipeline.jetson.yaml --source 0 --device 0
```

### 카메라를 열 수 없음

다른 프로세스가 카메라를 이미 열고 있을 수 있다. 어떤 프로세스가 점유 중인지 먼저 확인한다.

```bash
fuser -v /dev/video0
```

같은 Jetson에서 스트리밍 스택(`ros2 launch sentinel_bringup sensors.launch.py`)이 돌고 있으면 `usb_cam_node_exe`가 `/dev/video0`을 점유한다. 이 경우 단독 검증 동안 해당 launch를 내리거나, 카메라 대신 영상 파일을 `--source`로 지정해 추론 체인만 검증한다. 이것이 9.6장 카메라 단일 오픈 원칙이 실기기에서 그대로 나타나는 사례다.

카메라 번호를 바꿔 시도한다.

```bash
python -m src.main --config configs/pipeline.jetson.yaml --source 1 --device 0 --show
```

참고로 BRIO 100은 `/dev/video0`(영상)과 `/dev/video1`(메타데이터) 두 노드로 잡히며, `/dev/video1`이 열리지 않는 것은 정상이다.

### `CUDA error: CUBLAS_STATUS_ALLOC_FAILED` 또는 `NvMapMemHandleAlloc: error 12`

GPU 메모리 할당 실패다. Jetson은 GPU가 시스템 RAM을 공유하므로 다른 프로세스가 메모리를 크게 점유하면 CUDA 초기화부터 실패한다.

```bash
free -h
ps aux --sort=-%mem | head
```

가용 메모리를 확보한 뒤 다시 실행한다. 실측 기준 가용 메모리 700MB 이하에서 이 오류가 재현됐고, 5GB 확보 후 정상 실행됐다.

### `torch.cuda.is_available()`가 False

Jetson용 PyTorch가 제대로 설치되지 않은 상태다. 프로젝트 PC용 `requirements.txt`를 Jetson에 그대로 설치하면 안 된다. JetPack 버전에 맞는 NVIDIA 배포 PyTorch를 사용해야 한다.

---

## 14. 최종 로봇 통합 시 주의

이 문서의 실행 방식은 **USB 카메라 직접 실행용 단독 검증**이다.

전체 로봇 통합 시에는 프로젝트 명세에 따라 다음 구조가 되어야 한다.

```text
BRIO 100
→ usb_cam 노드
→ /camera/image_raw/compressed
→ AI detection ROS2 wrapper
→ cloud_bridge_node
→ MQTT
→ Spring Boot
```

즉 최종 통합에서는 `cv2.VideoCapture(0)`를 직접 쓰는 대신 ROS2 `sensor_msgs/CompressedImage`를 구독하는 wrapper가 필요하다.

---

## 15. 실기기 검증 결과 (2026-07-30)

Jetson Orin Nano 8GB 실기기에서 이 runbook 절차를 수행한 결과다 (S15P11A301-150).

**환경**

| 항목 | 값 |
|---|---|
| 보드 | Jetson Orin Nano 8GB, 전원 모드 15W |
| L4T / JetPack | R36.4.7 (JetPack 6.x) |
| Python / PyTorch | 3.10.12 / 2.8.0 (`torch.cuda.is_available()` True) |
| Ultralytics / OpenCV / lap | 8.4.107 / 4.11.0 / 0.5.13 (lap은 pip으로 신규 설치) |
| 모델 | yolo26n.pt, yolo26n-pose.pt (Ultralytics 자동 다운로드로 `models/`에 배치) |

**확인된 항목**

- USB 카메라(BRIO 100) `/dev/video0` OpenCV V4L2 오픈과 1280x720 MJPG 설정 정상.
- `pipeline.jetson.yaml` 프로파일 GPU(`--device 0`) 실행 정상: Detect → ByteTrack → 조건부 Pose → 자세 판정 → 이벤트 기록 전 체인 동작.
- 보행자 4명이 포함된 1280x720 검증 영상 600프레임 처리: 전 프레임 사람 탐지(2,400 detections), 조건부 Pose 300회, `ENCOUNTER_CONFIRMED` 이벤트 3건(15초 중복 제거 규칙과 일치), 이벤트 스냅샷 저장 정상.
- `events.jsonl`이 31-5 공통 JSON 봉투 형식(schemaVersion, messageType, encounterId, persons[].poseStatus 등)으로 기록됨.
- 평균 처리량 **9.45 FPS** (워밍업 포함, 사람 4명 상시 등장으로 Pose 활성이 잦은 조건, 스트리밍 스택 동시 구동 상태). 목표 약 15FPS 대비 부족하므로 12장 성능 조정 순서(imgsz 축소 → TensorRT 변환)가 실제로 필요하다.

**발견되어 13장에 반영한 문제**

- 표준 JetPack 구성에 `lap` 누락 → `pip install lap`으로 해결.
- 가용 RAM 700MB 이하에서 `CUBLAS_STATUS_ALLOC_FAILED` 재현 → 메모리 확보 후 정상.
- 스트리밍 스택(`sensors.launch.py`)의 `usb_cam_node_exe`가 `/dev/video0` 점유 시 카메라 오픈 실패 → 단독 검증과 스트리밍은 카메라를 동시에 쓸 수 없다(9.6장 원칙 실증).
