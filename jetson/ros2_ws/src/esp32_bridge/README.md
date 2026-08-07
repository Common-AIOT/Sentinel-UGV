# esp32_bridge

모터·센서 ESP32와 USB 직렬(921600bps)로 통신하고, 센서 페이로드를 **표준 ROS 메시지**로 변환하는 ROS2 패키지 (S15P11A301-84). `/cmd_vel` 역운동학은 `sentinel_drive`(전륜 조향 변환), 안전 중재는 `sentinel_safety`, BTS7960·조향 서보 실제 구동은 ESP32 펌웨어의 몫이다.

**두 링크의 프레이밍이 S15P11A301-321부터 서로 다르다.** 센서는 여전히 COBS+CRC16+길이+uptime(`packet_codec.py`)이고, 모터는 동기워드+고정길이+CRC8(`motor_packet_codec.py`)로 교체됐다 - 메시지 종류·의미는 같고 프레이밍만 바뀌었다. 근거는 아래 "모듈" 절과 `hardware/esp32/motor/esp32_motor_comm/motor_protocol.h` 참고.

## 노드

- `esp32_motor_bridge` — `/dev/sentinel_mcu_motor`(기본값)를 열어 모터 ESP32와 통신. `~/drive_command`(std_msgs/String, JSON)를 구독해 `DRIVE_COMMAND`로 전송, `~/stop`/`~/estop` (`std_srvs/Trigger`) 서비스 제공, `~/drive_state`/`~/command_ack` 발행, `/diagnostics` 발행. JSON 은 후륜 좌·우 `target_drive_*_mmps` 와 전륜 `target_steering_mdeg`·`max_steering_rate_mdps` 를 함께 싣는다(2026-08-06 전륜 조향 복구로 조향 필드가 다시 살아났다). **조향 키가 빠지면 마지막 값을 유지한다** — 0 을 기본값으로 두면 명령마다 앞바퀴가 중립으로 돌아가 §34-7 과 정반대가 된다. `keepalive_period_s`(기본 0.15s)로 핸드셰이크 여부와 무관하게 영원히 HELLO를 재전송한다(S15P11A301-321 전에는 이 keepalive가 없어, 핸드셰이크 이후 `~/drive_command`가 끊기면 이 노드가 만드는 트래픽이 전혀 없었다).
- `esp32_sensor_bridge` — `/dev/sentinel_mcu_sensor`(기본값)를 열어 센서 ESP32와 통신. 아래 "발행 토픽" 표대로 표준 메시지를 발행하고 `/diagnostics`에 보드 상태와 오도메트리 카운터를 낸다. HELLO를 ~6-7Hz로 keep-alive 재전송해 센서 보드의 300ms 통신 워치독에 입력을 공급한다(§34-7 gap-fill, README 하단 참고).
- `esp32_hello_check` — ROS 없이 포트를 열어 HELLO/HELLO_ACK만 확인하는 브링업 도구(옛 프레이밍, **센서** 전용). 하드웨어를 막 연결했을 때 가장 먼저 실행할 것.
- `esp32_motor_hello_check` — 위와 같은 목적이지만 모터 전용(새 프레이밍). 모터 보드를 새로 플래싱했으면 이걸 쓸 것 - 옛 `esp32_hello_check`는 새 프레이밍 프레임을 아예 못 알아봐 항상 타임아웃난다.

## 발행 토픽 (`esp32_sensor_bridge`)

