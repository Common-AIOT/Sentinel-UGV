# ESP32-Jetson 통신 계층 테스트 절차 (S15P11A301-84)

`hardware/esp32/jetson-comm`·`hardware/esp32/motor/esp32_motor_comm`·`hardware/esp32/sensor/esp32_sensor_comm`·`jetson/ros2_ws/src/esp32_bridge`를 검증하는 단계별 절차. 1~2단계는 하드웨어 없이 지금 바로 실행할 수 있고, 3단계부터는 실제 ESP32 2개 + Jetson이 필요하다.

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

1단계와 동일한 벡터를 Python 쪽에서 재검증한다. C++/Python 두 구현이 바이트 단위로 같은 결과를 내는지 여기서 잡아낸다.

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

## 참고: 실제 하드웨어 없이도 전부 테스트 가능한 이유

`esp32_motor_comm`/`esp32_sensor_comm`은 이번 티켓 범위상 BTS7960 PWM 구동, MT6701/DHT-11/HC-SR04 실측 코드가 전혀 없다(`pinMode`/`Wire.`/`pulseIn` 호출 없음, 전부 `safety_stub.cpp`/`sensor_task.cpp`의 placeholder). 물리 E-Stop 배선도 참조하지 않는다. 따라서 ESP32 2개 + Jetson + USB 케이블 2개만 있으면 위 8단계를 전부 실행할 수 있고, 텔레메트리 값(엔코더 tick, 온습도, 거리 등)은 실측이 아닌 통신 검증용 가짜 값이다.
