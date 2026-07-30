# sentinel_perception

> **[이관됨]** 이 패키지의 `person_detector_node`(S15P11A301-136)는 ai/detection
> 완성 전에 Jetson 통합을 쉽게 하기 위한 임시 구현이었습니다.
> `/perception/person_candidates` 발행 역할은 ai/detection의 ROS2 wrapper
> (`ai/detection/src/ros_main.py`, S15P11A301-153)로 이관되었습니다. 메인 인식
> 로직은 항상 ai/detection을 사용합니다. 같은 GPU에서 두 YOLO 노드를 동시에
> 상시 구동하지 마세요. 이 패키지 정리는 mission_manager 연동 확인 후 진행합니다.

카메라 입력, YOLO 사람 탐지와 LiDAR 기반 지도 좌표 추정을 담당합니다 (명세 25장). AI
결과는 충돌 방지의 단일 근거로 사용하지 않습니다.

지도 좌표 추정(25.3)은 SLAM이 붙은 뒤입니다(S15P11A301-137). 지금은 사람 탐지만
구현돼 있습니다(S15P11A301-136).

## encounter를 만들지 않습니다

`/perception/person_candidates`에 **"사람이 보인다"는 사실만** 발행합니다.
`encounterId`를 발급하지 않고 `phase`도 정하지 않습니다.

26.1이 Mission Manager를 유일한 권한자로 정했습니다. 이 노드가
`/perception/encounter`를 직접 발행하면 발행자가 여럿이 되어 한 사람의 이벤트가
둘·셋으로 쪼개집니다. 계약과 그 이유는
`common/schemas/person-candidates.schema.json`에 있습니다.

```text
person_detector ──/perception/person_candidates──▶ mission_manager
                                                        │
                                          /perception/encounter (발행자 1개)
                                                        ▼
                                                 recording_manager
```

## 25.2 확정 규칙의 분담

```text
class가 person이다                          detector.py     (classes=[0])
confidence가 설정값 이상이다                 detector.py     (conf 파라미터)
동일 track이 약 1초 동안 최소 관측 횟수 만족   candidate_filter.py
박스 크기·위치가 비정상적으로 급변하지 않는다   candidate_filter.py
카메라 timestamp와 TF를 조회할 수 있다        미구현 (TF는 S15P11A301-137 이후)
이미 활성 encounter에 포함된 track인지        mission_manager (25.4)
```

마지막 항목을 여기서 하지 않는 이유는 활성 encounter를 아는 것이
`mission_manager`뿐이기 때문입니다. 알려면 이 노드가 `/perception/encounter`를
되받아야 하고 그러면 26.1의 단일 권한이 흐려집니다.

## 파일

```text
detector.py           ultralytics 호출. ROS 모름. 이미지 파일로 단독 시험 가능
tracker.py            IoU 기반 추적. ROS·YOLO 모름. 시간 주입
candidate_filter.py   25.2 확정 규칙. ROS·YOLO 모름. 시간 주입
person_detector_node.py  ROS 노드. 위 셋을 조립
```

`detector.py`만 `ultralytics`를 import합니다. 도영훈님이 S15P11A301-99~102에서 추론
모듈을 만들면 **이 파일이 그것을 호출하는 껍데기가 됩니다.** 계약과 ROS 경계는 그대로
유지됩니다. `mission_state.py`나 `upload_client.py`를 ROS와 분리한 것과 같은
패턴입니다.

## 실행 — `ros2 run`을 쓸 수 없습니다

`ultralytics`와 `torch`가 프로젝트 `.venv`에만 있고 ROS는 시스템 파이썬에 있습니다.
colcon이 만드는 실행 스크립트의 shebang이 `/usr/bin/python3`로 박히므로
`ros2 run sentinel_perception ...`은 torch를 찾지 못합니다.

launch 파일이 `.venv` 파이썬을 직접 부릅니다.

```bash
cd jetson/ros2_ws && source install/setup.bash
ros2 launch sentinel_perception detector.launch.py
```

직접 실행하려면 이렇게 합니다.

```bash
source install/setup.bash
../../.venv/bin/python -m sentinel_perception.person_detector_node --ros-args \
  --params-file install/sentinel_perception/share/sentinel_perception/config/detector.yaml \
  -p model_path:=/절대/경로/jetson/models/yolo26n.pt
```