| 프레임 | 토픽 | 타입 | 비고 |
|---|---|---|---|
| `ENCODER_STATE` | `/wheel/odometry` | `nav_msgs/Odometry` | `odom`→`base_footprint`, 후륜 tick 정운동학(yaw 는 IMU 가 주 소스, 아래 참고) |
| `IMU_STATE` | `/imu/data_raw` | `sensor_msgs/Imu` | `imu_link`, 원시 gyro/accel, 100Hz |
| `PROXIMITY_STATE` | `/range/front` | `sensor_msgs/Range` | `ultrasonic_front_link`, ULTRASOUND |
| `PROXIMITY_STATE` | `/proximity/protective_stop` | `std_msgs/Bool` | 보드 로컬 보호 정지, TRANSIENT_LOCAL |
| `ENVIRONMENT_STATE` | `/environment/temperature` | `sensor_msgs/Temperature` | DHT-11 판독 성공 시에만 |
| `ENVIRONMENT_STATE` | `/environment/relative_humidity` | `sensor_msgs/RelativeHumidity` | 0~1 비율(deci-percent 아님) |

토픽 이름·프레임은 전부 파라미터다(`config/esp32_bridge.yaml`).

이전에는 셋 다 `std_msgs/String` JSON이었다. 그러면 `robot_localization` EKF도 Nav2 costmap/collision_monitor도 아무것도 구독할 수 없어 자율주행 전 단계가 막힌다.

세부 계약 몇 가지:

- **QoS는 전부 RELIABLE**이다. RELIABLE 발행자는 BEST_EFFORT 구독자도 받을 수 있지만 반대는 조용히 아무것도 오지 않는다. Nav2·`robot_localization`의 기본 구독 프로파일이 제각각이라 호환 범위가 넓은 쪽으로 통일했다.
- **`/range/front`의 `+Inf`는 "미검출"이다.** 보드는 에코 타임아웃을 `MAX_VALID_DISTANCE_CM`(4m)으로 clamp해 보내는데, 그대로 두면 4m 지점의 실제 장애물처럼 읽힌다. `valid_sensor_mask` 비트가 꺼졌거나 거리가 `range_max_m` 이상이면 `+Inf`로 바꾼다.
- **온습도는 `status_flags`가 정상일 때만 발행한다.** 보드가 실패 시 마지막 정상값을 그대로 들고 있어, 그냥 내보내면 오래된 값이 새 측정처럼 보인다. 실패는 `/diagnostics`의 `ENVIRONMENT_SENSOR_FAULT`로만 드러낸다.
- **`measured_steering_mdeg`는 발행하지 않는다.** 항상 0이다 — 전륜 조향이 복구됐지만 DS51150 서보가 내부 폐루프로 각도를 유지하고 외부로 출력하지 않아 **조향은 개루프**이며 실제 각도를 재는 수단이 없다(§34-5). 모터 채널 `DRIVE_STATE`의 `target_steering_mdeg`·`steering_actuator_cmd`는 명령이지 측정이 아니다.

## IMU (`/imu/data_raw`, S15P11A301-244)

`IMU_STATE`(0x26)로 오는 MPU6050 원시 gyro/accel을 `sensor_msgs/Imu`로 낸다. 명세 23.2·§34-5의 계약이 셋이다.

- **타임스탬프를 `sample_time_us`에서 만든다.** 수신 시각을 측정 시각으로 대신하지 않는다. `imu_clock.BoardClockOffset`이 보드 monotonic µs → ROS 시각 오프셋을 추정한다 — 전송 지연은 항상 양수이므로 관측한 `수신시각 − 측정시각`의 **최솟값**이 참 오프셋에 가장 가깝다(min filter). 평균을 쓰면 평균 지연만큼 통째로 늦게 찍힌다. 크리스털 드리프트를 따라가려고 `imu_clock_resync_period_s`(기본 10초) 창마다 최솟값을 다시 채택하므로 오차가 "창 길이 × 드리프트"(10s·100ppm = 1ms)로 묶인다.
- **재부팅 판정은 `sample_time_us` 역행으로 한다.** monotonic 시계가 되돌아가는 것은 재부팅뿐이다. "오프셋이 크게 뛰면 재부팅"으로 보면 Jetson 수신 스레드가 CPU 부하로 굶었을 때 오진에 걸리고, 그때 오프셋을 다시 잡으면 굶은 시간이 전송 지연으로 오인되어 스탬프가 그만큼 늦어진다.
- **`orientation`은 채우지 않는다.** 원시 출력에 자세 융합이 없으므로 REP-145 관례대로 `orientation_covariance[0] = -1`로 "추정값 없음"을 표시한다. 이걸 빼면 EKF가 항등 자세를 실제 관측으로 융합해 로봇이 계속 정면을 본다고 믿는다.

