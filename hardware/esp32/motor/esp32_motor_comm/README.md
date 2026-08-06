# esp32_motor_comm

모터 ESP32의 **유일한 운영 스케치**. 명령 소스가 둘이고 이 보드가 중재자다.

```
젯슨  ── USB 직렬 921600, COBS+CRC16 ──┐
                                       ├→ control_task (100Hz, 유일한 액추에이터)
폰    ── 핫스팟 WiFi, HTTP 20Hz ───────┘      → 후륜 BTS7960 2개 + 전륜 조향 서보
```

`motor_test`/`double_motor_test`/`triple_motor_test`/`steering_servo_test`(벤치 테스트용 수동 제어)는 이 스케치와 별개로 그대로 유지된다.

**2026-08-06 하드웨어 변경**: 앞쪽 캐스터 2개를 제거하고 전륜 조향부(앞바퀴 2개·타이로드)를 복구했다. 후륜 DC 모터 2개는 **전·후진 전용**이 되고 조향은 전륜 DS51150-12V 서보 1개가 담당한다(02장 6.3, §34-2). 좌·우 속도 차로 회두를 만들지 않으며 **제자리 회전은 할 수 없다.**

**S15P11A301-298**: `tank_drive` 스케치를 여기에 병합하고 **삭제했다.** 두 스케치는 같은 핀·같은 LEDC 채널을 쓰는 배타 스케치였고, 남겨 두면 잘못 굽는 순간 강직된 전륜 링키지를 거슬러 차동 선회를 시도한다. 수동 조종 웹 UI 는 `control_page.h` 로 옮겨 왔다.

**빌드 전 필수**:
1. `jetson-comm`을 Arduino 라이브러리로 설치한다 — `../../jetson-comm/README.md`의 "설치" 절 참고(정션 없이 그냥 열면 링크 단계에서 undefined reference 오류가 난다).
2. **`manual_web_config.h` 의 SSID/비밀번호를 자기 폰 핫스팟 값으로 바꾼다.** 플레이스홀더 그대로 구우면 접속만 실패하고 부팅은 정상 진행되므로(비블로킹) 조용히 수동 채널만 죽는다. 이 파일을 커밋하지 말 것.

## 범위

포함:
- `jetson-comm`(`<protocol.h>`) 기반 COBS+CRC16 프레이밍
- `HELLO`/`HELLO_ACK` 핸드셰이크 (E-Stop 래치 해제도 이 경로로 처리). **수동 래치는
  건드리지 않는다** — 젯슨 프로세스 재시작이 조작 중인 사람에게서 바퀴를 빼앗아선 안
  되고, 진짜 보드 리부트는 RAM 이 날아가 래치가 함께 사라진다
- `STOP_COMMAND`/`ESTOP_COMMAND`/`SET_MODE` 수신과 `COMMAND_ACK` 응답
- **액추에이션 중재**(`mode_arbiter.cpp`) — 수동 승격(2패킷·100ms), TTL(250ms),
  자동 전환 500ms 신선도 가드, 바퀴 소유자 결정
- **수동 조종 HTTP 채널**(`manual_web.cpp`) — 폰 핫스팟 WiFi STA, core 0 고정
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

- `esp32_motor_comm.ino` — 태스크 3개 생성.
- `board_state.h/.cpp` — 뮤텍스로 보호된 공유 상태(`MotorBoardState`, 젯슨 타깃, 수동
  장부, fault 비트, 진단 카운터).
- `comm_task.h/.cpp` — Serial(921600bps) 프레임 파싱·디스패치, 텔레메트리 송신.
  **액추에이션을 하지 않는다** — 목표만 기록한다.
- `control_task.h/.cpp` — 100Hz. `arbitrateDrive()` 를 돌려 **유일하게** 액추에이션
  계층을 호출한다. 워치독 판정·데드타임 해제·조향 슬루레이트도 여기서.
- `mode_arbiter.h/.cpp` — 중재 상태기계. **`Arduino.h` 미포함**이라 호스트 g++ 로
  시험한다(`test/test_mode_arbiter.cpp`).
- `steering_limits.h` — `STEERING_MAX_MDEG`·`STEERING_MIN_DRIVE_MMPS`. 수동 매핑과
  서보 클램프가 갈라지지 않게 분리했다.
- `manual_web.h/.cpp` + `manual_web_config.h` + `control_page.h` — 수동 조종 채널.
- `safety_stub.h/.cpp` — BTS7960 2개(후륜 좌·우) PWM/DIR/EN 액추에이션(전·후진 전용).
- `steering.h/.cpp` — DS51150 조향 서보 PWM(GPIO18, 50Hz, LEDC 채널 4/별도 타이머).

### 태스크 배치