`.venv`가 `include-system-site-packages = false`인데도 `rclpy`가 보이는 이유는 ROS가
`PYTHONPATH`를 설정하고 venv가 그것을 무시하지 않기 때문입니다. `numpy`는 venv
것(1.26.4)이 우선이라 `ai/stt` 런북의 `numpy<2` 제약도 지켜집니다.

**왜 전역 설치로 바꾸지 않는가.** 이 torch는 NVIDIA 젯슨 전용 wheel입니다
(CUDA 12.6 / cuDNN 9.3.0, apt 패키지로 존재하지 않음). `pip install torch`로는 나오지
않고, `ai/stt/README.md`가 "일반 pip로 설치하면 거의 반드시 막힙니다"라고 적어 둔
대상입니다. 루트 파티션 여유가 9.4GB인데 `.venv`가 2.6GB이므로 전역에 같은 것을 두
벌 두기도 어렵습니다. `.venv`는 `ai/stt`(faster-whisper, ctranslate2)와도 공유합니다.

## 모든 프레임을 추론하지 않습니다

카메라는 30fps인데 추론은 약 50ms입니다. 전부 처리하려 하면 큐가 밀리고 스트리밍이
GPU를 못 씁니다. 32장이 관제 영상을 우선순위로 정했습니다.

25.2의 기준이 "약 1초 동안 최소 관측 횟수"이므로 5Hz로도 창 안에 3~5번 관측됩니다.
실측에서 30fps 중 **82%를 버리고도** 확정이 정상 동작했습니다.

프레임은 `depth=1` `BEST_EFFORT`로 받아 **최신 것만** 씁니다. 큐를 쌓으면 0.5초 전
위치를 처리하게 되고, 사람이 지금 어디 있는지가 중요합니다.

콜백에서 추론하지 않습니다. 50ms 동안 실행기가 막히면 발행 타이머까지 밀려 "사람
없음"조차 못 보냅니다.

## 후보가 없어도 발행합니다

빈 `candidates` 배열을 보냅니다. 발행을 멈추면 `mission_manager`가 "사람이 사라진
것"과 "탐지 노드가 죽은 것"을 구별할 수 없고, 후자일 때 진행 중 이벤트가 조용히
종료됩니다.

추론이 실패해도 발행은 계속됩니다. 추론 타이머와 발행 타이머를 나눈 이유입니다.

반면 **모델을 못 불러오면 노드를 띄우지 않습니다.** 탐지 없이 빈 배열만 보내면
관제가 "사람 없음"으로 해석해 아무도 못 찾는 것을 모릅니다.

## FP32를 쓰지 않습니다

Orin Nano는 CPU와 GPU가 RAM을 공유합니다. 여유가 적을 때 FP32 추론은
`CUBLAS_STATUS_ALLOC_FAILED`로 시작조차 못 합니다(`jetson/models/README.md`). 그래서
`quantize='fp16'`을 명시합니다.

`half=True`는 ultralytics 8.4에서 deprecated이며 앞으로 제거됩니다. 실측에서 두
인자의 속도 차이는 없었습니다.

## 검증 기록 (2026-07-29)

### detector 단독 (이미지 파일)

```text
모델        jetson/models/yolo26n.pt (COCO 사전학습, 5.3MB)
이미지      ultralytics bus.jpg 1080x810
추론        39.4ms 중앙값 (25.3 FPS), 워밍업 2프레임 후
탐지        person 4명, confidence 0.91 / 0.91 / 0.87 / 0.56
박스        좌상단 기준 (계약과 일치)
확정        같은 박스 반복 입력 시 3번째 프레임에서 4명 확정
            → min_observations=3 이 정확히 동작
```

### ROS 노드 (실제 카메라, 스트리밍 동시)

```text
추론 주기    5.0Hz (설정 0.2초와 일치)
발행 주기    4.999Hz, 사람 없을 때 candidates: [] 정상 발행
프레임      수신 520, 추론 98, 버림 422 (82% 버림, 의도한 동작)
추론 지연    51~57ms (JPEG 디코딩 포함)
WHEP        HTTP 204 유지 — 스트리밍이 밀리지 않는다
CPU         us 20.0% + sy 7.6%
메모리      4060MB / 7619MB (53%)
온도        46.2°C
파이프라인   person_candidates 구독자 1(mission_manager),
            encounter 발행자 1(mission_manager) — 26.1 유지
단위 시험    19건 (torch 없이 시스템 파이썬에서 실행)
```

