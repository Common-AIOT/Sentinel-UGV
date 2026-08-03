# ESP32-Jetson 통신 계층 테스트 절차 (S15P11A301-84)

`hardware/esp32/jetson-comm`·`hardware/esp32/motor/esp32_motor_comm`·`hardware/esp32/sensor/esp32_sensor_comm`·`jetson/ros2_ws/src/esp32_bridge`를 검증하는 단계별 절차. 1~2단계는 하드웨어 없이 지금 바로 실행할 수 있고, 3단계부터는 실제 ESP32 2개 + Jetson이 필요하다. 3~8단계는 BTS7960·MT6701·HC-SR04·DHT-11이 물리적으로 연결되어 있지 않아도 통신 계층만으로 실행할 수 있지만(맨 아래 "참고" 절 참고), 9단계부터는 실제 센서·모터 배선이 필요하다.

## 1단계 — C++ 프로토콜 유닛테스트 (하드웨어 불필요)

```sh
cd hardware/esp32/jetson-comm
g++ -std=c++17 -I src test/test_protocol.cpp src/protocol.cpp -o test_protocol
./test_protocol
```

CRC16 표준 벡터(`0x29B1`), COBS 라운드트립, `DRIVE_COMMAND` 프레임 build/parse, CRC 손상 프레임 거부, 시퀀스 랩어라운드 비교까지 확인한다. 전부 `PASS`가 떠야 한다.

## 2단계 — Python packet_codec 유닛테스트 (하드웨어 불필요)

```sh
cd jetson/ros2_ws/src/esp32_bridge
python3 -m pytest test/ -v
```

1단계와 동일한 벡터를 Python 쪽에서 재검증한다. C++/Python 두 구현이 바이트 단위로 같은 결과를 내는지 여기서 잡아낸다. `test_wheel_odometry.py`가 함께 돌면서 차동 구동 정운동학(직진·제자리 회전·원호 적분·tick 랩어라운드·좌우 스케일 편차)도 검증한다.

## 3단계 — jetson-comm 라이브러리 설치 + 두 ESP32 플래싱

1. `jetson-comm`을 Arduino 라이브러리로 설치한다(`hardware/esp32/jetson-comm/README.md`의 "설치" 절 참고) — 스케치북 `libraries/` 아래에 저장소 경로를 가리키는 디렉터리 정션을 만든다.
   ```powershell
   mklink /J "%UserProfile%\Documents\Arduino\libraries\jetson_comm" "<repo>\hardware\esp32\jetson-comm"
   ```
   정션 없이 그냥 열면 헤더는 찾지만 `protocol.cpp`가 컴파일 대상에 안 들어가 링크 단계에서 `undefined reference`가 무더기로 난다.
2. Arduino IDE 재시작 후 보드 "ESP32 Dev Module" 선택.
3. `hardware/esp32/motor/esp32_motor_comm/esp32_motor_comm.ino` 업로드.
4. `hardware/esp32/sensor/esp32_sensor_comm/esp32_sensor_comm.ino` 업로드(다른 보드).
5. 시리얼 모니터는 열지 않는다 — 이 UART는 바이너리 프로토콜 전용이라 텍스트가 섞이면 CRC 오류로 잡힌다(정상 동작이지만 확인을 방해함).

## 4단계 — Jetson에서 포트 확인

```sh
ls /dev/ttyUSB* /dev/ttyACM*
dmesg | tail -30   # cp210x 또는 ch341 드라이버가 잡혔는지 확인
```

어느 포트가 모터/센서인지는 5단계에서 `HELLO_ACK.board_role`로 확정한다(udev 별칭은 아직 없음).

### 이 환경에서 실제로 겪은 문제와 해결 (Jetson Orin Nano, L4T 36.4.7 / 커널 5.15.148-tegra)

