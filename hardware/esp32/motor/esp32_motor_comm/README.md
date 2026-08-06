# esp32_motor_comm

모터 ESP32의 Jetson 통신·액추에이션 스케치. `motor_test`/`double_motor_test`/`triple_motor_test`/`steering_servo_test`(벤치 테스트용 수동 제어)는 이 스케치와 별개로 그대로 유지된다.

**2026-08-06 하드웨어 변경**: 앞쪽 캐스터 2개를 제거하고 전륜 조향부(앞바퀴 2개·타이로드)를 복구했다. 후륜 DC 모터 2개는 **전·후진 전용**이 되고 조향은 전륜 DS51150-12V 서보 1개가 담당한다(02장 6.3, §34-2). 좌·우 속도 차로 회두를 만들지 않으며 **제자리 회전은 할 수 없다.**

**빌드 전 필수**: `jetson-comm`을 Arduino 라이브러리로 설치해야 한다 — `../../jetson-comm/README.md`의 "설치" 절 참고(정션 없이 그냥 열면 링크 단계에서 undefined reference 오류가 난다).

## 범위

포함:
- `jetson-comm`(`<protocol.h>`) 기반 COBS+CRC16 프레이밍
- `HELLO`/`HELLO_ACK` 핸드셰이크 (E-Stop 래치 해제도 이 경로로 처리)
- `STOP_COMMAND`/`ESTOP_COMMAND` 수신과 `COMMAND_ACK` 응답
- 300ms 통신 워치독(§34-7) — 트립 시 구동만 끊고(`applySafeOutputs()`) **조향각은
  마지막 목표를 유지한다**(CTRL-26)
- `DRIVE_STATE`(50Hz)/`DIAGNOSTIC`(5Hz) 송신(실측 PWM/driver-enable/조향 목표·서보 지령값)
- BTS7960 2개(후륜 좌·우 구동)의 PWM/DIR/EN 제어 — `safety_stub.cpp`. **전·후진 전용**이며
  비블로킹 방향 전환 데드타임(500ms)을 둔다
- 전륜 DS51150 조향 서보 PWM 생성 — `steering.cpp`. δ_max 클램프·슬루레이트 제한·
  부팅 중립·정지 중 조향 금지(§34-2)를 여기서 적용한다

포함하지 않음(후속 티켓):
- 전류/과열 등 실제 드라이버 fault 판독(현재는 통신·watchdog 기반 fault만 존재)
- mm/s → PWM 매핑의 실측 캘리브레이션(`MAX_DRIVE_SPEED_MMPS`, §35-4) — 현재는 임시값
- 조향 중립·엔드포인트·δ_max 실측(§35-3, TBD-HW-008) — `steering.cpp`의
  `SERVO_CENTER_DEG`/`SERVO_MAX_OFFSET_DEG`/`STEERING_MAX_MDEG`는 벤치 임시값이다.
  실측 절차는 `../steering_servo_test/`
- `STEERING_RESPONSE_MISMATCH`(§34-9 bit 15) 간접 판정 — IMU yaw rate 비교가 필요해
  Jetson 몫이며 아직 미구현
- `safety_gate_node`/`command_mux_node` 등 Jetson 상위 로직

## 구조

- `esp32_motor_comm.ino` — `xTaskCreatePinnedToCore`로 `comm_task`/`control_task` 생성.
- `board_state.h/.cpp` — 뮤텍스로 보호된 공유 상태(`MotorBoardState`, 타깃 값, fault 비트, 진단 카운터).
- `comm_task.h/.cpp` — Serial(921600bps) 프레임 파싱·디스패치, 텔레메트리 송신.
- `control_task.h/.cpp` — 100Hz 워치독 검사 + 방향 전환 데드타임 해제 + 조향 슬루레이트 적용.
- `safety_stub.h/.cpp` — BTS7960 2개(후륜 좌·우) PWM/DIR/EN 액추에이션(전·후진 전용).
- `steering.h/.cpp` — DS51150 조향 서보 PWM(GPIO18, 50Hz, LEDC 채널 4/별도 타이머).

## 조향 계층에서 반드시 지키는 것

| 규칙 | 근거 |
|---|---|
| 부팅 직후에만 중립(δ=0)으로 초기화한다 | 재부팅 후에는 믿을 수 있는 마지막 각도가 없다(§34-6) |
| 그 외 모든 정지 경로(STOP·ESTOP·워치독·종료)에서 조향각을 유지한다 | 정지는 정차가 아니다 — 관성 주행 중 중립으로 꺾으면 궤적이 바뀐다(§34-7) |
| ±δ_max 클램프를 펄스 출력 직전에 한 번 더 적용한다 | 어떤 코드 경로로 와도 서보가 링키지 기계 한계를 밀지 않아야 한다(§21.6) |
| `abs(v) < v_min`에서는 조향 목표 변경을 거부한다 | 회두가 생기지 않고 타이어·링키지·서보 stall 전류만 최악이 된다(§34-2) |
| 클램프·거부는 `STEERING_COMMAND_INVALID`(bit 14)로 보고한다 | 조향은 개루프라 이 비트가 유일한 진단 창구다(§34-9). 래치하지 않아 실시간 상태로 읽힌다 |

구동 PWM(20kHz)과 서보 PWM(50Hz)은 **LEDC 타이머를 공유하지 않는다.** 공유하면 한쪽
주파수를 바꿀 때 다른 쪽이 함께 흔들린다(§34-1). Core 2.x에서는 구동이 채널 0~3
(타이머 0·1), 서보가 채널 4(타이머 2)다.

## 디버그 주의

WROOM-32 온보드 USB-UART 브리지는 UART 하나뿐이며 지금 이 UART가 바이너리 프로토콜 전용이다. `Serial.print()` 텍스트 디버그를 comm_task/control_task에 추가하지 말 것 — 프레임에 섞이면 CRC 드롭으로 복구는 되지만 노이즈와 오탐 카운트를 유발한다. 상태 확인은 GPIO/LED로, 텍스트 로그가 꼭 필요하면 별도 핀의 `Serial2`를 컴파일 타임 매크로로 게이트해 사용할 것.
