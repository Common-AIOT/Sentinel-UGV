# esp32_sensor_comm

센서 ESP32의 Jetson 통신 계층 스케치. `encoder/` 및 `total/` 아래 벤치 테스트 스케치(`mt6701_test_sample`, `mt6701_address_test`, `dual_mt6701_test_sample`, `total_mt6701_test`, `total_sensor_test`)는 이 스케치와 별개로 그대로 유지된다.

**빌드 전 필수**: `jetson-comm`을 Arduino 라이브러리로 설치해야 한다 — `../../jetson-comm/README.md`의 "설치" 절 참고(정션 없이 그냥 열면 링크 단계에서 undefined reference 오류가 난다).

## 범위

포함:
- `jetson-comm`(`<protocol.h>`) 기반 COBS+CRC16 프레이밍
- `HELLO`/`HELLO_ACK` 핸드셰이크, `CONFIG` "not implemented" 응답
- `ENCODER_STATE`(50Hz)/`IMU_STATE`(100Hz)/`ENVIRONMENT_STATE`(~1Hz)/`PROXIMITY_STATE`(~15Hz)/`DIAGNOSTIC`(5Hz) 송신(실측값)
- 300ms 통신 워치독(§34-7) — 로컬 액추에이터가 없어 정지 동작은 없고 `COMM_LOST`/`FAULT_COMM_TIMEOUT_SENSOR`만 보고
- MT6701 좌/우 구동 엔코더 I2C, MPU6050 차체 IMU I2C, DHT-11 1-wire 판독,
  HC-SR04 `pulseIn` 거리 측정(전방·후방 ×2)과
  로컬 `protective_stop` 판단 코드 — `sensor_task.cpp`. 현재는 전방 센서 오측 때문에
  `PROXIMITY_STOP_ENABLED=false`로 발동만 비활성화했다.
  엔코더는 **후륜 2개뿐**이며 `measured_steering_mdeg`는 항상 0으로 보고한다 —
  2026-08-06 전륜 조향이 복구됐지만 DS51150 서보가 내부 폐루프라 외부 각도
  피드백이 없어 조향은 개루프다(§6.3·34-5).
- 지속 오류 기준 `FAULT_DRIVE_ENCODER_FAULT`/`FAULT_IMU_SENSOR_FAULT`/
  `FAULT_PROXIMITY_SENSOR_FAULT`/`FAULT_ENVIRONMENT_SENSOR_FAULT` 판정과 `DEGRADED`
  상태 전이(환경/근접 한정)

포함하지 않음(후속 티켓):
- 엔코더 장거리·좌우·정역방향 재계측. 주기 수정 뒤 단거리 오도메트리는 줄자와
  3% 안에서 일치했지만, Jetson은 실물 바퀴 250mm를 쓰고 펌웨어 `speed_mmps`는
  아직 `WHEEL_DIAMETER_MM=120`을 사용하므로 보드 속도값은 통일이 필요하다.
- 초음파 보호 정책 복원 — 전방은 빈 공간 오측을 제거하고 15초 0회를 확인한 뒤에만
  플래그를 켠다. 후방은 방향별 임계·정지거리와 안전 체인 통합을 결정하기 전까지
  `rear_min_distance_mm`과 Jetson `/range/rear`로 값만 보고한다.
- IMU 축 정렬은 4기동으로 REP-103 항등 매핑이 맞음을 확인했다. 펌웨어 주기 수정 후
  bias·공분산·진동과 90°/360° 회전은 다시 측정한다.

Jetson 쪽 `IMU_STATE` 수신·`/imu/data_raw` 발행은 S15P11A301-244에서 구현됐다. `status_flags`
계약이 그쪽 발행 여부를 그대로 결정하므로 이 스케치를 고칠 때 함께 본다 — `BUS_ERROR`면
브리지가 발행을 멈추고(오래된 값을 새 측정으로 내보내지 않는다), `CALIBRATING`/`RANGE_ERROR`면
공분산을 크게 실어 EKF 융합에서 제외한다.

## 구조

