# ESP32-Jetson 통신·센서 검증 절차 (S15P11A301-84, 244)

`hardware/esp32/jetson-comm`·`hardware/esp32/motor/esp32_motor_comm`·`hardware/esp32/sensor/esp32_sensor_comm`·`jetson/ros2_ws/src/esp32_bridge`를 검증하는 단계별 절차.

| 단계 | 필요한 것 |
|---|---|
| 1~2 | 없음. 지금 바로 실행할 수 있다 |
| 3~9 | 실제 ESP32 2개 + Jetson. 센서·모터가 배선되어 있지 않아도 된다 |
| 10~14 | BTS7960 2개·MT6701 2개·MPU6050·HC-SR04·DHT-11 실물 배선 |

## 이 문서의 판정 원칙

**값이 0이거나 비어 있는 것이 "고장"이 아닐 수 있고, 반대로 fault가 서지 않는 것이 "정상"의 증거가 아니다.** 이 시스템은 실패를 조용히 처리하는 지점이 여러 곳 있고, 그 대부분이 의도된 설계다. 그래서 모든 판정은 **토픽 값 + `/diagnostics` fault 비트를 함께** 읽어야 한다. 각 지점은 「부록 A 정상 실패 카탈로그」에 모아 두었으며, 실물 검증 중 헷갈리면 그 표를 먼저 본다.

센서별 관측 경로는 이렇다.

| 센서 | 보드 프레임 | 주기 | ROS 토픽 | fault 비트 |
|---|---|---|---|---|
| MT6701 좌/우 엔코더 | `ENCODER_STATE` | 50Hz | `/wheel/odometry` | `DRIVE_ENCODER_FAULT` |
| MPU6050 IMU | `IMU_STATE` (0x26) | 100Hz | `/imu/data_raw` | `IMU_SENSOR_FAULT` |
| HC-SR04 초음파 | `PROXIMITY_STATE` | ~15Hz | `/range/front`, `/proximity/protective_stop` | `PROXIMITY_SENSOR_FAULT` |
| DHT-11 온습도 | `ENVIRONMENT_STATE` | ~1Hz | `/environment/temperature`, `/environment/relative_humidity` | `ENVIRONMENT_SENSOR_FAULT` |
| 두 보드 공통 | `DIAGNOSTIC` | 5Hz | `/diagnostics` | — |

---

## 1단계 — C++ 프로토콜 유닛테스트 (하드웨어 불필요)

```sh
cd hardware/esp32/jetson-comm
g++ -std=c++17 -I src test/test_protocol.cpp src/protocol.cpp -o test_protocol
./test_protocol
```

CRC16 표준 벡터(`0x29B1`), COBS 라운드트립, `DRIVE_COMMAND` 프레임 build/parse, CRC 손상 프레임 거부, 시퀀스 랩어라운드 비교, 그리고 `IMU_STATE` 라운드트립(f32 리틀엔디안 비트 패턴, `u64 sample_time_us` 상위 바이트 보존, 짧은 페이로드 거부)까지 확인한다. 전부 `PASS`가 떠야 한다.

## 2단계 — Python packet_codec 유닛테스트 (하드웨어 불필요)

```sh
cd jetson/ros2_ws/src/esp32_bridge
python3 -m pytest test/ -v
```

1단계와 동일한 벡터를 Python 쪽에서 재검증한다. C++/Python 두 구현이 바이트 단위로 같은 결과를 내는지 여기서 잡아낸다. `IMU_STATE`(0x26)도 1단계와 같은 벡터로 확인한다 — 36바이트 길이, `-0.125f` = `0xBE000000` 비트 패턴, `u64 sample_time_us` 상위 바이트 보존, 짧은 페이로드 거부.

`test_wheel_odometry.py`가 함께 돌면서 후륜 tick 정운동학(직진·회전·원호 적분·tick 랩어라운드·좌우 스케일 편차)을, `test_imu_clock.py`가 보드 monotonic 시계 → ROS 시각 오프셋 추정(지터 속에서 min filter 수렴, 도착 지터가 스탬프 간격에 새지 않음, 드리프트 재동기, 재부팅 시 폐기, 수신 지연을 재부팅으로 오진하지 않음)을 검증한다.

## 3단계 — jetson-comm 라이브러리 설치 + 두 ESP32 플래싱

1. `jetson-comm`을 Arduino 라이브러리로 설치한다(`hardware/esp32/jetson-comm/README.md`의 "설치" 절 참고) — 스케치북 `libraries/` 아래에 저장소 경로를 가리키는 디렉터리 정션을 만든다.
   ```powershell
   mklink /J "%UserProfile%\Documents\Arduino\libraries\jetson_comm" "<repo>\hardware\esp32\jetson-comm"
   ```
   정션 없이 그냥 열면 헤더는 찾지만 `protocol.cpp`가 컴파일 대상에 안 들어가 링크 단계에서 `undefined reference`가 무더기로 난다.
2. Arduino IDE 재시작 후 보드 "ESP32 Dev Module" 선택.
3. `hardware/esp32/motor/esp32_motor_comm/esp32_motor_comm.ino` 업로드.
4. `hardware/esp32/sensor/esp32_sensor_comm/esp32_sensor_comm.ino` 업로드(다른 보드). 이 스케치는 태스크 **3개**를 만든다 — `comm_task`, `sensorTaskFn`(우선순위 2, 100Hz, I2C 전용: 엔코더 + IMU), `envTaskFn`(우선순위 1: HC-SR04 + DHT-11). 저주기 센서의 블로킹 판독이 IMU·엔코더 수집을 막지 않게 하려는 분리다(§34-8).
5. 시리얼 모니터는 열지 않는다 — 이 UART는 바이너리 프로토콜 전용이라 텍스트가 섞이면 CRC 오류로 잡힌다(정상 동작이지만 확인을 방해함).

## 4단계 — Jetson에서 포트 확인

```sh
ls /dev/ttyUSB* /dev/ttyACM*
dmesg | tail -30   # cp210x 또는 ch341 드라이버가 잡혔는지 확인
```

어느 포트가 모터/센서인지는 5단계에서 `HELLO_ACK.board_role`로 확정한다. **번호 순서로 추측하지 않는다** — CP2102 클론 보드는 `idVendor:idProduct:serial`이 겹칠 수 있어 udev 별칭으로도 역할을 보장할 수 없다. 라이다가 붙어 있으면 번호가 밀리고, 설정이 ESP32를 열고 있던 사고가 실제로 있었다(S15P11A301-173).

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

## 5단계 — raw HELLO/ACK 확인 (가장 먼저 해볼 것)

6단계 빌드가 끝난 뒤 실행한다. 순서상 여기 두는 이유는 **이것이 실패하면 뒤 단계를 볼 의미가 없기** 때문이다.

```sh
ros2 run esp32_bridge esp32_hello_check --port /dev/ttyUSB0
ros2 run esp32_bridge esp32_hello_check --port /dev/ttyUSB1
```

각 포트에서 `HELLO_ACK 수신: role=MOTOR ...` / `role=SENSOR ...`가 출력되면 배선·플래싱·프레이밍이 전부 정상이다. 나온 포트를 적어 두고 이후 단계에서 쓴다.

