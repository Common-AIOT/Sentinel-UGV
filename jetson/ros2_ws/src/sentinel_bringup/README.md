# sentinel_bringup

전체 시스템 launch, lifecycle 순서와 장치별 파라미터 조합을 담당합니다. 시작 전 안전 상태를 확인하고 핵심 센서 실패 시 주행 노드를 활성화하지 않습니다.

## Jetson 센서 bringup

Logitech Brio 100과 YDLIDAR X4 Pro를 ROS 2 Humble에서 실행한다.

라이다 USB 권한이 없으면 사용자를 `dialout` 그룹에 추가한 뒤 로그아웃·로그인한다.

```bash
sudo usermod -aG dialout "$USER"
```

```bash
sudo apt install -y python3-vcstool v4l-utils \
  ros-humble-image-transport-plugins
cd ~/projects/S15P11A301/jetson/ros2_ws
vcs import src < sentinel.repos
../../scripts/setup_jetson.sh
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash
```

라이다 단독 실행:

```bash
ros2 launch sentinel_bringup lidar.launch.py
```

카메라·라이다 동시 실행:

```bash
ros2 launch sentinel_bringup sensors.launch.py
```

목표값은 카메라 `1280x720 @ 30 FPS`, `/scan` 약 `10~11 Hz`다. 상세 절차는 워크스페이스의 `CAMERA_LIDAR_INTEGRATION_TEST.md`를 참고한다.

TF 골격(`base_footprint`~`camera_optical_frame`)은 `sentinel_description`의 `robot_state_publisher`가 발행하며 `lidar.launch.py`에 포함된다. LiDAR 스캔 `frame_id`는 `lidar_link`다(S15P11A301-74에서 `laser_frame`에서 통일).

Brio 설정은 `config/brio_100.yaml`에 있으며 `usb_cam`의 `raw_mjpeg` 패스스루를 사용한다. 영상은 `/camera/image_raw/compressed`(JPEG)로만 발행되며, `usb_cam`은 `sentinel.repos`로 받은 0.8.1 소스에 raw_mjpeg 버그 패치(`patches/`)를 적용해 빌드한다. 패치 적용은 `scripts/setup_jetson.sh`가 담당하므로 손으로 `git apply`하지 않는다. 빌드 전 확인은 `./scripts/setup_jetson.sh --check`다. Brio가 `/dev/video0`가 아니면 해당 파일의 `video_device`를 변경한다. `tri_test`는 라이다 ROS 2 드라이버와 동시에 실행하지 않는다.

## SLAM (S15P11A301-137)

`slam_toolbox`가 `/scan`으로 지도를 만들고 `map → odom` TF를 발행합니다. 그러면
`cloud_bridge`가 `map → base_footprint`를 조회해 telemetry의 `pose`를 채웁니다
(명세 8.3의 TF 트리, 23.1~23.2).

```bash
ros2 launch sentinel_bringup slam.launch.py
```

센서 launch에 넣지 않았습니다. SLAM을 끄고도 카메라·라이다·스트리밍이 돌아야 하고
(32장 장애 격리), SLAM은 지도 크기에 따라 메모리를 계속 쓰므로 필요할 때만
올립니다.

### 오도메트리가 없는 임시 구성입니다

엔코더가 ESP32 연동(S15P11A301-84·85) 이후이므로 `ekf_node`의 `/odometry/filtered`가
없습니다. 그런데 `slam_toolbox`는 **`odom → base_frame` TF를 요구합니다.** 없으면
"Failed to compute odom pose"를 반복하고 지도를 만들지 않습니다.

그래서 launch가 `odom → base_footprint`를 static identity로 발행합니다. 그러면
로봇의 실제 이동이 전부 `map → odom`에 담기고, 스캔 매칭만으로 위치를 추정합니다.

한계가 두 가지입니다.