- **`ch341` 커널 모듈이 아예 없음** (`modprobe: FATAL: Module ch341 not found`): 이 L4T 커널에는 `CONFIG_USB_SERIAL_CH341`이 꺼져 있고 소스도 로컬 빌드 트리에 없다. 커널 버전과 정확히 일치하는 업스트림 소스를 받아 out-of-tree로 직접 빌드해서 해결했다.
  ```sh
  mkdir -p ~/ch341-build && cd ~/ch341-build
  wget https://raw.githubusercontent.com/gregkh/linux/v$(uname -r | sed 's/-tegra//')/drivers/usb/serial/ch341.c
  echo "obj-m += ch341.o" > Makefile
  make -C /lib/modules/$(uname -r)/build M=$(pwd) modules
  sudo insmod ch341.ko
  ```
  재부팅 후에도 유지하려면:
  ```sh
  sudo mkdir -p /lib/modules/$(uname -r)/kernel/drivers/usb/serial/
  sudo cp ch341.ko /lib/modules/$(uname -r)/kernel/drivers/usb/serial/
  sudo depmod -a
  ```
- **`brltty`(점자 디스플레이 접근성 데몬)가 CH340 장치를 낚아채 바로 disconnect시킴** (`dmesg`에 `usbfs: interface 0 claimed by ch341 while 'brltty' sets config #1` 직후 `now disconnected`): 이 로봇 Jetson에는 점자 디스플레이가 연결될 일이 없으므로 완전히 꺼서 해결.
  ```sh
  sudo systemctl stop brltty.service brltty-udev.service 2>/dev/null
  sudo systemctl disable brltty.service brltty-udev.service 2>/dev/null
  sudo systemctl mask brltty.service brltty-udev.service brltty-udev.path 2>/dev/null
  ```
- **`dialout` 그룹 미소속** 시 pyserial이 permission denied를 낸다.
  ```sh
  sudo usermod -aG dialout $USER   # 이후 재로그인 필요
  ```

## 5단계 — raw HELLO/ACK 확인 (ROS 빌드 전, 가장 먼저 해볼 것)

```sh
cd jetson/ros2_ws
rosdep install --from-paths src/esp32_bridge --ignore-src -y
colcon build --symlink-install --packages-select esp32_bridge
source install/setup.bash

ros2 run esp32_bridge esp32_hello_check --port /dev/ttyUSB0
ros2 run esp32_bridge esp32_hello_check --port /dev/ttyUSB1
```

각 포트에서 `HELLO_ACK 수신: role=MOTOR ...` / `role=SENSOR ...`가 출력되면 배선·플래싱·프레이밍이 전부 정상이다.

> **ESP32 auto-reset 회로 유의사항**: `pyserial`로 포트를 열면 기본적으로 DTR/RTS가 assert된 채로 남아 보드의 auto-reset 회로(RTS→EN, DTR→GPIO0)가 계속 리셋 상태를 유지시킬 수 있다. `SerialTransport`는 연결 직후 DTR/RTS를 명시적으로 해제하고 ESP-IDF 부팅이 끝날 때까지 짧게(약 1.5초) 대기한 뒤 그 사이 쌓인 ROM 부팅 배너 잔재(74880bps로 찍혀 921600bps로는 깨져 보임)를 버리도록 되어 있다. `esp32_hello_check`도 이 안정화 시간 동안 HELLO를 주기적으로 재전송하도록 만들어져 있으니, 첫 시도에 응답이 없어도 몇 초 더 기다려본다.

## 6단계 — 두 브리지 노드 실행 + 진단 확인

```sh
ros2 launch esp32_bridge esp32_bridge.launch.py \
    motor_port:=/dev/ttyUSB0 sensor_port:=/dev/ttyUSB1
```

다른 터미널에서:
```sh
ros2 topic echo /diagnostics
```

두 보드의 role/state/fault_flags/카운터가 주기적으로 찍히는지 확인한다.

## 7단계 — 300ms 워치독 트립 확인

```sh
ros2 topic pub -r 50 /esp32_motor_bridge/drive_command std_msgs/msg/String \
  "{data: '{\"mode\": 2, \"target_drive_left_mmps\": 100, \"target_drive_right_mmps\": 100}'}"
```