> **ESP32 auto-reset 회로 유의사항**: `pyserial`로 포트를 열면 기본적으로 DTR/RTS가 assert된 채로 남아 보드의 auto-reset 회로(RTS→EN, DTR→GPIO0)가 계속 리셋 상태를 유지시킬 수 있다. `SerialTransport`는 연결 직후 DTR/RTS를 명시적으로 해제하고 ESP-IDF 부팅이 끝날 때까지 짧게(약 1.5초) 대기한 뒤 그 사이 쌓인 ROM 부팅 배너 잔재(74880bps로 찍혀 921600bps로는 깨져 보임)를 버리도록 되어 있다. `esp32_hello_check`도 이 안정화 시간 동안 HELLO를 주기적으로 재전송하도록 만들어져 있으니, 첫 시도에 응답이 없어도 몇 초 더 기다려본다.

## 6단계 — ROS 워크스페이스 빌드와 환경 소싱

```sh
cd ~/projects/S15P11A301/jetson/ros2_ws
rosdep install --from-paths src/esp32_bridge --ignore-src -y
colcon build --symlink-install --packages-up-to sentinel_bringup
colcon build --symlink-install --packages-select esp32_bridge
source ~/projects/S15P11A301/scripts/ros_env.sh
```

### `ros_env.sh`를 쓴다. `install/setup.bash`만 source하지 않는다

`scripts/ros_env.sh`가 `/opt/ros/humble` + 워크스페이스 `install` + `ROS_DOMAIN_ID=97` + `ROS_LOCALHOST_ONLY=1`을 한 번에 처리하며, DDS 격리 값이 있는 유일한 곳이다(S15P11A301-218).

**이 값이 창마다 다르면 노드들이 서로를 못 본다. 그때 증상은 "노드가 안 뜬다"가 아니라 "오류 없이 토픽이 비어 있다"이고, 발행도 구독도 성공하므로 로그에 아무것도 남지 않는다.** 이 문서에서 진단하려는 센서 고장과 화면상 구별되지 않으므로, 터미널을 새로 열 때마다 반드시 source한다. 확인은 이렇게 한다.

```sh
env | grep -E 'ROS_DOMAIN_ID|ROS_LOCALHOST_ONLY'
```

부수 효과로 **노트북에서 `ros2` CLI로 젯슨 토픽을 보는 경로는 끊긴다.** `ros2` 명령은 전부 젯슨(SSH 안)에서 치고, 노트북에서는 Foxglove로 본다(11단계).

### 저장소 일부만 복사한 기기에서

검증용으로 `jetson/`만 따로 복사해 쓰는 경우가 있다. 그때 걸리는 것이 셋이다.

- **`install/`은 빌드 산출물이라 복사되지 않는다**(`.gitignore`). 복사한 기기에서 `colcon build`를 먼저 해야 생긴다.
- **`scripts/`는 `jetson/`의 하위가 아니라 저장소 루트의 형제 디렉터리다.** `jetson/`만 복사하면 `ros_env.sh`·`viz_up.sh`가 따라오지 않는다. `<대상>/scripts`에 함께 복사하면 `ros_env.sh`가 `../jetson/ros2_ws/install/setup.bash`를 계산하므로 경로가 그대로 맞는다.
- **Windows(NTFS)에서 복사하면 실행 비트가 사라진다.** `chmod +x <대상>/scripts/*.sh`를 한 번 실행한다. `ros_env.sh`는 source 전용이라 필요 없다.

`sentinel_bringup`(11단계의 `viz.launch.py`)까지 쓰려면 함께 빌드한다. `sentinel_description`을 빼면 안 된다 — `sentinel_bringup`이 그것을 `exec_depend`로 선언하고 있어 `--packages-select`로 둘만 고르면 colcon이 의존 패키지 `package.sh`를 못 찾고 멈춘다.

```sh
colcon build --symlink-install --packages-up-to esp32_bridge sentinel_bringup
```

전체 빌드(`colcon build`)는 하지 않는다. `usb_cam`·`ydlidar_ros2_driver`가 `sentinel.repos`로 `vcs import`해야 생기는 외부 소스라 복사본에는 없다.

## 7단계 — 두 브리지 노드 실행 + 진단 확인

```sh
ros2 launch esp32_bridge esp32_bridge.launch.py \
    motor_port:=/dev/ttyUSB0 sensor_port:=/dev/ttyUSB1
```

`센서 ESP32 핸드셰이크 완료: fw=... state=STREAMING`이 떠야 한다. 오도메트리 캘리브레이션 임시값 경고(`TBD-CAL-001`)는 정상이다.

다른 터미널에서:
```sh
ros2 topic echo /diagnostics
```

두 보드의 role/state/fault_flags/카운터가 5Hz로 찍히는지 확인한다. `hardware_id`로 보드를 구별한다(두 보드가 같은 토픽에 쓴다).

**데모 스택과 동시에 쓸 때**: `demo.launch.py`의 `enable_esp32`가 `false`면 `slam.launch.py`가 `odom→base_footprint`를 static identity로 발행하고 있다. 이 launch의 `publish_odom_tf`는 yaml 기본값 `false`이므로 발행자가 정확히 하나로 유지된다. **`publish_odom_tf:=true`를 주지 않는다** — 둘이 같은 TF를 다투어 위치가 흔들리고, 증상이 "지도가 이상하다"로만 보인다(S15P11A301-222).

센서 브리지가 두 개 떠 있지 않은지도 본다. 한 시리얼 포트를 두 프로세스가 읽으면 프레임이 갈려 CRC 오류만 쌓이는데, ROS 2는 같은 이름 노드를 목록에 한 번만 보여 줘서 `ros2 node list`로는 안 보인다.

```sh
pgrep -af esp32_sensor_bridge     # 한 줄이어야 한다
```

## 8단계 — 300ms 워치독 트립 확인

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

## 9단계 — CRC 드롭 / 오래된 시퀀스 거부 확인

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

---

## 10단계 — 센서 스트림이 흐르는지 확인 (실물 배선 필요)

여기부터 실물 배선이 필요하다. §34-10에 따라 **구동 바퀴를 바닥에서 띄운 상태**로 시작하고, 물리 E-Stop이 있다면 먼저 눌러 차단되는지부터 확인한다.

Foxglove로 가기 전에 CLI로 먼저 본다. 이 순서를 건너뛰면 값이 안 보일 때 보드 문제인지 Foxglove 설정 문제인지 구분할 수 없다.

```sh
ros2 topic hz /wheel/odometry     # 50Hz
ros2 topic hz /range/front        # ~15Hz
ros2 topic hz /diagnostics        # 두 보드 합산
ros2 topic echo /range/front | grep --line-buffered '^range:'
```

**주기가 규격과 일치하는 것은 링크가 살아 있다는 증거이지, 센서가 살아 있다는 증거가 아니다.** 센서가 죽어도 보드는 같은 주기로 계속 프레임을 보낸다. 판정은 아래 10-1~10-4에서 값과 fault를 함께 보고 한다.

`/diagnostics`가 가장 좋은 탐침이다. 200ms마다 무조건 오고, 센서 판독 성공 여부와 무관하다. 이것이 안 흐르면 시리얼 수신 자체가 죽은 것이므로 5단계로 돌아간다.

### 10-1. MT6701 엔코더