| 코어 | 태스크 | prio | 스택 | 주기 | 하는 일 |
|---|---|---|---|---|---|
| 1 | `control_task` | 3 | 3072B | 10ms | 중재 + **전 액추에이션** |
| 1 | `comm_task` | 2 | 4096B | 1ms | 921600 시리얼, 50Hz `DRIVE_STATE` |
| 0 | `manual_web` | 1 | 8192B | 2ms | `WebServer::handleClient()` |
| 0 | (시스템) | 23/18 | — | — | WiFi + lwIP |

웹 태스크가 core 0 인 것은 WiFi 태스크가 거기 고정돼 있어서다 — 100Hz 제어 루프와
921600 RX 폴에서 소켓 작업을 물리적으로 분리한다. prio 1 은 lwIP(18)·WiFi(23)
**아래**여야 하며, 위에 두면 네트워크 스택이 굶어 20Hz HTTP 가 오히려 느려진다.

`xTaskCreate` 의 `usStackDepth` 는 ESP-IDF 에서 **바이트**다(vanilla FreeRTOS 의
워드가 아니다). 상수 이름이 `*_STACK_BYTES` 인 이유다.

## 수동 조종 채널

폰이 자기 핫스팟 위에서 이 보드에 직결한다. 젯슨과 관제 PC 는 별개 WiFi 망에 있고
젯슨은 폰에 도달할 수 없으므로 이것이 수동 조종의 유일한 경로다.

| 경로 | 동작 |
|---|---|
| `GET /` | 조종 페이지 |
| `GET /manual/session` | `sid` 발급. 다른 조종자가 500ms 내 활성이면 409 |
| `GET /manual/drive?sid=&seq=&dm=&lin=&ang=&ttl=` | `lin`/`ang` 은 −1000..1000 정규화 밀리 단위(보드에서 float 파싱 없음). `ang` 은 CCW=+ (REP-103). 200/400/403/409/423 |
| `GET /manual/stop?sid=&seq=` | 즉시 구동 0. **세션 불일치에도 수락한다** — 누구의 정지든 존중 |
| `GET /manual/state` | 조회 전용. **부작용이 하나도 없다** |

> **`/manual/state` 가 `lastManualInputMs` 를 갱신하면 관제의 500ms 신선도 창이 영구히
> 닫히지 않아 `SET_MODE(AUTO)` 가 항상 거부되고 로봇을 되찾을 수 없다.** 폰이 이 경로를
> 2Hz 로 폴링하므로 실수하면 즉시 그렇게 된다. HTTP 계층에서 가장 중요한 금지사항이다.

`sid` 는 **로컬** 세션이며 관제의 `controlSessionId` 가 아니다. 인증 경계는 핫스팟
WPA2 가 전부이고 `sid` 는 단일 조종자만 강제하며 신원이 아니다(docs/06 보호 공백 표).

### 수동 중에 무엇이 모드를 바꾸는가

바꾸는 것은 하나뿐이다 — 관제 「자율」이 보내는 `SET_MODE(AUTO)`. 아래는 전부 **바퀴만
0 이고 `MANUAL_ACTIVE` 는 유지된다**: 모바일 「정지」/Space/Esc/창 blur, deadman 해제,
TTL(250ms) 만료, WiFi 끊김, `STOP_COMMAND`(추가로 re-arm 요구), 젯슨 링크 사망,
젯슨 재접속(`HELLO`).

유일한 예외는 `ESTOP_COMMAND`·물리 E-Stop 이다. 이것은 수동 권한을 막는 게 아니라
**벗긴다**(래치·세션 파괴). 자동 복귀는 어떤 경로로도 없다(SR-008).

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

WROOM-32 온보드 USB-UART 브리지는 UART 하나뿐이며 지금 이 UART가 바이너리 프로토콜 전용이다. `Serial.print()` 텍스트 디버그를 **어느 파일에도** 추가하지 말 것 — 프레임에 섞이면 CRC 드롭으로 복구는 되지만 노이즈와 오탐 카운트를 유발한다. `tank_drive` 를 병합하면서 그쪽의 `Serial.print*` 와 `Serial.begin(115200)` 은 전부 버렸다. 상태 확인은 GPIO/LED 또는 `/manual/state` 응답으로, 텍스트 로그가 꼭 필요하면 별도 핀의 `Serial2`를 컴파일 타임 매크로로 게이트해 사용할 것.

## 호스트 테스트

```
cd test
g++ -std=c++17 -I.. -I../../../jetson-comm/src \
    test_mode_arbiter.cpp ../mode_arbiter.cpp ../../../jetson-comm/src/protocol.cpp \
    -o test_mode_arbiter && ./test_mode_arbiter
```

Arduino IDE 는 스케치 폴더의 `test/` 를 컴파일하지 않으므로 여기 두어도 안전하다
(`jetson-comm` 이 쓰는 방식과 같다).
