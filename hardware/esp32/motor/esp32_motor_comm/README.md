# esp32_motor_comm

모터 ESP32의 Jetson 통신 계층 스케치. `motor_test`/`double_motor_test`/`triple_motor_test`(벤치 테스트용, BTS7960 Wi-Fi 수동 제어)는 이 스케치와 별개로 그대로 유지된다.

**빌드 전 필수**: `jetson-comm`을 Arduino 라이브러리로 설치해야 한다 — `../../jetson-comm/README.md`의 "설치" 절 참고(정션 없이 그냥 열면 링크 단계에서 undefined reference 오류가 난다).

## 범위

포함:
- `jetson-comm`(`<protocol.h>`) 기반 COBS+CRC16 프레이밍
- `HELLO`/`HELLO_ACK` 핸드셰이크 (E-Stop 래치 해제도 이 경로로 처리)
- `STOP_COMMAND`/`ESTOP_COMMAND` 수신과 `COMMAND_ACK` 응답
- 300ms 통신 워치독(§34-7) — 트립 시 `safety_stub.h`의 `applySafeOutputs()` 호출
- `DRIVE_STATE`(50Hz)/`DIAGNOSTIC`(5Hz) 송신(실측 PWM/driver-enable 값)
- BTS7960 2개(좌/우 구동)의 실제 PWM/DIR/EN 제어 — `safety_stub.cpp`. 조향 모터는
  캐스터 휠로 대체되어 제거되었고, `target_steering_mdeg`는 프로토콜 호환을 위해
  수신만 하고 액추에이션에는 쓰지 않는다.

포함하지 않음(후속 티켓):
- 전류/과열 등 실제 드라이버 fault 판독(현재는 통신·watchdog 기반 fault만 존재)
- mm/s → PWM 매핑의 실측 캘리브레이션(`MAX_DRIVE_SPEED_MMPS`, §35-4) — 현재는 임시값
- `vehicle_kinematics_node`/`safety_gate_node`/`command_mux_node` 등 Jetson 상위 로직

## 구조

- `esp32_motor_comm.ino` — `xTaskCreatePinnedToCore`로 `comm_task`/`control_task` 생성.
- `board_state.h/.cpp` — 뮤텍스로 보호된 공유 상태(`MotorBoardState`, 타깃 값, fault 비트, 진단 카운터).
- `comm_task.h/.cpp` — Serial(921600bps) 프레임 파싱·디스패치, 텔레메트리 송신.
- `control_task.h/.cpp` — 100Hz 워치독 검사.
- `safety_stub.h/.cpp` — BTS7960 2개(좌/우 구동) PWM/DIR/EN 실제 액추에이션.

## 디버그 주의

WROOM-32 온보드 USB-UART 브리지는 UART 하나뿐이며 지금 이 UART가 바이너리 프로토콜 전용이다. `Serial.print()` 텍스트 디버그를 comm_task/control_task에 추가하지 말 것 — 프레임에 섞이면 CRC 드롭으로 복구는 되지만 노이즈와 오탐 카운트를 유발한다. 상태 확인은 GPIO/LED로, 텍스트 로그가 꼭 필요하면 별도 핀의 `Serial2`를 컴파일 타임 매크로로 게이트해 사용할 것.