발행 여부는 `status_flags`로 갈린다.

| `status_flags` | 동작 |
|---|---|
| `VALID` + clock offset 안정 | 정상 발행(`imu_*_variance` 공분산) |
| `CALIBRATING`·`RANGE_ERROR`, 또는 clock offset 미안정 | 발행하되 공분산을 `imu_untrusted_variance`(1e6)로 실어 EKF 융합에서 제외한다. 값을 끊으면 축 정렬 검증(TESTING.md 10-4)에 쓸 것이 없어지고, 그대로 신뢰하면 바이어스 미보정 자이로가 융합된다 |
| `BUS_ERROR` | **발행하지 않는다.** 판독 실패 시 보드가 마지막 값을 그대로 들고 있어(`sensor_task.cpp`) 내보내면 오래된 값이 새 측정처럼 보인다. DHT-11과 같은 규칙이며, 실패는 `/diagnostics`의 `IMU_SENSOR_FAULT`로 드러낸다 |

**같은 `sample_time_us`가 두 번 오면 뒤쪽을 버린다.** comm_task의 송신 주기(10ms)와 센서 태스크의 샘플 주기(10ms)가 서로 독립이라 같은 측정이 두 프레임에 실릴 수 있고, 같은 스탬프로 두 번 발행하면 EKF가 한 측정을 두 번 센다. 그래서 `ros2 topic hz /imu/data_raw`가 100Hz보다 조금 낮게 나오는 것은 정상이다.

`/diagnostics`에 `esp32_bridge: IMU` 항목이 함께 나온다. 토픽이 조용할 때 사유(BUS_ERROR / 중복 / 프레임 미수신)를 여기서 구별하며, `clock_offset_ms`·`clock_resync_count`·`published_count`·`skipped_bus_error_count`를 센다.

> `imu_angular_velocity_variance`·`imu_linear_acceleration_variance`는 **진동·bias 실측 전 임시값**이다(TBD-HW-012). 데이터시트 잡음 밀도만 보면 gyro가 3e-7 (rad/s)² 수준이지만 실제로는 차체 진동과 온도 bias가 지배하므로 보수적으로 (0.02 rad/s)²·(0.2 m/s²)²를 넣었다. EKF 융합을 켜기 전에 정지·주행 로그로 다시 잡을 값이다.

## 오도메트리와 TF

`ENCODER_STATE`의 후륜 좌·우 누적 tick으로 정운동학을 계산한다.

```
d_L = Δtick_L · meters_per_tick_L,  d_R = Δtick_R · meters_per_tick_R
v   = (d_R + d_L) / 2Δt ,  ω = (d_R − d_L) / WΔt
```

- **속도를 보드가 보낸 mm/s가 아니라 tick 차분에서 구한다.** 보드의 mm/s는 `sensor_task.cpp`에 하드코딩된 기어비·바퀴 지름으로 계산되므로, 캘리브레이션 값을 Jetson 파라미터 한 곳에만 두려면 tick이 유일한 입력이어야 한다. 재플래싱 없이 재튜닝된다.
- **Δt를 프레임 도착 시각이 아니라 `senderUptimeMs`에서 구한다.** USB 직렬 도착 지터가 그대로 속도 잡음이 되는 것을 막는다.
- **`publish_odom_tf`는 기본 `false`다.** 지금은 `slam.launch.py`가 `odom → base_footprint`를 static identity로 발행하고 있어(그쪽 docstring 참고) 동시에 켜면 두 발행자가 같은 TF를 다툰다. `publish_static_odom:=false`로 static을 끈 뒤 true로 올리거나, Phase 3에서 `ekf_node`가 `/odometry/filtered`로 TF를 소유하게 한 뒤 계속 false로 둔다.
- 보드가 재부팅하면 tick 카운터가 0으로 돌아간다. 기준점만 다시 잡고 **pose는 유지한다** — odom 프레임은 연속이어야 한다(REP-105). 재부팅 동안의 이동량은 유실된다.
- tick 점프(I2C 글리치)는 `max_wheel_speed_mps`로 걸러 `/diagnostics`의 `rejected_sample_count`로 센다.