`ros2 topic echo /esp32_motor_bridge/drive_state`로 `state: AUTO_ACTIVE`, `fault_flags: 0` 확인 → Ctrl-C로 발행 중단 → 약 300ms 안에 `fault_flags`에 `COMM_TIMEOUT_MOTOR` 비트가 서고 `state`가 `STOPPING`으로 바뀌는지 확인한다.

`~/stop`, `~/estop` 서비스도 함께 확인:
```sh
ros2 service call /esp32_motor_bridge/stop std_srvs/srv/Trigger {}
ros2 service call /esp32_motor_bridge/estop std_srvs/srv/Trigger {}
```

estop 호출 후 `state: ESTOP_LATCHED`가 유지되다가, 노드를 재시작(HELLO 재전송)하면 `SAFE_IDLE`로 돌아오는지 확인한다(자동 주행 재시작은 없어야 함).

## 8단계 — CRC 드롭 / 오래된 시퀀스 거부 확인

브리지 노드를 거치지 않고 직접 손상된 프레임을 흘려보낸다.

```python
import serial
from esp32_bridge.packet_codec import build_frame
from esp32_bridge.protocol_constants import MSG_STOP_COMMAND

frame = bytearray(build_frame(MSG_STOP_COMMAND, sequence=1, sender_uptime_ms=0))
frame[-3] ^= 0xFF  # payload/crc 영역 손상 (마지막 바이트는 0x00 구분자)

port = serial.Serial("/dev/ttyUSB0", 921600)
port.write(bytes(frame))
```

이후 `/diagnostics`의 `crc_error_count`가 증가하고 모터 보드 상태는 변하지 않는지 확인한다. 오래된 시퀀스 재전송도 같은 방식으로 이미 처리된 시퀀스를 다시 보내 `stale_sequence_count` 증가를 확인하면 된다(§34-5 CTRL-05/CTRL-06 대응).

## 9단계 — 실제 센서·모터 동작 확인 (실물 배선 필요)

여기부터는 BTS7960 2개(좌/우 구동)·MT6701 2개(좌/우 구동 엔코더)·HC-SR04·DHT-11이
실제로 배선되어 있어야 한다. 조향 모터·조향 엔코더는 캐스터 휠로 대체되어
더 이상 존재하지 않는다. §34-10에 따라 **구동 바퀴를 바닥에서 띄운 상태**로
시작하고, 물리 E-Stop이 있다면 먼저 눌러 차단되는지부터 확인한다.

### 9-1. 엔코더 방향·응답 확인 (모터 정지 상태)

```sh
ros2 topic echo /wheel/odometry
ros2 topic hz /wheel/odometry     # 보드 송신 50Hz에 맞는지
```

- 좌측 바퀴를 손으로 전진 방향으로 돌리면 `twist.twist.linear.x`가 **양수**로,
  뒤로 돌리면 음수로 나오는지 확인한다. 우측도 동일하게 반복한다(§35-3, CTRL-07).
  반대로 나오면 `sensor_task.cpp`의 `LEFT_DIRECTION_SIGN`/`RIGHT_DIRECTION_SIGN`을
  뒤집는다(Jetson 파라미터가 아니라 보드 쪽에서 고친다 — tick 부호 정의가
  단일 진실이어야 한다).
- 우측 바퀴만 전진 방향으로 돌리면 `twist.twist.angular.z`가 **양수**(반시계,
  REP-103)여야 한다. 부호가 반대면 좌·우 배선이 바뀐 것이다.
- 정지 상태에서 `pose.pose.position`이 흐르지 않고 멈춰 있는지 확인한다
  (계속 흘러가면 I2C 노이즈다 — `/diagnostics`의 `rejected_sample_count`도 함께 본다).
- MT6701 커넥터를 하나 뽑아본다 → `/diagnostics`에 `DRIVE_ENCODER_FAULT` 비트가
  서는지, 재연결 후 ~1초 내 자동 복구되는지 확인한다.