```sh
ros2 topic echo /wheel/odometry
```

- 좌측 바퀴를 손으로 전진 방향으로 돌리면 `twist.twist.linear.x`가 **양수**로, 뒤로 돌리면 음수로 나오는지 확인한다. 우측도 동일하게 반복한다(§35-3, CTRL-07). 반대로 나오면 `sensor_task.cpp`의 `LEFT_DIRECTION_SIGN`/`RIGHT_DIRECTION_SIGN`을 뒤집는다(Jetson 파라미터가 아니라 보드 쪽에서 고친다 — tick 부호 정의가 단일 진실이어야 한다).
- 우측 바퀴만 전진 방향으로 돌리면 `twist.twist.angular.z`가 **양수**(반시계, REP-103)여야 한다. 부호가 반대면 좌·우 배선이 바뀐 것이다.
- 정지 상태에서 `pose.pose.position`이 흐르지 않고 멈춰 있는지 확인한다(계속 흘러가면 I2C 노이즈다 — `/diagnostics`의 `rejected_sample_count`도 함께 본다).
- MT6701 커넥터를 하나 뽑아본다 → `/diagnostics`에 `DRIVE_ENCODER_FAULT` 비트가 서는지, 재연결 후 ~1초 내 자동 복구되는지 확인한다.
- 노드를 재시작하지 않고 센서 ESP32만 리셋해본다 → `엔코더 기준점 재설정` 경고가 뜨고, `pose`가 0으로 튀지 않고 **이어지는지** 확인한다(odom 연속성, REP-105).

**`DRIVE_ENCODER_FAULT: 1`이면 `/wheel/odometry`는 50Hz로 계속 오지만 값이 전부 0이다.** 채널이 offline이면 보드가 속도를 0으로 채워 보내기 때문이다. 바퀴를 돌려도 아무 변화가 없으므로, 이 fault를 먼저 해소한 뒤에 위 부호 판정을 한다. 좌·우 중 어느 쪽인지는 fault 비트로 구별되지 않으니(좌우 OR) 아래 스케치로 확인한다.

```text
hardware/esp32/sensor/encoder/mt6701_address_test/mt6701_address_test.ino
```

I2C 버스 전체를 스캔해 발견된 주소를 출력한다(이 스케치는 텍스트 출력이므로 시리얼 모니터를 열어도 된다). 확인 후 `esp32_sensor_comm.ino`로 되돌린다.

| 스캔 결과 | 해석 |
|---|---|
| 아무 주소도 없음 | SDA(GPIO21)·SCL(GPIO22)·3.3V·공통 GND 배선. 400kHz에서 ESP32 내부 풀업(약 45kΩ)은 대개 너무 약하다 — 외부 2.2~4.7kΩ 풀업 |
| `0x06`만 | 우측 엔코더가 `0x46`으로 설정되지 않았다. 같은 버스에서 주소가 겹치면 한쪽만 응답한다 |
| `0x06`·`0x46` 둘 다 | 배선은 정상. `dual_mt6701_test_sample.ino`로 실제 각도값까지 확인한다 |
| `0x68`이 함께 | MPU6050이 붙어 있다는 뜻이다(10-4) |

### 10-2. HC-SR04 근접 감지

```sh
ros2 topic echo /range/front
ros2 topic echo /proximity/protective_stop
```

- 센서 앞에서 물체를 가까이/멀리 움직이며 `range`(단위 **m**)가 따라 변하는지 확인한다.
- 300mm(현재 `PROXIMITY_STOP_DISTANCE_MM` 임시값, §35 캘리브레이션 대상) 이내로 접근하면 `/proximity/protective_stop`이 `true`로 바뀌는지 확인한다.
- `ros2 run tf2_ros tf2_echo base_footprint ultrasonic_front_link`로 TF가 풀리는지 확인한다(`sentinel_description`이 떠 있어야 한다). 안 풀리면 collision_monitor가 이 Range를 쓰지 못한다.

**`PROXIMITY_SENSOR_FAULT: 0`은 초음파가 정상이라는 증거가 아니다.** 보드는 에코 무응답을 "5m 밖에 장애물 없음"으로 해석해 **성공으로 처리하고** fail streak을 리셋한다(4m 지점 장애물로 오인하지 않으려는 의도된 설계). 그래서 센서가 완전히 죽어 있어도 fault가 서지 않고 15Hz로 계속 발행되며, 브리지는 그 값을 `+Inf`로 바꿔 내보낸다. 즉 **배선이 끊긴 센서와 "앞이 비어 있음"의 관측값이 같다.**

판정은 이렇게 한다.

| `range` | 해석 |
|---|---|
| 숫자가 손 움직임에 따라 변한다 | **정상.** 상수로는 만들 수 없는 값이므로 이것이 유일한 합격 판정이다 |
| 항상 `.inf` | 아래 배선 확인. 또는 정말 4m 안에 아무것도 없음 |
| 2cm 미만 초근접에서 `.inf` + 잠시 후 `PROXIMITY_SENSOR_FAULT` | 정상 동작(최소거리 미만 반사를 노이즈로 거른다) |

항상 `.inf`일 때 볼 곳(TRIG=GPIO5, ECHO=GPIO36):

1. **VCC가 5V인지.** 가장 흔한 원인이다. HC-SR04는 5V 부품이고 3.3V로는 송신 버스트가 제대로 나오지 않아 에코가 전혀 오지 않는다.
2. **GND 공통** — 별도 전원을 쓰면 반드시 묶는다.
3. **ECHO 레벨** — ECHO는 5V로 나오고 GPIO36은 3.3V 입력이다. 1kΩ/2kΩ 분압이 정석이다.
4. **측정 대상** — 손바닥은 각도가 조금만 틀어져도 에코가 되돌아오지 않는다. 30cm 앞에 책이나 벽 같은 **평면**을 두고 시험한다.

### 10-3. DHT-11 온습도

```sh
ros2 topic echo /environment/temperature
ros2 topic echo /environment/relative_humidity
```

- `temperature`(°C)/`relative_humidity`(**0~1 비율**)가 주변 환경과 대략 맞는 값인지 확인한다. 25.3/0.612 같은 고정값이 계속 나오면 아직 placeholder 빌드가 올라가 있는 것이니 재플래싱한다.
- 센서를 손으로 감싸 온도를 올려보며 몇 초 내 값이 따라 움직이는지 확인한다.
- DATA 핀을 뽑아본다 → 판독 실패 상태에서는 두 토픽이 **아예 멈추고**(오래된 값을 새 측정처럼 내보내지 않는다), 3회 연속 실패 후 `ENVIRONMENT_SENSOR_FAULT`가 서고 센서 보드 `state`가 `DEGRADED`로 바뀌는지, 다시 꽂으면 `STREAMING`으로 돌아오며 발행이 재개되는지 확인한다.

**두 토픽이 조용한 것은 그 자체로 진단이 되지 않는다.** 판독 실패 시 발행을 멈추는 것이 정상 동작이므로, `--once`로 기다리는 것은 진단으로 적합하지 않다. `/diagnostics`의 `ENVIRONMENT_SENSOR_FAULT`를 본다.

`ENVIRONMENT_SENSOR_FAULT: 1`이면 볼 곳(DATA=GPIO4):

