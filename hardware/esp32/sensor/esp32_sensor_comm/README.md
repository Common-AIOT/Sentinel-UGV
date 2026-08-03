# esp32_sensor_comm

센서 ESP32의 Jetson 통신 계층 스케치. `encoder/` 및 `total/` 아래 벤치 테스트 스케치(`mt6701_test_sample`, `mt6701_address_test`, `dual_mt6701_test_sample`, `total_mt6701_test`, `total_sensor_test`)는 이 스케치와 별개로 그대로 유지된다.

**빌드 전 필수**: `jetson-comm`을 Arduino 라이브러리로 설치해야 한다 — `../../jetson-comm/README.md`의 "설치" 절 참고(정션 없이 그냥 열면 링크 단계에서 undefined reference 오류가 난다).

## 범위

포함:
- `jetson-comm`(`<protocol.h>`) 기반 COBS+CRC16 프레이밍
- `HELLO`/`HELLO_ACK` 핸드셰이크, `CONFIG` "not implemented" 응답
- `ENCODER_STATE`(50Hz)/`ENVIRONMENT_STATE`(~1Hz)/`PROXIMITY_STATE`(~15Hz)/`DIAGNOSTIC`(5Hz) 송신(실측값)
- 300ms 통신 워치독(§34-7) — 로컬 액추에이터가 없어 정지 동작은 없고 `COMM_LOST`/`FAULT_COMM_TIMEOUT_SENSOR`만 보고
- MT6701 좌/우 구동 엔코더 I2C 실측, DHT-11 1-wire 판독, HC-SR04 `pulseIn` 거리 측정과
  로컬 `protective_stop` 판단 — `sensor_task.cpp`. 조향 엔코더는 조향 모터가 캐스터
  휠로 대체되어 제거되었고, `measured_steering_mdeg`는 프로토콜 호환을 위해 항상 0으로
  보고한다.
- 지속 오류 기준 `FAULT_DRIVE_ENCODER_FAULT`/`FAULT_PROXIMITY_SENSOR_FAULT`/
  `FAULT_ENVIRONMENT_SENSOR_FAULT` 판정과 `DEGRADED` 상태 전이(환경/근접 한정)

포함하지 않음(후속 티켓):
- 엔코더 감속비·바퀴 지름, 초음파 안전거리 등 실측 캘리브레이션값(§35-3, §35-4) — 현재는 임시값
- 차체 IMU(`IMU_STATE`) 판독 — 별도 하드웨어·티켓 범위

## 구조

- `esp32_sensor_comm.ino` — `xTaskCreatePinnedToCore`로 `comm_task`/`sensor_task` 생성.
- `board_state.h/.cpp` — 뮤텍스로 보호된 공유 상태(`SensorBoardState`, 실측 텔레메트리, fault 비트, 진단 카운터).
- `comm_task.h/.cpp` — Serial(921600bps) 프레임 파싱·디스패치, 텔레메트리 송신, 통신 워치독.
- `sensor_task.h/.cpp` — MT6701(좌/우 구동)·DHT-11·HC-SR04 실측과 fault 판정.

## 워치독 관련 유의사항 (§8)

프로토콜 메시지 표에는 Jetson→센서 보드로 가는 주기적 트래픽이 `HELLO`/`CONFIG`뿐이라, Jetson 쪽 `esp32_sensor_bridge_node`가 이 보드의 워치독을 살아있게 하려면 `HELLO`를 ~5-10Hz로 keep-alive 삼아 재전송해야 한다(gap-fill, 문서 addendum 필요).

## 디버그 주의

모터 보드와 동일 — 이 UART는 바이너리 프로토콜 전용이므로 `comm_task`/`sensor_task`에 `Serial.print()` 디버그를 넣지 말 것.