**추론 지연이 이미지 파일(39ms)보다 큽니다.** JPEG 디코딩과 프레임 복사가 더해진
값입니다. 그래도 5Hz 주기(200ms) 안에 들어오므로 버려지는 프레임 외에 밀림은
없습니다.

### 아직 검증하지 않은 것

**실제 사람을 카메라에 넣은 end-to-end입니다.** 사람이 카메라 앞에 서야 하므로
사람이 필요합니다. 확인할 것은 확정까지 걸리는 시간, `CONFIRMED` 발행, 그리고 녹화
노드를 붙였을 때 이벤트 MP4가 하나 나오는지입니다.

### TensorRT 변환을 하지 않은 이유

필요한 속도가 나오기 때문입니다. 25.2가 요구하는 것은 "약 1초 동안 최소 관측"이고
5Hz로 충분한데 지금 20Hz 이상 나옵니다.

변환에는 `.venv`를 `--system-site-packages`로 다시 만들고 TensorRT 파이썬 바인딩을
설치해야 합니다(`jetson/models/README.md`). 동작하는 CUDA torch 환경을 마감 전에
건드릴 이유가 없습니다.

`jetson/models/README.md`가 "입력 크기를 줄여도 FPS가 거의 변하지 않는다. 호출당
오버헤드가 병목이므로 TensorRT 전환의 이득이 큰 구간"이라고 짚어 뒀습니다. **동시
부하에서 스트리밍이 밀리면** 그 수치를 근거로 변환 티켓을 만듭니다. 지금은 밀리지
않습니다.

## AI 없이 검증하기

`detector.py`를 뺀 나머지는 torch 없이 시험됩니다.

```bash
cd jetson/ros2_ws/src/sentinel_perception && python3 -m pytest test/ -q
```

`detector.py`까지 확인하려면 `.venv`로 돌립니다.

```bash
cd <저장소 루트>
.venv/bin/python -c "
import sys; sys.path.insert(0, 'jetson/ros2_ws/src/sentinel_perception')
import cv2
from sentinel_perception.detector import PersonDetector
d = PersonDetector('jetson/models/yolo26n.pt')
img = cv2.imread('.venv/lib/python3.10/site-packages/ultralytics/assets/bus.jpg')
print(len(d.detect(img)), '명')
"
```

## 문제 해결

### launch가 `.venv/bin/python` 을 못 찾는다

```text
FileNotFoundError: .../ros2_ws/.venv/bin/python
```

저장소 루트를 잘못 계산한 것입니다. share 경로에서 고정된 단계 수를 되짚으면
`--symlink-install`에서 어긋납니다. 지금은 위로 올라가며 `.venv`와 `jetson/models`가
함께 있는 곳을 찾습니다. 그래도 실패하면 환경변수로 지정합니다.

```bash
SENTINEL_REPO_ROOT=/home/orin/projects/S15P11A301 ros2 launch sentinel_perception detector.launch.py
```

### 프레임을 한 번도 못 받는다

10초마다 경고가 나옵니다. 카메라 토픽과 QoS를 확인합니다.

```bash
ros2 topic hz /camera/image_raw/compressed
```

`usb_cam`은 RELIABLE로 발행하고 이 노드는 BEST_EFFORT로 구독하므로 호환됩니다.
반대 조합(RELIABLE 구독 + BEST_EFFORT 발행)은 한 건도 받지 못합니다.

### 사람이 있는데 확정되지 않는다

로그의 `추적` 수를 봅니다.

`추적 0개`면 탐지가 안 되는 것입니다. `confidence`를 낮춰 봅니다(기본 0.5).

`추적`은 있는데 `확정 0명`이면 25.2의 안정성 조건을 못 넘는 것입니다. `급변으로 확정
보류` 로그가 있으면 추적이 다른 물체를 이어붙이고 있습니다. 없으면 관측 횟수가
부족한 것이므로 `inference_period_seconds`를 줄이거나 `min_observations`를 낮춥니다.
단 2 미만은 거부됩니다 — 25.2가 단일 프레임 확정을 금지합니다.

### 한 사람이 두 명으로 세어진다

IoU 추적이 `trackId`를 바꾼 것입니다. `mission_manager`의 `personCount`는 줄어들지
않으므로(32-6) 한 번 늘면 그 이벤트 동안 유지됩니다.

`iou_threshold`를 낮추면 id 교체가 줄어듭니다. 25.4가 "정밀 재식별은 범위에서
제외한다"고 정했으므로 완전한 해결은 ByteTrack 도입 이후입니다.