1. 3.3V·GND·DATA 배선.
2. **풀업.** 코드가 `INPUT_PULLUP`(ESP32 내부 약 45kΩ)에만 의존하는데 DHT-11은 보통 4.7~10kΩ 외부 풀업을 요구한다. `readDht11`이 마이크로초 단위 타임아웃으로 엣지를 재기 때문에 상승 시간이 느리면 그대로 TIMEOUT이 된다. 3핀 모듈 보드라면 풀업이 이미 실려 있으니 배선·전압 쪽을 본다.

### 10-4. MPU6050 IMU

```sh
ros2 topic hz /imu/data_raw       # 100Hz보다 조금 낮게(중복 샘플 제거) 나오는 것이 정상
ros2 topic echo /imu/data_raw --once
```

**(a) I2C에 붙어 있는지** — 10-1의 `mt6701_address_test.ino` 스캔 결과에 `0x68`이 보이면 배선은 정상이다. MT6701 `0x06`/`0x46`과 겹치지 않으므로 GPIO21/22 버스를 그대로 공유한다.

| MPU6050(GY-521) | ESP32 | 비고 |
|---|---|---|
| `VCC` | `3V3` | **3.3V 직결.** 5V를 쓰면 모듈 pull-up이 5V로 올라가 ESP32 I2C 핀 정격을 넘는다 |
| `GND` | `GND` | |
| `SCL` / `SDA` | `GPIO22` / `GPIO21` | MT6701과 공유 |
| `AD0` | `GND` | 주소 `0x68` 고정 |
| `INT`, `XDA`, `XCL` | 미결선 | 폴링 방식이라 불필요 |

GY-521은 SDA/SCL에 자체 pull-up(4.7k~10k)을 달고 있다. MT6701 모듈 2개까지 합쳐 병렬 저항이 낮아져 400kHz에서 I2C 오류가 잦으면 pull-up 한 벌을 떼거나 `I2C_CLOCK_SPEED`를 100kHz로 낮춘다.

**(b) 값이 도는지** — 판정 기준은 축 정렬과 같다(아래 (d)).

| 관측 | 해석 |
|---|---|
| `linear_acceleration.z ≈ +9.8`, 나머지 ≈ 0 (정지) | 판독은 정상이다. 다만 정지 관측만으로는 축 부호가 확정되지 않으므로 (d)의 회두 시험까지 해야 합격이다 |
| 100Hz로 오는데 값이 전부 0 | 판독은 되는데 정지 중 `accel_z`가 0이다 — 중력이 어느 축에도 안 잡히는 상태이므로 축 매핑(`IMU_AXIS_SOURCE`)을 잘못 쓴 것이다 |
| **토픽이 아예 조용하다** | `BUS_ERROR` 또는 프레임 미수신. 아래 (c)로 간다 |

**(c) 조용할 때 — `/diagnostics`의 `esp32_bridge: IMU` 항목을 본다.** 발행이 멈추는 사유가 셋이고 토픽만 보면 구별되지 않는다.

| `esp32_bridge: IMU` | 해석 |
|---|---|
| `message: IMU_STATE 프레임 미수신`, `published_count: 0` | 시리얼 수신 자체가 안 되거나 펌웨어가 IMU_STATE를 안 보낸다. 5단계로 |
| `message: BUS_ERROR ...`, `skipped_bus_error_count`가 계속 증가 | I2C 판독 실패. **발행하지 않는 것이 정상 동작이다** — 보드가 마지막 값을 들고 있어 내보내면 오래된 값이 새 측정처럼 보인다. 배선은 (a)로 |
| `malformed_payload_count`가 0이 아니다 | 페이로드 길이가 36바이트가 아니다. 펌웨어와 브리지의 프로토콜 버전 불일치 |
| `level: WARN`, `message: 발행 중 - 공분산을 ...` | `CALIBRATING`(부팅 후 2초) 또는 clock offset 미안정(첫 ~200ms). 값은 나오지만 EKF는 융합하지 않는다 |
| `clock_resync_count`가 계속 오른다 | 센서 ESP32가 반복 재부팅하고 있다. `INTERNAL_WATCHDOG_RESET`도 함께 본다 |

`IMU_SENSOR_FAULT`(bit 13)는 이제 `/diagnostics`의 fault 키로 이름이 나온다. 예전에는 `FAULT_NAMES`에 bit 13이 없어 **IMU만 고장 났을 때 `level`만 ERROR로 오르고 나열된 fault 키는 전부 0**이었고, "이름 없는 비트가 서 있다"를 추론해야 했다. 그 추론은 이제 필요 없다.

IMU를 붙이지 않고 이 펌웨어를 올리면 `IMU_SENSOR_FAULT: 1` + `/imu/data_raw` 무발행 상태가 계속 유지된다(1초 주기로 재접속 시도). 엔코더·환경·근접 스트림과 `DEGRADED` 판정에는 영향이 없다.

**(d) 축 정렬** — `sensor_task.cpp`의 `IMU_AXIS_SOURCE`/`IMU_AXIS_SIGN`은 기판 실크스크린 축이 곧 전방/좌측/상방이라는 **가정의 항등 설정**이다. 이제 값을 볼 수 있으므로 여기서 확정한다(REP-103).

```sh
ros2 topic echo /imu/data_raw --once     # 정지 상태
```

- 정지 상태에서 `linear_acceleration.z ≈ +9.8`. 음수면 z가 뒤집혀 있고, `x`나 `y`에 9.8이 나오면 축이 치환되어 있다.
- 차체를 **왼쪽으로** 회두시키면 `angular_velocity.z > 0`.
- 앞으로 기울이면(pitch down) `angular_velocity.y`가 음수.

어긋나면 `IMU_AXIS_SOURCE`/`IMU_AXIS_SIGN`을 보드 쪽에서 고친다(엔코더 부호와 같은 원칙 — 축 정의의 단일 진실은 보드에 둔다). 정렬이 확정되기 전까지는 IMU를 EKF에 넣지 않는다.

부팅 후 2초간은 자이로 바이어스를 모으며 `CALIBRATING`이고, 이 구간의 메시지는 **공분산이 1e6으로 실려 나간다**(값은 보이지만 EKF가 융합하지 않는다). 20°/s를 넘는 움직임이 보이면 수집을 처음부터 다시 시작하므로 흔드는 동안에는 계속 `CALIBRATING`이다. **차체를 정지시킨 상태로 부팅한다.**

**(e) 타임스탬프 확인** — IMU 스탬프는 수신 시각이 아니라 보드가 보낸 `sample_time_us`를 변환한 값이다(§34-5). 변환이 어긋나면 EKF가 조용히 융합을 거부하므로 한 번 본다.

```sh
ros2 topic echo /imu/data_raw --field header.stamp --once   # 현재 시각과 수십 ms 이내여야 한다
```

`/diagnostics`의 `clock_offset_settled: True`이면 오프셋 추정이 안정된 상태다. 스탬프가 현재 시각보다 크게 과거/미래면 `clock_resync_count`를 함께 본다.

**(f) 회귀 확인** — IMU 100Hz가 추가되면서 링크 부하가 늘었고, 저주기 센서가 `envTaskFn`으로 분리됐다. 다음이 유지되어야 한다.