- 노드를 재시작하지 않고 센서 ESP32만 리셋해본다 → `엔코더 기준점 재설정` 경고가
  뜨고, `pose`가 0으로 튀지 않고 **이어지는지** 확인한다(odom 연속성, REP-105).

> 거리·각도 **절대값**은 아직 보지 않는다. `meters_per_tick`·트랙폭 `W`가
> §35-3 실측 전 임시값이라 이 단계에서는 부호·응답성·연속성만 판정한다.
> 절대값 확인은 Phase 1의 3m 직선 5회·제자리 회전 역산에서 한다.

### 9-2. HC-SR04 근접 감지 확인

```sh
ros2 topic echo /range/front
ros2 topic echo /proximity/protective_stop
```

- 센서 앞에서 손이나 물체를 가까이/멀리 움직이며 `range`(단위 **m**)가 따라
  변하는지 확인한다.
- 아무것도 없는 방향을 향하면 `range: .inf`가 나오는 것이 정상이다 — 에코
  타임아웃을 "미검출"로 변환한 것이다(4m 지점의 장애물로 오인하지 않게 한다).
- 300mm(현재 `PROXIMITY_STOP_DISTANCE_MM` 임시값, §35 캘리브레이션 대상) 이내로
  접근하면 `/proximity/protective_stop`이 `true`로 바뀌는지 확인한다.
- 센서를 2cm 미만 초근접 상태로 오래 두거나 커넥터를 뽑아 노이즈/무응답을
  유도하면 `range`가 `.inf`로 바뀌고 잠시 후 `PROXIMITY_SENSOR_FAULT`가 서는지
  확인한다. 에코 무응답(먼 거리, 5m 밖)은 그 자체로는 fault가 아니라 "장애물
  없음"으로 처리되므로 fault가 서지 않는 것이 정상이다.
- `ros2 run tf2_ros tf2_echo base_footprint ultrasonic_front_link`로 TF가
  풀리는지 확인한다(`sentinel_description`이 떠 있어야 한다). 안 풀리면
  collision_monitor가 이 Range를 쓰지 못한다.

### 9-3. DHT-11 온습도 확인

```sh
ros2 topic echo /environment/temperature
ros2 topic echo /environment/relative_humidity
```

- `temperature`(°C)/`relative_humidity`(**0~1 비율**)가 주변 환경과 대략 맞는
  값인지 확인한다. 25.3/0.612 같은 고정값이 계속 나오면 아직 placeholder 빌드가
  올라가 있는 것이니 재플래싱한다.
- 센서를 손으로 감싸 온도를 올려보며 몇 초 내 값이 따라 움직이는지 확인한다.
- DATA 핀을 뽑아본다 → 판독 실패 상태에서는 두 토픽이 **아예 멈추고**(오래된
  값을 새 측정처럼 내보내지 않는다), 3회 연속 실패 후 `ENVIRONMENT_SENSOR_FAULT`가
  서고 센서 보드 `state`가 `DEGRADED`로 바뀌는지, 다시 꽂으면 `STREAMING`으로
  돌아오며 발행이 재개되는지 확인한다.

### 9-4. 실제 구동 확인 (바퀴를 반드시 띄운 상태)

```sh
ros2 topic pub -r 50 /esp32_motor_bridge/drive_command std_msgs/msg/String \
  "{data: '{\"mode\": 1, \"target_drive_left_mmps\": 150, \"target_drive_right_mmps\": 150}'}"
```

다른 터미널에서:

```sh
ros2 topic echo /esp32_motor_bridge/drive_state
```

- 좌·우 바퀴가 모두 같은 방향(전진)으로 도는지 눈으로 확인한다. 반대로 돌면
  `safety_stub.cpp`의 `LEFT_MOTOR_REVERSED`/`RIGHT_MOTOR_REVERSED`를 뒤집는다.
- `drive_pwm_left/right_permille`가 0이 아니고 `driver_enabled`가 `true`인지
  확인한다.
- 좌·우 부호를 반대로 보내(`target_drive_left_mmps: 150, target_drive_right_mmps: -150`)
  제자리 회전 방향이 기대와 맞는지 확인한다.
