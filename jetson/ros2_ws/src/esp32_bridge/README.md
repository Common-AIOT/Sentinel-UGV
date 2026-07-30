# esp32_bridge

모터·센서 ESP32와 USB 직렬(COBS+CRC16, 921600bps)로 통신하는 ROS2 패키지 (S15P11A301-84). 범위는 통신 계층뿐이다 - 실제 BTS7960 PWM 제어·차량 기구학 변환·안전 중재는 아직 README 스텁인 `sentinel_control`/`sentinel_drive`/`sentinel_safety`의 몫이며 이번엔 만들지 않는다.

## 노드

- `esp32_motor_bridge` — `/dev/sentinel_mcu_motor`(기본값)를 열어 모터 ESP32와 통신. `~/drive_command`(std_msgs/String, JSON)를 구독해 `DRIVE_COMMAND`로 전송, `~/stop`/`~/estop` (`std_srvs/Trigger`) 서비스 제공, `~/drive_state`/`~/command_ack` 발행, `/diagnostics` 발행.
- `esp32_sensor_bridge` — `/dev/sentinel_mcu_sensor`(기본값)를 열어 센서 ESP32와 통신. `~/encoder_state`/`~/environment_state`/`~/proximity_state` 발행, `/diagnostics` 발행. HELLO를 ~6-7Hz로 keep-alive 재전송해 센서 보드의 300ms 통신 워치독에 입력을 공급한다(§34-7 gap-fill, README 하단 참고).
- `esp32_hello_check` — ROS 없이 포트를 열어 HELLO/HELLO_ACK만 확인하는 브링업 도구. 하드웨어를 막 연결했을 때 가장 먼저 실행할 것.

## 모듈

- `packet_codec.py` — CRC-16/CCITT-FALSE, COBS, 프레임 build/parse, 메시지별 pack/unpack. `rclpy`를 import하지 않아 ROS 없이도 `pytest`로 검증할 수 있다(`test/test_packet_codec.py`).
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
