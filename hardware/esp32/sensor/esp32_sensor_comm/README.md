# esp32_sensor_comm

센서 ESP32의 Jetson 통신 계층 스케치. `encoder/` 및 `total/` 아래 벤치 테스트 스케치(`mt6701_test_sample`, `mt6701_address_test`, `dual_mt6701_test_sample`, `total_mt6701_test`, `total_sensor_test`)는 이 스케치와 별개로 그대로 유지된다.

## 범위

포함:
- `../../jetson-comm/protocol.h` 기반 COBS+CRC16 프레이밍
- `HELLO`/`HELLO_ACK` 핸드셰이크, `CONFIG` "not implemented" 응답
- `ENCODER_STATE`(50Hz)/`ENVIRONMENT_STATE`(~1Hz)/`PROXIMITY_STATE`(~15Hz)/`DIAGNOSTIC`(5Hz) 송신(placeholder 값)
- 300ms 통신 워치독(§34-7) — 로컬 액추에이터가 없어 정지 동작은 없고 `COMM_LOST`/`FAULT_COMM_TIMEOUT_SENSOR`만 보고

포함하지 않음(후속 센서 판독 티켓):
- MT6701 I2C 실측, DHT-11 1-wire 판독, HC-SR04 `pulseIn` 거리 측정 — `sensor_task.cpp`가 그 훅
- 실제 초음파 임계거리 판단에 따른 `protective_stop` 로직

## 구조

- `esp32_sensor_comm.ino` — `xTaskCreatePinnedToCore`로 `comm_task`/`sensor_task` 생성.
- `board_state.h/.cpp` — 뮤텍스로 보호된 공유 상태(`SensorBoardState`, placeholder 텔레메트리, fault 비트, 진단 카운터).
- `comm_task.h/.cpp` — Serial(921600bps) 프레임 파싱·디스패치, 텔레메트리 송신, 통신 워치독.
- `sensor_task.h/.cpp` — 실측 대신 placeholder 값을 채우는 자리표시자.

## 워치독 관련 유의사항 (§8)

프로토콜 메시지 표에는 Jetson→센서 보드로 가는 주기적 트래픽이 `HELLO`/`CONFIG`뿐이라, Jetson 쪽 `esp32_sensor_bridge_node`가 이 보드의 워치독을 살아있게 하려면 `HELLO`를 ~5-10Hz로 keep-alive 삼아 재전송해야 한다(gap-fill, 문서 addendum 필요).

## 디버그 주의

모터 보드와 동일 — 이 UART는 바이너리 프로토콜 전용이므로 `comm_task`/`sensor_task`에 `Serial.print()` 디버그를 넣지 말 것.