- **`ω`(`angular_z`)는 EKF 입력이 아니다.** 전륜 조향 차량에서 후륜 좌·우 속도 차는 회두의 근거가 되지 못한다 — 조향 링크가 회두를 정하고 후륜은 같은 속도로 구동되므로 선회 중 내·외측 후륜이 노면에 **스크럽**한다(§35-3). yaw 의 주 소스는 IMU 자이로이고 `ekf_node`가 엔코더 `vx` + IMU `vyaw` 로 융합한다(23.2). 여기서 계속 계산하는 이유는 `x·y` 적분에 자세가 필요하고, §35-3 의 네 값(엔코더 yaw · 조향각 yaw · IMU yaw · 실제 회전량) 비교에 쓰기 때문이다.

> ⚠️ **캘리브레이션 값이 전부 임시값이다(TBD-CAL-001, §35-3).** `meters_per_tick`은 `sensor_task.cpp`의 실측 전 상수(바퀴 지름 120mm·기어비 82)에서 유도했고, 트랙폭 `W = 0.30`은 근거 없는 자리값이다. 그대로면 노드가 기동 시 경고를 띄운다. 3m 직선 5회(좌·우 개별)와 **`δ_max` 원주행**(제자리 360° 회전은 전륜 조향에서 불가능하다 — §35-3 이 정한 대체 기동)으로 확정하기 전까지 거리·각도 **절대값을 신뢰하면 안 된다.**

## 모듈

센서 전용(옛 프레이밍, 그대로 유지):

- `packet_codec.py` — CRC-16/CCITT-FALSE, COBS, 프레임 build/parse, 메시지별 pack/unpack. `rclpy`를 import하지 않아 ROS 없이도 `pytest`로 검증할 수 있다(`test/test_packet_codec.py`).
- `protocol_constants.py` — 메시지 코드·fault bit·struct 포맷. `hardware/esp32/jetson-comm/message_ids.h`/`fault_codes.h`/`protocol.h`와 값이 반드시 동일해야 한다(수동 동기화).
- `serial_transport.py` — pyserial 래퍼. 백그라운드 스레드가 0x00 구분 청크를 큐에 담고, 포트가 없거나 끊기면 재연결을 계속 시도한다.

모터 전용(S15P11A301-321, 새 프레이밍):

- `motor_packet_codec.py` — 동기워드+고정길이+CRC8, 프레임 build/parse, 메시지별 pack/unpack. `rclpy`-free라 `pytest`로 검증한다(`test/test_motor_packet_codec.py`). `hardware/esp32/motor/esp32_motor_comm/motor_protocol.h`/`.cpp`와 값·바이트 배치가 반드시 동일해야 한다.
- `motor_protocol_constants.py` — 모터가 쓰는 메시지 코드·fault bit·struct 포맷. 메시지 코드 값 자체는 `protocol_constants.py`와 같다(프레이밍만 바뀌었지 메시지 종류는 안 바뀌었다).
- `motor_serial_transport.py` — `serial_transport.py`와 연결·재연결·DTR/RTS 하드닝은 동일하지만, 프레임 추출이 델리미터가 아니라 27바이트 슬라이딩 윈도우(동기워드+CRC8)다.

공통:

- `imu_clock.py` — 보드 monotonic 시계 → ROS 시각 오프셋 추정(min filter + 창 단위 재동기 + 재부팅 시 폐기). 역시 `rclpy`-free라 `pytest`로 검증한다(`test/test_imu_clock.py`). 센서 전용(모터는 새 프레이밍에 uptime 필드가 없어 이 방식의 재부팅 감지를 쓰지 않는다 - 대신 keepalive가 재부팅 직후에도 곧장 HELLO_ACK로 핸드셰이크를 재확인시킨다).
- `wheel_odometry.py` — 후륜 tick 정운동학·원호 적분·int32 tick 랩어라운드 처리. 역시 `rclpy`-free라 `pytest`로 검증하며(`test/test_wheel_odometry.py`), Phase 1에서 `sentinel_drive`가 그대로 가져다 쓸 수 있다.
- `diagnostics.py` — HELLO_ACK/DIAGNOSTIC → `diagnostic_msgs/DiagnosticArray` 변환. `RebootDetector`(`senderUptimeMs` 역행 감지)는 센서만 쓴다. `build_status()`의 `extra_values`는 모터가 `link_silence_ms`(링크 자체 생존 여부 - `DRIVE_COMMAND` 수신 빈도만 보는 `mode_arbiter`의 300ms 워치독과는 다른 축)를 덧붙이는 데 쓴다.

## 빌드·실행

```sh
cd jetson/ros2_ws
rosdep install --from-paths src/esp32_bridge --ignore-src -y
colcon build --symlink-install --packages-select esp32_bridge
source install/setup.bash

python3 -m pytest src/esp32_bridge/test/

# 하드웨어 연결 후 가장 먼저: raw HELLO/ACK 확인 (센서는 위, 모터는 아래 - 프레이밍이 다르다)
ros2 run esp32_bridge esp32_hello_check --port /dev/ttyUSB0
ros2 run esp32_bridge esp32_motor_hello_check --port /dev/ttyUSB1

# 두 노드 함께 실행 (udev 별칭이 없으면 실제 ttyUSB 경로를 넘긴다)
ros2 launch esp32_bridge esp32_bridge.launch.py \
    motor_port:=/dev/ttyUSB0 sensor_port:=/dev/ttyUSB1
```

## 열린 리스크

- udev 별칭(`/dev/sentinel_mcu_motor`, `/dev/sentinel_mcu_sensor`)이 이 저장소에 아직 없다. CP2102 클론 보드는 `idVendor:idProduct:serial`이 겹칠 수 있어 별칭만으로 역할을 보장할 수 없으므로, `HELLO_ACK.board_role`(핸드셰이크 로그의 role 불일치 오류)로 실제 역할을 확인하는 쪽을 우선 신뢰한다.
- 센서 ESP32 워치독을 먹일 주기적 Jetson→센서 트래픽이 프로토콜 표에 없어 HELLO 재전송을 keep-alive로 임시 채택했다 - `docs/03-제어-캘리브레이션.md` §34-7에 addendum 반영 필요.
- `HELLO_ACK`/`DIAGNOSTIC`/`COMMAND_ACK`/`CONFIG` 페이로드는 문서 §34-5에 없어 이번 작업에서 새로 정의했다(`protocol_constants.py`/`hardware/esp32/jetson-comm/src/protocol.h` 주석 참고) - 문서 addendum 필요.
- 오도메트리 캘리브레이션(TBD-CAL-001)이 전부 임시값이다. 특히 트랙폭 `W`는 실측 근거가 아예 없어 각속도 절대값이 무의미하다. 위 "오도메트리와 TF" 절 참고.
- `ultrasonic_front_link`(= `/range/front`의 `frame_id`)를 `sentinel_description`에 추가했지만 origin은 전부 0인 placeholder다. collision_monitor가 Range를 점으로 바꿀 때 이 TF를 쓰므로, 실측(§35-7, TBD-HW-006) 전까지 정지 거리가 센서 돌출량만큼 어긋난다.