**제자리 회전에서 정확도가 떨어집니다.** 스캔 매칭은 벽 모양이 비슷한 방향을
구분하지 못하는데, 오도메트리가 있으면 회전량을 초기 추정으로 줄 수 있습니다.
그래서 `do_loop_closing`을 켜 누적 오차를 잡습니다.

**`odom`이 로봇 이동을 표현하지 않습니다.** Nav2가 붙으면 `odom`을 속도 추정에
쓰는데 그 값이 항상 0입니다. 엔코더가 붙으면 이렇게 전환합니다.

```bash
ros2 launch sentinel_bringup slam.launch.py publish_static_odom:=false
```

static TF와 `ekf_node`가 같은 변환을 발행하면 TF 트리가 충돌하므로 반드시 끕니다.

### pose는 SLAM이 없으면 null입니다

`cloud_bridge`가 값을 지어내지 않습니다. 관제가 "위치 모름"과 "원점에 있음"을
구별해야 하고, 후자로 오해하면 지도에 로봇이 엉뚱한 곳에 그려집니다.

생존 판정은 `/map` 발행자 수로 합니다. TF 조회만으로는 안 됩니다 —
`tf2_ros.Buffer`가 마지막 변환을 얼마간 들고 있어서 SLAM이 죽은 직후에도 조회가
성공합니다. S15P11A301-135에서 `mission_manager`에 쓴 방식과 같습니다.

`mapId`는 SLAM이 뜰 때마다 새로 발급합니다. 재시작하면 지도가 처음부터 만들어지므로
옛 `mapId`를 유지하면 관제가 두 지도의 좌표를 섞습니다.

### 검증 기록 (2026-07-29)

#### 기동과 pose 전달

```text
기동          slam_toolbox + static odom TF, 경고·오류 0
지도          /map 0.5Hz (map_update_interval 2.0과 일치)
TF            map → base_footprint 생성
pose 전달      TF [1.240, -0.560] yaw 0.866
              → telemetry {x: 1.24, y: -0.56, yaw: 0.8665} 일치
mapId         SLAM 재시작 시 a6b18bfe → d09fdfa0 재발급
SLAM 사망      SIGKILL 후 0.5초(telemetry 1주기) 만에 pose=null
동시 부하      SLAM + 스트리밍 + 탐지 + 브리지 + 센서 = 노드 8개
              CPU us 28.7% + sy 6.5%, 메모리 57%, 47.3°C
              WHEP HTTP 204 유지
```

`pose` 전달을 확인할 때 static odom TF를 다른 값으로 바꿔 봤습니다. 정지한 로봇에서
0만 보면 상수를 보내는 것과 구별할 수 없기 때문입니다. **이 검증이 증명하는 것은 TF
체인과 yaw 변환이 맞다는 것뿐이며, SLAM이 지속적으로 추정한다는 증거는 아닙니다.**
그것은 아래 이동 측정으로 확인했습니다.

#### 사람이 로봇을 밀어 측정한 정확도

모터가 없으므로 사람이 차체를 굴렸습니다. 들지 않고, 방향을 유지하며, 한 방향으로
약 50cm 간 뒤 같은 자리로 되돌린 왕복입니다.

```text
이동 추정      실제 50cm → 0.234m          절반으로 과소 추정
원점 복귀      1.5~4.2cm                   좋음
정지 중 안정    yaw 0.0도 고정, 위치 0.02m 안
이동 후 yaw    -25도로 정착                방향 유지했는데 25도 오차
지도 성장      124x257 → 333x290, 점유 670 → 4769
```

궤적입니다.

```text
  0~52초   정지        원점거리 0.000~0.020m   yaw 0.0도 고정
 62~92초   밀고 나감    0.020 → 0.234m         yaw -12 → -31 → -2도
102~122초  되돌아옴     0.138 → 0.032m
132~172초  정지        0.015~0.042m           yaw -24~-26도 (2도 변동)
```