```sh
ros2 topic hz /wheel/odometry     # 여전히 50Hz
ros2 topic hz /range/front        # 여전히 ~15Hz
ros2 topic echo /diagnostics      # crc_error_count·dropped_frame_count가 0에서 안 오름
```

DHT-11 판독이 `noInterrupts()`로 약 4ms 동안 UART 수신을 막는데, 이제 그 구간이 IMU·엔코더 수집을 막지 않는다(태스크 분리). CRC 오류가 오르기 시작하면 그 분리가 깨진 것이다.

---

## 11단계 — Foxglove로 눈으로 보기

CLI로 확인한 것을 그래프로 본다. 손을 움직이며 곡선이 따라오는 것을 보는 것이 "실제 센서값이 ROS 노드로 전달된다"의 가장 직접적인 증명이다.

### 11-1. foxglove_bridge 설치 확인

apt로 따로 설치하는 패키지다. 없는 기기가 있고, 그때 `viz.launch.py`는 죽지 않고 로그만 남기고 시각화를 건너뛴다 — 화면에서는 "지도가 안 나온다"로만 보이므로 먼저 확인한다.

```sh
ros2 pkg prefix foxglove_bridge        # 경로가 나오면 설치돼 있다
sudo apt install -y ros-humble-foxglove-bridge
```

### 11-2. Bridge를 띄운다

**데모 스택이 돌고 있으면 이미 하나 떠 있다.** `demo.launch.py`의 `enable_viz` 기본값이 `true`이고, 그 인스턴스는 8765에 **6개 토픽 화이트리스트**(`/map`·`/pose`·`/scan`·`/tf`·`/tf_static`·`/robot_description`)로 붙어 있다. 여기에 센서 토픽이 없으므로 그대로 접속하면 목록에 하나도 안 뜬다. 확인은 이렇게 한다.

```sh
ros2 node list | grep foxglove        # /foxglove_bridge 가 있으면 이미 떠 있다
ss -tln | grep 876                    # 어느 포트를 쓰는지
```

8765를 끄면 관제 웹의 실시간 지도가 비므로(S15P11A301-224), **끄지 않고 다른 포트에 두 번째를 띄운다.** 화이트리스트가 9개 소형 토픽이라 CPU 비용은 무시할 수 있다 — 6개 토픽 bridge를 켠 실측에서 탐지 FPS 차이가 잡음 범위였다(5.90 대 5.80, 부호도 반대).

**방법 A — `viz_up.sh` (저장소 전체가 있는 기기, 권장)**

```sh
cd ~/projects/S15P11A301
./scripts/viz_up.sh --local \
    viz_port:=8766 \
    viz_tls:=false \
    viz_topic_whitelist:="['/wheel/odometry','/imu/data_raw','/range/front','/proximity/protective_stop','/environment/temperature','/environment/relative_humidity','/diagnostics','/tf','/tf_static']"
```

인자 셋 다 필요하다.

- **`--local`은 반드시 첫 번째 인자다** — `viz_up.sh`가 `$1`만 검사한다. 뒤에 두면 무시되고 `0.0.0.0`으로 열린다. 젯슨이 공인 IP에 있고 `foxglove_bridge`는 인증이 없으므로 그러면 인터넷에서 닿는다.
- **`viz_tls:=false`** — 기본값이 `true`이고 인증서를 `~/.config/sentinel/certs/server.{crt,key}`에서 찾는다. 그 파일이 없으면 bridge가 뜨지 못하고 "30초 안에 8766이 열리지 않았습니다"로 실패한다.
- **`viz_topic_whitelist`** — 없으면 센서 토픽이 광고조차 되지 않는다. `viz.launch.py`의 기본값은 관제 웹용 6개 토픽이라 센서 토픽이 하나도 들어 있지 않다.

`viz_up.sh`가 `ros_env.sh`를 스스로 source하고, 백그라운드로 띄우고, 포트가 실제로 열릴 때까지 기다린 뒤 결과를 낸다. 실패하면 `/tmp/sentinel-viz.log` 마지막 20줄을 보여 준다. 끄는 것은 `./scripts/viz_down.sh`다(`pkill -f foxglove_bridge`를 쓰면 그 패턴이 호출한 셸의 명령줄에 들어 있어 셸이 함께 죽는다).

**방법 B — `viz.launch.py` 직접**

`sentinel_bringup`은 빌드했지만 `scripts/`가 없는 기기(6단계의 부분 복사)에서 쓴다. 인자는 A와 같고, 포그라운드를 잡는다.

```sh
source <ws>/install/setup.bash && export ROS_DOMAIN_ID=97 ROS_LOCALHOST_ONLY=1
ros2 launch sentinel_bringup viz.launch.py \
    viz_address:=127.0.0.1 viz_port:=8766 viz_tls:=false \
    viz_topic_whitelist:="['/wheel/odometry','/imu/data_raw','/range/front','/proximity/protective_stop','/environment/temperature','/environment/relative_humidity','/diagnostics','/tf','/tf_static']"
```

**방법 C — `foxglove_bridge`를 직접**

`sentinel_bringup`을 빌드하지 않은 기기에서 쓴다. 기본 `topic_whitelist`가 `['.*']`라 **화이트리스트 인자가 필요 없고** 모든 토픽이 광고된다. 카메라·SLAM이 함께 돌지 않는 검증 환경에서는 이 편이 단순하다.

```sh
ros2 launch foxglove_bridge foxglove_bridge_launch.xml port:=8766 address:=127.0.0.1
```

`address:=127.0.0.1`을 반드시 준다. 이쪽은 `viz.launch.py`가 걸어 두는 읽기 전용 capabilities(`[connectionGraph]`)가 없어 기본값에 `clientPublish`·`parameters`·`services`가 들어 있다 — 접속만 하면 파라미터를 바꾸고 서비스를 부를 수 있다.

**어느 방법이든 기동 확인은 하나다.**

```sh
ss -tln | grep 8766        # 127.0.0.1:8766 이 나와야 한다
```

### 11-3. 노트북에서 접속한다

`--local`/`address:=127.0.0.1`로 띄웠으므로 SSH 터널로 본다. 이미 SSH를 쓰고 있다면 이것이 가장 확실하다 — WiFi IP·공유기 방화벽·공인 도메인의 헤어핀 NAT 같은 변수가 전부 없어진다.

```sh
ssh -N -L 8766:127.0.0.1:8766 orin@<젯슨 주소>
```

`-N`이라 아무 출력이 없는 것이 정상이고, 그 창은 열어 둔다.

Foxglove Studio 데스크톱 앱 → **Open connection**:

```text
연결 유형: Foxglove WebSocket      ← Rosbridge가 아니다
URL:      ws://localhost:8766
```

**Rosbridge를 고르면 핸드셰이크가 깨진다.** 그때 오류 문구에 `rosbridge`라는 단어가 들어가므로 그것으로 구별한다. 서버 로그에는 `Dropping client ...: handshake`가 남는다.

### 11-4. 패널을 만든다

접속 직후 좌측 토픽 목록에서 **`/tf`만 Hz가 뜨고 나머지가 `— —`인 것이 정상이다.** Foxglove는 **어떤 패널도 쓰지 않는 토픽을 구독하지 않는다.** 기본 레이아웃의 3D 패널이 `/tf`·`/tf_static`만 쓰기 때문이다. 패널에 경로를 걸면 그 순간 구독이 걸리고 수치가 나타난다.

