# esp32_bridge

모터·센서 ESP32와 USB 직렬(COBS+CRC16, 921600bps)로 통신하고, 센서 페이로드를 **표준 ROS 메시지**로 변환하는 ROS2 패키지 (S15P11A301-84). `/cmd_vel` 역운동학·안전 중재·BTS7960 제어 체인은 아직 README 스텁인 `sentinel_drive`/`sentinel_safety`의 몫이며 이번엔 만들지 않는다.

## 노드

- `esp32_motor_bridge` — `/dev/sentinel_mcu_motor`(기본값)를 열어 모터 ESP32와 통신. `~/drive_command`(std_msgs/String, JSON)를 구독해 `DRIVE_COMMAND`로 전송, `~/stop`/`~/estop` (`std_srvs/Trigger`) 서비스 제공, `~/drive_state`/`~/command_ack` 발행, `/diagnostics` 발행.
- `esp32_sensor_bridge` — `/dev/sentinel_mcu_sensor`(기본값)를 열어 센서 ESP32와 통신. 아래 "발행 토픽" 표대로 표준 메시지를 발행하고 `/diagnostics`에 보드 상태와 오도메트리 카운터를 낸다. HELLO를 ~6-7Hz로 keep-alive 재전송해 센서 보드의 300ms 통신 워치독에 입력을 공급한다(§34-7 gap-fill, README 하단 참고).
- `esp32_hello_check` — ROS 없이 포트를 열어 HELLO/HELLO_ACK만 확인하는 브링업 도구. 하드웨어를 막 연결했을 때 가장 먼저 실행할 것.

## 발행 토픽 (`esp32_sensor_bridge`)

| 프레임 | 토픽 | 타입 | 비고 |
|---|---|---|---|
| `ENCODER_STATE` | `/wheel/odometry` | `nav_msgs/Odometry` | `odom`→`base_footprint`, 차동 구동 정운동학 |
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
- **`measured_steering_mdeg`는 발행하지 않는다.** 조향 모터가 캐스터 휠로 대체되어 항상 0이다.
- **`sensor_msgs/Imu`(`/imu/data_raw`, 명세 23.2)는 아직 없다.** IMU 모델 미확정(TBD-HW-012)이고 센서 ESP32에 `IMU_STATE` 메시지 자체가 없다.

## 오도메트리와 TF

`ENCODER_STATE`의 좌·우 누적 tick으로 차동 구동 정운동학을 계산한다.

```
d_L = Δtick_L · meters_per_tick_L,  d_R = Δtick_R · meters_per_tick_R
v   = (d_R + d_L) / 2Δt ,  ω = (d_R − d_L) / WΔt
```

- **속도를 보드가 보낸 mm/s가 아니라 tick 차분에서 구한다.** 보드의 mm/s는 `sensor_task.cpp`에 하드코딩된 기어비·바퀴 지름으로 계산되므로, 캘리브레이션 값을 Jetson 파라미터 한 곳에만 두려면 tick이 유일한 입력이어야 한다. 재플래싱 없이 재튜닝된다.
- **Δt를 프레임 도착 시각이 아니라 `senderUptimeMs`에서 구한다.** USB 직렬 도착 지터가 그대로 속도 잡음이 되는 것을 막는다.
- **`publish_odom_tf`는 기본 `false`다.** 지금은 `slam.launch.py`가 `odom → base_footprint`를 static identity로 발행하고 있어(그쪽 docstring 참고) 동시에 켜면 두 발행자가 같은 TF를 다툰다. `publish_static_odom:=false`로 static을 끈 뒤 true로 올리거나, Phase 3에서 `ekf_node`가 `/odometry/filtered`로 TF를 소유하게 한 뒤 계속 false로 둔다.
- 보드가 재부팅하면 tick 카운터가 0으로 돌아간다. 기준점만 다시 잡고 **pose는 유지한다** — odom 프레임은 연속이어야 한다(REP-105). 재부팅 동안의 이동량은 유실된다.
- tick 점프(I2C 글리치)는 `max_wheel_speed_mps`로 걸러 `/diagnostics`의 `rejected_sample_count`로 센다.

> ⚠️ **캘리브레이션 값이 전부 임시값이다(TBD-CAL-001, §35-3).** `meters_per_tick`은 `sensor_task.cpp`의 실측 전 상수(바퀴 지름 120mm·기어비 82)에서 유도했고, 트랙폭 `W = 0.30`은 근거 없는 자리값이다. 그대로면 노드가 기동 시 경고를 띄운다. 3m 직선 5회(좌·우 개별)와 제자리 회전 역산으로 확정하기 전까지 거리·각도 **절대값을 신뢰하면 안 된다.**

## 모듈

- `packet_codec.py` — CRC-16/CCITT-FALSE, COBS, 프레임 build/parse, 메시지별 pack/unpack. `rclpy`를 import하지 않아 ROS 없이도 `pytest`로 검증할 수 있다(`test/test_packet_codec.py`).
- `wheel_odometry.py` — 차동 구동 정운동학·원호 적분·int32 tick 랩어라운드 처리. 역시 `rclpy`-free라 `pytest`로 검증하며(`test/test_wheel_odometry.py`), Phase 1에서 `sentinel_drive`가 그대로 가져다 쓸 수 있다.
- `protocol_constants.py` — 메시지 코드·fault bit·struct 포맷. `hardware/esp32/jetson-comm/message_ids.h`/`fault_codes.h`/`protocol.h`와 값이 반드시 동일해야 한다(수동 동기화).
- `serial_transport.py` — pyserial 래퍼. 백그라운드 스레드가 0x00 구분 청크를 큐에 담고, 포트가 없거나 끊기면 재연결을 계속 시도한다.
- `diagnostics.py` — HELLO_ACK/DIAGNOSTIC → `diagnostic_msgs/DiagnosticArray` 변환, `senderUptimeMs` 역행으로 보드 재부팅 감지.

## 빌드·실행

```sh
cd jetson/ros2_ws
rosdep install --from-paths src/esp32_bridge --ignore-src -y
colcon build --symlink-install --packages-select esp32_bridge
source install/setup.bash

python3 -m pytest src/esp32_bridge/test/

# 하드웨어 연결 후 가장 먼저: raw HELLO/ACK 확인
ros2 run esp32_bridge esp32_hello_check --port /dev/ttyUSB0

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