**지도 좌표계 안에서는 일관적입니다.** 로봇이 지도 어디쯤 있는지, 이벤트가 어디서
났는지는 맞게 표시됩니다. 같은 자리로 돌리면 SLAM도 같은 자리라고 봅니다(오차 수 cm).

**절대 거리와 방향은 못 믿습니다.** "3m 이동했다" 같은 표시는 틀리고, 방향 화살표는
25도 어긋납니다. 관제 화면에 미터 단위 거리나 정밀한 방향을 표시하려면 엔코더가
필요합니다.

이것이 오도메트리 없는 2D 라이다 SLAM의 본질적 한계이며, 명세 8.2가 `ekf_node`를 둔
이유입니다. 파라미터로 메울 성질이 아닙니다.

#### 첫 측정을 버린 이유

첫 측정에서 정지 중 yaw가 34도 튀는 것을 보고 "스캔 매칭이 잘못된 해로 수렴했다"고
진단했습니다. **틀린 진단이었습니다.** 사람이 선 꼬임을 피하려 로봇을 들어올렸다
내려놓은 구간이었습니다. 라이다를 들면 스캔이 완전히 달라지고 내려놓을 때 다른 자세로
매칭됩니다.

그래서 첫 측정 데이터 전체를 비교 기준에서 제외했습니다. 그 안의 "50cm → 0.426m"처럼
좋아 보이는 수치도 같은 이유로 신뢰할 수 없습니다. 위 표는 들어올림 없이 측정한 것만
담았습니다.

**측정할 때 로봇을 들지 않습니다.** 바퀴로 굴립니다. 이것이 검증 절차의 일부입니다.

### 아직 확인하지 않은 것

`minimum_time_interval`을 0.1로 더 낮추면 이동 추정이 나아질 수 있습니다. 이번에는
조건이 깨끗하므로 비교가 유효합니다. 남은 오차의 주된 원인은 오도메트리 부재라고
보아 시도하지 않았습니다.

### 문제 해결

**지도가 자라지 않고 pose가 0에서 안 움직인다.** `minimum_travel_distance`를 0이
아닌 값으로 두면 이렇게 됩니다. 로그에 이것이 이어집니다.

```text
Message Filter dropping message: frame 'lidar_link' at time ...
for reason 'discarding message because the queue is full'
```

`slam_toolbox`는 "얼마나 움직였는가"를 **odom pose로** 판단합니다. 오도메트리가 없어
`odom → base_footprint`가 static identity이면 그 값이 항상 (0,0,0)이므로 매 스캔이
"제자리"로 판정돼 버려집니다. 처리가 없으니 message filter 큐가 차고 위 로그가
반복됩니다.

첫 스캔으로 만든 지도만 남아 겉보기에는 SLAM이 도는 것처럼 보입니다. 실측에서 로봇을
20cm 왕복했는데 pose가 0.000m, 점유 셀이 46개로 고정이었습니다.

`ekf_node`가 붙어 진짜 오도메트리가 생기면 이 값을 다시 올릴 수 있습니다. 그때까지는
0이어야 하고, CPU는 `minimum_time_interval`로 관리합니다.

**pose가 계속 null이다.** `/map` 발행자가 있는지 봅니다.

```bash
ros2 topic info /map
```

발행자가 0이면 `slam_toolbox`가 죽은 것입니다. 있는데도 null이면 TF 체인이 끊긴
것이므로 `robot_state_publisher`가 떠 있는지 확인합니다.

```bash
ros2 run tf2_ros tf2_echo map base_footprint
```

**`minimum laser range setting ... exceeds the capabilities` 경고.**
`min_laser_range`를 라이다의 `range_min`과 똑같이 주면 나옵니다. `/scan`의
`range_min`이 float32라 `0.10000000149...`이고 `slam_toolbox`가 그 값과 비교하기
때문입니다. 조금 크게(0.12) 주면 조용해집니다.

## 데모 전체 스택 (S15P11A301-156)

한 줄로 데모 구성 전부를 올립니다.

