# Jetson

Jetson Orin Nano에서 실행되는 로봇 온보드 소프트웨어입니다. 센서 수집, SLAM, 주행·안전 제어, 사람 탐지·접근, 임무 상태 머신, 영상 녹화·스트리밍은 로컬에서 동작합니다. 음성 인식(Qwen3-ASR)과 정보 구조화(GMS)는 원격 서비스를 사용합니다.

## 구성

| 경로·패키지 | 역할 |
|---|---|
| `ros2_ws/src/esp32_bridge` | 모터·센서 ESP32 직렬 브리지 |
| `sentinel_description` | 실측 URDF와 정적 TF |
| `sentinel_bringup` | 전체 launch와 Nav2·SLAM·EKF·안전 파라미터 |
| `sentinel_drive` | 전륜 조향 자전거 모델 역운동학 |
| `sentinel_safety` | 명령 중재·속도 제한·충돌 감시·최종 게이트 |
| `sentinel_exploration` | Frontier 목표와 카메라 커버리지 스윕 |
| `sentinel_approach` | 방위각+LiDAR 기반 요구조자 접근 |
| `sentinel_mission` | 임무·encounter 상태 머신 |
| `sentinel_recorder` | 링 버퍼, 이벤트 MP4, 지도 저장·업로드 |
| `sentinel_streaming` | H.264 변환과 MediaMTX 송출 |
| `sentinel_bridge` | ROS↔MQTT 관제 브리지 |
| `streaming_poc` | 카메라·WebRTC PoC 기록(운영 코드 아님) |

탐지와 음성 소스는 각각 `ai/detection`, `ai/voice`에 있고 `demo.launch.py`가 실행 경로를
연결합니다.

## 설치·빌드

저장소 루트에서 실행합니다.

```bash
./scripts/setup_jetson.sh --check
./scripts/setup_jetson.sh
cd jetson/ros2_ws
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
colcon test
colcon test-result --verbose
```

ROS 명령을 직접 실행할 때는 루트에서 `source scripts/ros_env.sh`를 사용해 워크스페이스와
DDS 격리 설정을 함께 불러옵니다.

## 전체 스택 실행

```bash
./scripts/demo_up.sh
./scripts/demo_down.sh
```

`demo_up.sh`는 중복 스택을 거부하고, 센서 ESP32 udev 별칭을 감지하면
`enable_esp32:=true`를 자동으로 전달합니다. launch 인자는 그대로 넘길 수 있습니다.

```bash
./scripts/demo_up.sh enable_nav2:=true enable_exploration:=true \
  enable_safety:=true enable_ekf:=true
```

기본으로 켜지는 항목은 SLAM, 스트리밍, 녹화, 임무, 클라우드 브리지, 음성, 탐지,
Foxglove 지도입니다. `enable_nav2`, `enable_exploration`, `enable_approach`,
`enable_safety`, `enable_ekf`는 기본 `false`입니다. 특히 `enable_safety:=true`는
`/cmd_vel`을 전륜 운동학과 모터 브리지까지 연결하므로 차량을 띄우고 E-Stop을 확인한
뒤에만 켭니다.

현재 후방 초음파는 `/range/rear` 관측까지 구현되어 있습니다. 방향별 임계 실측 전이라
`protective_stop`과 Nav2 후진 안전에는 사용하지 않습니다. 전방 보호 정지도 빈 공간
오측 때문에 센서 펌웨어의 `PROXIMITY_STOP_ENABLED=false`로 발동을 껐으며,
`/range/front` 거리 관측은 계속 유지합니다.

세부 설치·기동·장애 진단은 [scripts/README.md](../scripts/README.md)와
[sentinel_bringup README](ros2_ws/src/sentinel_bringup/README.md)를 따릅니다.