기본 레이아웃의 Image 패널은 지운다("Waiting for image messages…"에서 영원히 안 바뀐다 — 카메라 토픽은 화이트리스트에 없고 영상은 관제 웹에서 본다). 3D 패널도 이 검증에는 쓸 일이 없다. Foxglove는 `sensor_msgs/Range`를 3D로 그리지 않는다.

### 11-5. Plot에 넣을 message path

**토픽명 뒤는 점이다**(`/range/front.range`). 슬래시로 쓰면(`/range/front/range`) 구독이 걸리지 않고 좌측 목록도 계속 `— —`로 남는다. 오타를 이 증상으로 구별한다.

#### MT6701 엔코더 — `/wheel/odometry` (nav_msgs/Odometry, 50Hz)

| message path | 단위 | 무엇을 보나 |
|---|---|---|
| `/wheel/odometry.twist.twist.linear.x` | m/s | **부호 판정의 주 지표.** 바퀴를 전진 방향으로 돌리면 양수 |
| `/wheel/odometry.twist.twist.angular.z` | rad/s | 우측만 전진으로 돌리면 양수(반시계, REP-103) |
| `/wheel/odometry.pose.pose.position.x` | m | 적분 위치. 정지 상태에서 흐르면 I2C 노이즈 |
| `/wheel/odometry.pose.pose.position.y` | m | 동일 |
| `/wheel/odometry.pose.pose.orientation.z` | — | 쿼터니언 성분(sin(yaw/2)). **각도가 아니다** — ±180° 안에서 yaw와 단조 증가하므로 "돌고 있는지"만 본다 |
| `/wheel/odometry.pose.covariance[0]` | — | x 분산. yaml 값(0.05)이 실제로 로드됐는지 확인 |
| `/wheel/odometry.pose.covariance[35]` | — | yaw 분산(0.1). 대각 성분은 0·7·35번이다 |

절대 거리·각도는 §35-3 실측 전이라 값의 크기를 신뢰하지 않는다(13단계). 이 단계에서 Plot으로 보는 것은 **부호와 응답성**이다.

#### MPU6050 IMU — `/imu/data_raw` (sensor_msgs/Imu, ~100Hz)

| message path | 단위 | 무엇을 보나 |
|---|---|---|
| `/imu/data_raw.angular_velocity.z` | rad/s | **축 정렬 판정.** 차체를 왼쪽으로 회두시키면 양수(REP-103) |
| `/imu/data_raw.linear_acceleration.z` | m/s² | **축 정렬 판정.** 정지 시 +9.8 |
| `/imu/data_raw.angular_velocity.x`, `.y` | rad/s | 정지 시 0 근처. 바이어스 보정이 끝났는지 |
| `/imu/data_raw.linear_acceleration.x`, `.y` | m/s² | 정지 시 0 근처. 기울어져 있으면 중력이 새어 들어온다 |
| `/imu/data_raw.angular_velocity_covariance[0]` | — | `1e6`이면 `CALIBRATING`/`RANGE_ERROR`거나 clock offset 미안정 상태다(값은 보이지만 EKF가 융합하지 않는다) |

**gyro와 accel을 한 Plot에 넣지 않는다.** accel_z가 9.8이고 gyro가 0.0x 대라 자이로 변화가 바닥에 눌려 안 보인다.

`orientation`은 MPU6050 원시 출력에 융합이 없어 채우지 않는다(`orientation_covariance[0] = -1`이 "추정값 없음"을 뜻한다) — Plot에 걸지 않는다.

**토픽이 조용하면 `BUS_ERROR`다**(10-4 (c)). Foxglove 좌측 목록의 Hz가 `— —`이면 패널 구독 문제와 구별되지 않으므로 `/diagnostics`의 `esp32_bridge: IMU`를 함께 띄워 둔다.

#### HC-SR04 초음파 — `/range/front` (sensor_msgs/Range, ~15Hz)

| message path | 단위 | 무엇을 보나 |
|---|---|---|
| `/range/front.range` | m | **합격 판정의 유일한 지표.** 손 움직임에 따라 변해야 한다. `+Inf`면 선이 안 그려진다(11-6) |
| `/range/front.min_range` | m | 0.02 고정. yaml `range_min_m`이 로드됐는지 |
| `/range/front.max_range` | m | 4.0 고정. yaml `range_max_m` |
| `/range/front.field_of_view` | rad | 0.26 고정. yaml `range_field_of_view_rad` |
| `/range/front.radiation_type` | — | 0(`ULTRASOUND`) 고정 |

아래 넷은 상수라 직선으로 그려진다. **값이 아니라 파라미터 로드 확인용**이며, `esp32_bridge.yaml`을 고쳤는데 반영이 안 될 때 여기서 잡는다.

#### 근접 정지 — `/proximity/protective_stop` (std_msgs/Bool, latched)

| 패널 | message path | 무엇을 보나 |
|---|---|---|
| **State Transitions** | `/proximity/protective_stop.data` | true/false 전이 시점이 막대로 보인다. 이 신호에 가장 맞는 패널이다 |
| Indicator | `/proximity/protective_stop.data` | 현재 상태만 크게 |
| Plot | `/proximity/protective_stop.data` | 0/1 계단으로 그려진다. `range`와 한 화면에 겹쳐 보고 싶을 때만 |

latched(`TRANSIENT_LOCAL`)라 접속하자마자 마지막 값이 한 번 온다. 그 뒤로는 상태가 바뀔 때만 오는 것이 아니라 초음파 갱신마다 계속 발행된다.

#### DHT-11 온습도 — `/environment/*` (~1Hz)

| message path | 단위 | 무엇을 보나 |
|---|---|---|
| `/environment/temperature.temperature` | °C | 손으로 감싸면 몇 초 내 올라간다 |
| `/environment/relative_humidity.relative_humidity` | **0~1 비율** | 퍼센트가 아니다. 60%면 0.6 |
| `/environment/temperature.variance` | — | 0 고정. DHT-11 분산 미측정으로 "모름"을 뜻한다 |

**판독 실패 시 두 토픽이 아예 발행되지 않으므로 Plot이 완전히 빈다.** 이것은 정상 동작이며 고장 판정은 `/diagnostics`로 한다(10-3).

#### 보드 상태 — `/diagnostics` (diagnostic_msgs/DiagnosticArray, 5Hz)

**기본은 Plot이 아니라 `Diagnostics – Detail (ROS)` 패널이다.** fault는 `values[]`의 `KeyValue`이고 `value`가 문자열이라 Plot이 그릴 수 없다.

Plot으로 볼 수 있는 것은 숫자인 `level` 하나다. 그런데 **모터·센서 두 보드가 같은 토픽에 쓴다.** `status[0]`으로 걸면 메시지마다 어느 보드인지 달라져 두 보드가 섞인 선이 나온다. 보드를 지정한다.

```text
/diagnostics.status[:]{name=="esp32_bridge: SENSOR"}.level
/diagnostics.status[:]{name=="esp32_bridge: MOTOR"}.level
```