```bash
./scripts/demo_up.sh                          # 저장소 루트에서
# 또는
ros2 launch sentinel_bringup demo.launch.py
```

```text
 0s  sensors    usb_cam + lidar
 4s  slam · streaming(TLS WHEP)
 8s  recorder(recording_manager + media_uploader) · mission
10s  bridge (MQTT wss://api.sentinel-ugv.xyz:443/mqtt)
14s  detector (ai/detection wrapper — S15P11A301-155)
```

단계별 지연을 두는 이유는 부팅 직후 전부 동시에 뜨면 CPU·메모리 경합으로 NVMM
버퍼 할당이 실패하기 때문입니다(실측). 구성 요소는 `enable_*` 인자로 끕니다.

```bash
ros2 launch sentinel_bringup demo.launch.py enable_detector:=false
```

### 선행 조건 (기기당 한 번)

```bash
sudo mkdir -p /var/lib/sentinel/media && sudo chown -R orin:orin /var/lib/sentinel
# ~/.config/sentinel/secrets.yaml (600):   broker_password: <MQTT 비밀번호>
# ~/.config/sentinel/certs/server.{crt,key}  — S15P11A301-145 공인 인증서
```

### 부팅 자동 시작 (systemd)

유닛은 설치돼 있고 **기본 비활성**입니다. 개발 중에 켜 두면 개발자가 올리는
launch와 이중 인스턴스가 되어 카메라 단일 오픈(32-3)이 깨집니다.

```bash
# 설치 (기기당 한 번)
sudo cp scripts/systemd/sentinel-demo.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo loginctl enable-linger orin    # 부팅 시 PulseAudio(오디오 브랜치)용

# 데모 전에 켜기 / 개발로 돌아올 때 끄기
sudo systemctl enable --now sentinel-demo
sudo systemctl disable --now sentinel-demo

# 로그
journalctl -u sentinel-demo -f
```

### launch 인자 이름 충돌을 조심하십시오

여러 launch를 include할 때 `GroupAction(scoped=True)`로 감싸야 합니다.
LaunchConfiguration은 launch context **전역**이라, lidar.launch가 설정한
`params_file`이 뒤에 include되는 streaming·recorder·mission·bridge의 같은 이름
인자 기본값을 조용히 덮습니다. 실측에서 `stream_pipeline`이
`ydlidar_x4_pro.yaml`을 params로 받았고, 각 노드가 코드 기본값으로 돌아
겉보기에는 정상이었습니다 — recorder의 `no_response_timeout` 300초
(S15P11A301-142 완화)가 조용히 30초로 퇴행한 상태였습니다. `demo.launch.py`의
`_include()`가 그 격리를 담당합니다.

### 없는 launch 파일은 건너뜁니다

include 대상 launch 파일이 없으면 demo.launch가 죽는 대신 해당 구성만 건너뛰고
로그를 남깁니다. `detection.launch.py`는 S15P11A301-155가 넣는 파일이라 머지
순서에 따라 없을 수 있고, 탐지 하나 때문에 스트리밍·녹화·관제까지 죽으면 32장
장애 격리에 어긋납니다.

### 검증 (2026-07-30)

`demo.launch.py` 단독 기동으로 확인했습니다.

```text
프로세스 12개 전부 기동, 죽은 프로세스 0
bridge     MQTT 연결됨 + cmd/mission 구독 (secrets.yaml에서 비밀번호)
streaming  TLS WHEP 204, 링 버퍼 /var/lib/sentinel/media/buffer
recorder   상한=580MB → recorder.yaml이 실제로 로드됨 (충돌 수정의 증거)
조각        h264+aac 두 스트림 (S15P11A301-131 오디오 포함)
```

**재부팅 자동 기동은 아직 검증 전입니다.** 유닛을 enable한 상태의 재부팅
시험은 장비를 공유하는 다른 작업이 없는 시점에 합니다(티켓의 시점 제약).