- 전진 중 즉시 반대 부호로 바꿔 보내(급후진) 드라이버 fault 없이 짧은
  dead-time 이후 반대 방향으로 전환되는지 확인한다(CTRL-13).
- Ctrl-C로 발행을 멈추고, 300ms 뒤 바퀴가 실제로 멈추는지 확인한다(7단계
  워치독 확인을 실제 PWM으로 재확인하는 것).

### 9-5. mm/s ↔ PWM, 거리 매핑은 아직 임시값

`safety_stub.cpp`의 `MAX_DRIVE_SPEED_MMPS`(현재 600), `sensor_task.cpp`의
`LEFT/RIGHT_GEAR_RATIO`(82.0)·`WHEEL_DIAMETER_MM`(120)·`PROXIMITY_STOP_DISTANCE_MM`
(300), `config/esp32_bridge.yaml`의 `meters_per_tick_left/right`·`track_width_m`은
전부 §35-3/§35-4 실측 전 임시값이다. 9단계에서는 부호·방향·응답성만
확인하고, 절대 속도(mm/s)나 절대 거리의 정확도는 실측 캘리브레이션 이후에
판단한다.

### 9-6. 오도메트리 캘리브레이션 (§35-3, Phase 1)

여기부터는 바퀴를 내리고 바닥에서 실제로 주행시킨다. 순서를 바꾸면 원인 구분이
불가능해진다(앞 단계가 틀린 채로 뒤 값을 맞추면 오차가 서로를 상쇄한다).

1. **거리 스케일** — 3m 직선을 좌·우 각각 5회 주행하고 `/wheel/odometry`의
   `pose.pose.position.x`와 줄자 실측을 비교한다. 합격 기준 오차 ±5%, 좌우 편차
   ±3%p. 어긋난 만큼 `meters_per_tick_left`/`meters_per_tick_right`를 개별로
   나눠 보정한다(좌우를 따로 두는 이유가 이 편차 보정이다).
2. **트랙폭 `W`** — 접지 중심 거리를 자로 재 `track_width_m`에 넣고, 제자리
   360° 회전을 좌·우 방향으로 각각 반복해 `pose`의 yaw가 실제 회전량과 맞는지
   본다. 각속도 대칭 오차 ±10% 이내가 될 때까지 `W`를 역산해 조정한다.
   **1번이 끝나기 전에는 손대지 않는다** — 거리 스케일이 틀리면 `W`도 반드시
   틀리게 나온다.
3. 확정값을 `config/esp32_bridge.yaml`에 기록하고, 기동 시 임시값 경고가 더
   이상 뜨지 않는지 확인한다. `docs/TBD.md`의 TBD-CAL-001도 함께 갱신한다.

---

## 참고: 1~8단계가 실제 하드웨어 없이도 되는 이유 / 지금은 달라진 부분

`safety_stub.cpp`(BTS7960 2개 PWM/DIR/EN)·`sensor_task.cpp`(MT6701 I2C, HC-SR04
`pulseIn`, DHT-11 1-wire)는 더 이상 placeholder가 아니라 실측 코드다(하드웨어
변경으로 조향 모터·조향 엔코더는 캐스터 휠로 대체되어 제거되고 좌·우 구동
2계통만 남았다). 그래도 1~8단계는 센서·액추에이터가 물리적으로 연결되어
있지 않아도 안전하게 실행된다 — 모터 ESP32는 미연결 BTS7960 방향으로 PWM을
내보낼 뿐이고, 센서 ESP32는 I2C/에코/DATA 핀에 응답이 없으면 offline·fault로
보고할 뿐이다. 다만 이제 텔레메트리 값(엔코더 tick, 온습도, 거리 등) 자체는
통신 검증용 가짜 값이 아니라 실측값이므로, 실제 센서·모터 동작까지
확인하려면 9단계가 필요하다. 물리 E-Stop 배선은 여전히 이 스케치의 범위
밖이다.