`level`은 `0`=OK, `2`=ERROR다. **센서 보드 선이 0에서 2로 올라가는 순간이 fault 발생 시점**이므로, 커넥터를 뽑는 시험(10-1~10-3)에서 반응 시간을 재는 데 쓸 수 있다. 어느 fault인지는 Diagnostics 패널에서 읽는다.

#### Plot으로 볼 수 없는 것

| 토픽 | 왜 |
|---|---|
| `/esp32_motor_bridge/drive_state` | `std_msgs/String`에 담긴 JSON이다. Raw Messages 패널로 본다 |
| `/esp32_motor_bridge/command_ack` | 동일 |
| `/diagnostics`의 fault 비트 | `KeyValue.value`가 문자열이다. Diagnostics 패널로 본다 |

#### 패널 구성 요령

**단위가 다른 series를 한 Plot에 넣지 않는다.** `range`(0~4)와 `linear.x`(0~0.5)와 `temperature`(20~30)를 한 축에 겹치면 작은 쪽이 바닥에 눌려 변화가 안 보인다. 센서별로 패널을 나누고, 같은 단위끼리만 겹친다(예: `linear.x`와 `angular.z`는 둘 다 작은 값이라 함께 볼 수 있다).

### 11-6. Plot에 선이 안 그려질 때

**`+Inf`는 Plot에 그려지지 않는다.** 메시지가 15Hz로 도착하는데도 그래프가 완전히 비어 있으면 값이 `Inf`인 것이다. 원인은 10-2(초음파 미검출 또는 배선). 구독이 걸렸는지는 좌측 목록의 Hz 표시로 구별한다.

| 좌측 목록 | 해석 |
|---|---|
| `15.1 Hz`가 붙었는데 선이 없다 | 값이 `Inf`다 → 10-2 |
| 여전히 `— —` | 패널이 구독을 안 걸었다 → 경로 오타 |

값이 `Inf`인지 숫자인지는 CLI가 확실하다.

```sh
ros2 topic echo /range/front | grep --line-buffered '^range:'
```

### 11-7. 레이아웃을 저장한다

설정은 다음 접속에 사라진다. 우측 상단 레이아웃 드롭다운(기본 **Default**) → 다른 이름으로 저장한다(예: `esp-sensor-check`). 팀에 주려면 같은 드롭다운에서 **export** 해 JSON 파일로 전달한다. 레이아웃은 저장소가 아니라 Foxglove 계정에 저장되므로 `git pull`로는 전파되지 않는다.

---

## 12단계 — 실제 구동 확인 (뒷바퀴를 반드시 띄운 상태)

```sh
ros2 topic pub -r 50 /esp32_motor_bridge/drive_command std_msgs/msg/String \
  "{data: '{\"mode\": 1, \"target_drive_left_mmps\": 150, \"target_drive_right_mmps\": 150, \"target_steering_mdeg\": 0, \"max_steering_rate_mdps\": 60000}'}"
```

다른 터미널에서 `ros2 topic echo /esp32_motor_bridge/drive_state`.

- 좌·우 뒷바퀴가 모두 같은 방향(전진)으로 도는지 눈으로 확인한다. 반대로 돌면 `safety_stub.cpp`의 `LEFT_MOTOR_REVERSED`/`RIGHT_MOTOR_REVERSED`를 뒤집는다.
- `drive_pwm_left/right_permille`가 0이 아니고 `driver_enabled`가 `true`인지 확인한다.
- **좌·우 부호를 반대로 보내지 않는다.** 2026-08-06 전륜 조향 복구로 후륜은 전·후진 전용이며(§6.3), 부호가 반대인 명령은 펌웨어가 양쪽 0으로 만든다. 제자리 회전은 더 이상 존재하지 않는 기동이다.
- 전진 중 즉시 반대 부호로 바꿔 보내(급후진) 드라이버 fault 없이 데드타임(500ms) 이후 반대 방향으로 전환되는지 확인한다(CTRL-13). 데드타임 동안 `driver_enabled`가 `false`인 것이 정상이다.
- Ctrl-C로 발행을 멈추고, 300ms 뒤 바퀴가 실제로 멈추는지 확인한다(8단계 워치독 확인을 실제 PWM으로 재확인하는 것).

## 12-2단계 — 조향 서보 확인 (앞바퀴도 띄운 상태, CTRL-24·25·26)

조향 중립·엔드포인트를 아직 잡지 않았다면 먼저 `hardware/esp32/motor/steering_servo_test/`
벤치 스케치로 §35-3 절차를 끝낸다. 여기서는 통합 경로(`drive_command` → 서보)만 본다.

```sh
# 좌 15° 조향 + 전진. v_min(30mm/s) 이상이어야 조향 목표가 반영된다
ros2 topic pub -r 50 /esp32_motor_bridge/drive_command std_msgs/msg/String \
  "{data: '{\"mode\": 1, \"target_drive_left_mmps\": 100, \"target_drive_right_mmps\": 100, \"target_steering_mdeg\": 15000, \"max_steering_rate_mdps\": 60000}'}"
```

- `drive_state`의 `target_steering_mdeg`가 15000 으로 **서서히** 수렴하는지 본다. 계단으로 뛰면 슬루레이트가 안 걸린 것이다(CTRL-25). `steering_actuator_cmd`는 서보 펄스폭(µs)이다.
- `+15000`에서 앞바퀴가 **좌**로 꺾이는지 눈으로 본다. 반대면 `steering.cpp`의 `SERVO_DIRECTION_SIGN`을 `-1`로 바꾼다 — 부호가 뒤집힌 채 자율주행에 들어가면 회피가 장애물 쪽으로 향한다.
- `±30000`을 넘는 값(예: 40000)을 보내 `drive_state`가 30000에서 포화하고 `/diagnostics`에 `STEERING_COMMAND_INVALID`(bit 14)가 서는지 확인한다(CTRL-14). 정상 값으로 되돌리면 즉시 내려간다 — 래치하지 않는다.
- **정지 상태(`target_drive_*_mmps: 0`)에서 조향각을 바꿔 보낸다.** 서보가 움직이지 않고 `STEERING_COMMAND_INVALID`가 서면 정상이다(§34-2 — 정지 조향은 회두를 만들지 못하고 타이어·서보에만 부담이다).
- 조향을 준 상태에서 Ctrl-C로 발행을 멈춘다. 구동은 300ms 안에 멈추고 **조향각은 그대로 유지**돼야 한다(CTRL-26, §34-7). 중립으로 튀면 정지 경로 어딘가가 조향을 건드리는 것이다.

## 13단계 — mm/s ↔ PWM, 조향 매핑, 거리 매핑은 아직 임시값

`safety_stub.cpp`의 `MAX_DRIVE_SPEED_MMPS`(현재 600), `steering.cpp`의 `SERVO_CENTER_DEG`(145)·`SERVO_MAX_OFFSET_DEG`(30)·`STEERING_MAX_MDEG`(30000), `sensor_task.cpp`의 `LEFT/RIGHT_GEAR_RATIO`(82.0)·`WHEEL_DIAMETER_MM`(120)·`PROXIMITY_STOP_DISTANCE_MM`(300), `config/esp32_bridge.yaml`의 `meters_per_tick_left/right`·`track_width_m`, `sentinel_drive`의 `wheelbase_m`(0.50)·`max_steering_deg`(30)은 전부 §35-3/§35-4 실측 전 임시값이다. 10~12단계에서는 부호·방향·응답성만 확인하고, 절대 속도(mm/s)·조향각·절대 거리의 정확도는 실측 캘리브레이션 이후에 판단한다.