- `esp32_sensor_comm.ino` — `xTaskCreatePinnedToCore`로 `comm_task`/`sensor_task`/`env_task` 생성.
- `board_state.h/.cpp` — 뮤텍스로 보호된 공유 상태(`SensorBoardState`, 실측 텔레메트리, fault 비트, 진단 카운터).
- `comm_task.h/.cpp` — Serial(921600bps) 프레임 파싱·디스패치, 텔레메트리 송신, 통신 워치독.
- `sensor_task.h/.cpp` — 실측과 fault 판정. §34-8("저주기 센서 대기가 IMU·엔코더 수집을
  막지 않는다")에 따라 두 태스크로 나뉜다.
  - `sensorTaskFn`(우선순위 2, 100Hz) — MT6701 좌/우 구동 엔코더 + MPU6050 IMU. I2C 전용.
  - `envTaskFn`(우선순위 1, 10ms tick) — HC-SR04(`pulseIn` 최대 30ms 블로킹) + DHT-11(~24ms).

## MPU6050 배선·설정

| MPU6050(GY-521) | ESP32 | 비고 |
|---|---|---|
| `VCC` | `3V3` | 3.3V 직결. 5V를 쓰면 모듈 pull-up이 5V로 올라가 ESP32 I2C 핀 정격을 넘는다 |
| `GND` | `GND` | |
| `SCL` | `GPIO22` | MT6701과 공유하는 기존 I2C 버스 |
| `SDA` | `GPIO21` | 동일 |
| `AD0` | `GND` | 주소 `0x68` 고정(모듈 pull-down 때문에 미결선도 `0x68`이지만 명시 결선 권장) |
| `INT`, `XDA`, `XCL` | 미결선 | 폴링 방식이라 `INT` 불필요, `XDA`/`XCL`는 보조 I2C용 |

- MT6701은 `0x06`/`0x46`이라 `0x68`과 겹치지 않고, 버스를 그대로 공유한다.
- GY-521 모듈은 SDA/SCL에 자체 pull-up(보통 4.7k~10k)을 달고 있다. MT6701 모듈 2개까지
  합쳐 병렬 저항이 낮아져 400kHz에서 I2C 오류가 잦으면 pull-up 한 벌을 떼거나
  `I2C_CLOCK_SPEED`를 100kHz로 낮춘다.
- 장착 방향이 REP-103(x 전방, y 좌측, z 상방)과 다르면 `sensor_task.cpp`의
  `IMU_AXIS_SOURCE`/`IMU_AXIS_SIGN`으로 맞춘다. 검증: 정지 상태에서 `accel_z ≈ +9.8`,
  차체를 왼쪽으로 회두시키면 `gyro_z > 0`.
- 부팅 후 2초간 정지 상태로 자이로 바이어스를 모으며 `status_flags = CALIBRATING`을
  보고한다. 이 구간 샘플은 EKF에 넣지 않는다(§34-5). 20°/s를 넘는 움직임이 보이면
  수집을 처음부터 다시 시작하므로, 흔들리는 동안에는 `CALIBRATING`이 유지된다.
- IMU를 붙이지 않고 이 펌웨어를 올리면 `FAULT_IMU_SENSOR_FAULT`가 계속 보고된다(1초
  주기로 재접속을 시도한다). 엔코더·환경·근접 스트림과 `DEGRADED` 판정에는 영향이 없다.

## HC-SR04 배선 (전방·후방)

| HC-SR04 | ESP32 | 비고 |
|---|---|---|
| `VCC` | `5V` | |
| `GND` | `GND` | |
| `TRIG`(전방) | `GPIO18` | 코드 배정. 실물 도통 재확인 필요 |
| `ECHO`(전방) | `GPIO39` | 5V 신호 — 분압/레벨 변환 필요(입력 전용 핀) |
| `TRIG`(후방) | `GPIO5` | 3.3V 출력 그대로 구동 가능 |
| `ECHO`(후방) | `GPIO36` | 5V 신호 — 분압/레벨 변환 필요(입력 전용 핀) |

- **2026-08-08 실배선 점검(S15P11A301-324): `GPIO5`/`GPIO36`에 물린 HC-SR04는
  실제로는 후방에 달려 있었다.** 센서 위치를 다시 바꾸기 어려워 코드의
  전방/후방 핀 배정을 맞바꿨다 — 핀 번호 자체(5/36, 18/39)는 그대로다.
- 두 센서는 같은 `envTaskFn` 틱(60ms, ~15-16Hz) 안에서 순차 측정한다 — 전방을
  완전히 측정(에코 종료 또는 30ms timeout)한 뒤에만 후방을 trigger해 서로의
  echo를 오독하지 않는다(02장 6.5·21.3).
- 후방 `rear_min_distance_mm`/`validSensorMask` bit1은 `PROXIMITY_STATE`로
  보고되고 Jetson이 `/range/rear`(`sensor_msgs/Range`)로 발행하지만,
  `protective_stop`에는 아직 반영되지 않는다 — 방향별 정지 임계 실측과 안전
  체인 통합은 TBD-HW-011·TBD-CAL-001로 남아 있다.
- 전방도 현재는 `PROXIMITY_STOP_ENABLED=false`라 거리 측정·발행만 하고
  `protective_stop`은 항상 0이다. 빈 공간에서 15초 218표본 중 9회 발생한
  2.6~5.5cm 오측을 제거하기 전에는 플래그를 복원하지 않는다(S15P11A301-353).
- 전방·후방 각각의 연속 실패는 공통 `FAULT_PROXIMITY_SENSOR_FAULT` 비트로
  보고된다(방향 구분 없음, 기존 비트를 그대로 확장).

## 워치독 관련 유의사항 (§8)

Jetson의 `esp32_sensor_bridge_node`는 `keepalive_period_s=0.15` 기본값으로 HELLO를 약
6.7Hz 재전송해 센서 보드의 300ms 통신 워치독을 유지한다. 포트가 끊기면 같은 타이머가
재연결을 계속 유도한다.

## 디버그 주의

모터 보드와 동일 — 이 UART는 바이너리 프로토콜 전용이므로 `comm_task`/`sensor_task`에 `Serial.print()` 디버그를 넣지 말 것.