조향은 개루프라 **매핑 정확도가 곧 조향 정확도**이며(§34-8), 실제 조향각을 재는 센서가 없어 이 값이 틀려도 시스템은 아무 오류도 내지 않는다.

## 14단계 — 오도메트리 캘리브레이션 (§35-3, Phase 1)

여기부터는 바퀴를 내리고 바닥에서 실제로 주행시킨다. 순서를 바꾸면 원인 구분이 불가능해진다(앞 단계가 틀린 채로 뒤 값을 맞추면 오차가 서로를 상쇄한다).

1. **거리 스케일** — 3m 직선을 좌·우 각각 5회 주행하고 `/wheel/odometry`의 `pose.pose.position.x`와 줄자 실측을 비교한다. 합격 기준 오차 ±5%, 좌우 편차 ±3%p. 어긋난 만큼 `meters_per_tick_left`/`meters_per_tick_right`를 개별로 나눠 보정한다(좌우를 따로 두는 이유가 이 편차 보정이다).
2. **휠베이스 `L`과 `δ_max`** — 전·후 차축 중심 거리를 자로 재 `sentinel_drive`의 `wheelbase_m`에 넣고, 조향을 `δ_max`로 고정한 채 저속으로 원을 한 바퀴 돌려 `R_min`을 실측한다(§35-3). 좌·우 각각 5회, 대칭 오차 ±10% 이내. **제자리 360° 회전은 전륜 조향에서 불가능하므로 이 원주행이 대체 기동이다.** 트랙폭 `W`(`track_width_m`)는 오도메트리 yaw 계산에 계속 쓰이지만 그 yaw 는 EKF 입력이 아니다 — 후륜 스크럽 때문에 신뢰할 수 없고 yaw 의 주 소스는 IMU 다(§35-3). **1번이 끝나기 전에는 손대지 않는다** — 거리 스케일이 틀리면 `L`·`R_min` 역산도 반드시 틀리게 나온다.
3. 확정값을 `config/esp32_bridge.yaml`에 기록하고, 기동 시 임시값 경고가 더 이상 뜨지 않는지 확인한다. `docs/TBD.md`의 TBD-CAL-001도 함께 갱신한다.

---

## 부록 A — 정상 실패 카탈로그

**이 표의 왼쪽 증상은 전부 "고장처럼 보이지만 의도된 동작"이다.** 실물 검증에서 시간을 잃는 지점이 거의 다 여기 있다.

| 증상 | 실제 의미 | 어디서 판정하나 |
|---|---|---|
| `/wheel/odometry`가 50Hz인데 값이 전부 0 | 엔코더 offline. 보드가 속도를 0으로 채워 보낸다 | `DRIVE_ENCODER_FAULT` |
| `/range/front`가 항상 `.inf`, fault는 0 | 에코 무응답을 "5m 밖 장애물 없음"으로 처리한다. 센서가 죽어도 fault가 안 선다 | 손 움직임에 값이 반응하는지 |
| `/environment/*`가 아예 발행되지 않음 | DHT 판독 실패 시 오래된 값을 새 측정처럼 내보내지 않는다 | `ENVIRONMENT_SENSOR_FAULT` |
| `/imu/data_raw`가 100Hz보다 조금 낮다 | 같은 `sample_time_us`가 두 번 온 것을 버린다. 같은 스탬프 두 개는 EKF가 한 측정을 두 번 세는 것이 된다 | 10-4 |
| `/imu/data_raw`가 아예 발행되지 않음 | `BUS_ERROR`. 판독 실패 시 보드가 들고 있는 마지막 값을 새 측정처럼 내보내지 않는다 | `/diagnostics`의 `esp32_bridge: IMU` |
| IMU 공분산이 `1e6` | `CALIBRATING`(부팅 2초) 또는 clock offset 미안정. 값은 보여 주고 EKF 융합만 막는다 | 10-4 (c) |
| Foxglove 토픽 목록이 `— —` | 어떤 패널도 그 토픽을 구독하지 않는다 | 11-4 |
| Foxglove Plot이 완전히 비어 있음 | 값이 `+Inf`다. Plot은 Infinity를 그리지 않는다 | 11-6 |
| Foxglove에 센서 토픽이 아예 없음 | bridge 화이트리스트에 없다 | 11-2 |
| `/environment/*` Plot이 계속 빔 | 판독 실패 시 발행하지 않는다 | 10-3 |
| `/diagnostics`를 Plot에 걸었는데 선이 두 값 사이를 튄다 | 모터·센서가 같은 토픽에 쓴다. `status[:]{name==...}`로 보드를 지정한다 | 11-5 |
| `/esp32_motor_bridge/drive_state`가 Plot에 안 걸린다 | JSON 문자열이다. Raw Messages로 본다 | 11-5 |
| 토픽이 전부 비어 있고 로그도 조용함 | 터미널 간 `ROS_DOMAIN_ID`/`ROS_LOCALHOST_ONLY` 불일치 | 6단계 |
| 브리지 노드는 살아 있는데 데이터만 안 옴 | 포트를 못 열었다. 1초마다 재시도하며 토픽은 계속 광고한다 | 7단계 로그의 `not available ... retrying` |
| 부팅 직후 IMU가 `CALIBRATING` | 자이로 바이어스 2초 수집. 흔들면 처음부터 다시 시작한다 | 10-4 |

## 부록 B — 1~9단계가 실물 센서 없이 되는 이유

`safety_stub.cpp`(BTS7960 2개 PWM/DIR/EN)·`sensor_task.cpp`(MT6701 I2C, MPU6050 I2C, HC-SR04 `pulseIn`, DHT-11 1-wire)는 placeholder가 아니라 실측 코드다. 그래도 1~9단계는 센서·액추에이터가 물리적으로 연결되어 있지 않아도 안전하게 실행된다 — 모터 ESP32는 미연결 BTS7960 방향으로 PWM을 내보낼 뿐이고, 센서 ESP32는 I2C/에코/DATA 핀에 응답이 없으면 offline·fault로 보고할 뿐이다.

다만 텔레메트리 값 자체는 통신 검증용 가짜 값이 아니라 실측값이므로, 실제 센서·모터 동작까지 확인하려면 10단계 이후가 필요하다. 물리 E-Stop 배선은 여전히 이 스케치의 범위 밖이다.

하드웨어 변경 이력: 조향 모터(RS380SP)·조향 엔코더는 2026-08-01에 캐스터 휠로 대체되어 제거되었고, 차체 IMU로 MPU6050이 추가되었다(S15P11A301-244). **2026-08-06에 캐스터를 다시 떼고 전륜 조향부를 복구해 DS51150-12V 서보 1개를 모터 ESP32의 PWM 1채널(GPIO18)에 연결했다**(S15P11A301-297) — 후륜 2개는 전·후진 전용이 되고 조향은 서보가 담당한다. 조향 엔코더는 돌아오지 않았으므로 `measured_steering_mdeg`는 여전히 항상 0이다(서보 내부 폐루프, 외부 각도 피드백 없음).
