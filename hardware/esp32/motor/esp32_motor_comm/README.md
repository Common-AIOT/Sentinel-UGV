# esp32_motor_comm

모터 ESP32의 **유일한 운영 스케치**. 명령 소스가 둘이고 이 보드가 중재자다.

```
젯슨  ── USB 직렬 921600, 동기워드+고정길이+CRC8 ──┐
                                                    ├→ control_task (100Hz, 유일한 액추에이터)
폰    ── 핫스팟 WiFi, HTTP 20Hz ────────────────────┘      → 후륜 BTS7960 2개 + 전륜 조향 서보
```

**S15P11A301-321**: 젯슨 링크 프레이밍을 `jetson-comm`(COBS+CRC16+길이+uptime, 센서가 여전히 씀)에서 이 스케치 전용의 `motor_protocol.h`(동기워드 2바이트+타입+시퀀스+고정 22바이트 payload+CRC8, 27바이트 고정)로 교체했다. 메시지 종류·의미는 그대로다 - 프레이밍만 단순화했다. 근거·설계는 `motor_protocol.h` 헤더 주석과 `docs/03-제어-캘리브레이션.md` §34-5 addendum 참고.

`motor_test`/`double_motor_test`/`triple_motor_test`/`steering_servo_test`(벤치 테스트용 수동 제어)는 이 스케치와 별개로 그대로 유지된다.

**2026-08-06 하드웨어 변경**: 앞쪽 캐스터 2개를 제거하고 전륜 조향부(앞바퀴 2개·타이로드)를 복구했다. 후륜 DC 모터 2개는 **전·후진 전용**이 되고 조향은 전륜 DS51150-12V 서보 1개가 담당한다(02장 6.3, §34-2). 좌·우 속도 차로 회두를 만들지 않으며 **제자리 회전은 할 수 없다.**

**S15P11A301-298**: `tank_drive` 스케치를 여기에 병합하고 **삭제했다.** 두 스케치는 같은 핀·같은 LEDC 채널을 쓰는 배타 스케치였고, 남겨 두면 잘못 굽는 순간 강직된 전륜 링키지를 거슬러 차동 선회를 시도한다. 수동 조종 웹 UI 는 `control_page.h` 로 옮겨 왔다.

**빌드 전 필수**:
1. `jetson-comm`을 Arduino 라이브러리로 설치한다 — `../../jetson-comm/README.md`의 "설치" 절 참고(정션 없이 그냥 열면 링크 단계에서 undefined reference 오류가 난다).
2. **`manual_web_config.h` 의 SSID/비밀번호를 자기 폰 핫스팟 값으로 바꾼다.** 플레이스홀더 그대로 구우면 접속만 실패하고 부팅은 정상 진행되므로(비블로킹) 조용히 수동 채널만 죽는다. 이 파일을 커밋하지 말 것.

## 범위

포함:
- 모터 전용 동기워드+고정길이+CRC8 프레이밍(`motor_protocol.h`, S15P11A301-321).
  페이로드 pack/unpack은 여전히 `jetson-comm`(`<protocol.h>`)의 것을 재사용한다 -
  건드린 건 프레이밍 계층뿐이다
- `HELLO`/`HELLO_ACK` 핸드셰이크 (E-Stop 래치 해제도 이 경로로 처리). **수동 래치는
  건드리지 않는다** — 젯슨 프로세스 재시작이 조작 중인 사람에게서 바퀴를 빼앗아선 안
  되고, 진짜 보드 리부트는 RAM 이 날아가 래치가 함께 사라진다
- `STOP_COMMAND`/`ESTOP_COMMAND`/`SET_MODE` 수신과 `COMMAND_ACK` 응답
- **액추에이션 중재**(`mode_arbiter.cpp`) — 수동 승격(2패킷·100ms), TTL(250ms),
  자동 전환 500ms 신선도 가드, 바퀴 소유자 결정
- **수동 조종 HTTP 채널**(`manual_web.cpp`) — 폰 핫스팟 WiFi STA, core 0 고정
- 300ms 통신 워치독(§34-7) — 트립 시 구동만 끊고(`applySafeOutputs()`) **조향각은
  마지막 목표를 유지한다**(CTRL-26). `mode_arbiter`가 보는 이 워치독은
  `DRIVE_COMMAND` 수신 빈도만 본다 - 링크 자체의 생존은 `comm_task.cpp`가 별도로
  추적해 `DIAGNOSTIC.linkSilenceMs`로 보고한다(둘을 합치면 안 되는 이유는
  `motor_protocol.h`/`comm_task.cpp` 주석 참고)
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
- `motor_protocol.h/.cpp` — 모터 전용 프레이밍(동기워드+고정길이+CRC8,
  S15P11A301-321). `Arduino.h` 미포함이라 호스트 g++로 시험한다
  (`test/test_motor_protocol.cpp`). 페이로드 구조체·pack/unpack은 `jetson-comm`
  것을 그대로 재사용하고, `MotorDiagnostic`(linkSilenceMs 포함)만 이 파일이
  새로 정의한다.
- `board_state.h/.cpp` — 뮤텍스로 보호된 공유 상태(`MotorBoardState`, 젯슨 타깃, 수동
  장부, fault 비트, 진단 카운터, 링크 접촉 시각).
- `comm_task.h/.cpp` — Serial(921600bps) 프레임 파싱·디스패치, 텔레메트리 송신.
  **액추에이션을 하지 않는다** — 목표만 기록한다. Jetson으로부터 온 프레임이면
  타입 무관하게 링크 접촉 시각을 갱신해 `linkSilenceMs`를 만든다 -
  `DRIVE_COMMAND` 수신 빈도만 보는 `mode_arbiter`의 300ms 워치독과는 다른 축.
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
| `GET /manual/speed?percent=` | 수동 주행 상한 0~100%. 게이트 없음 — 속도를 낮추는 건 늘 안전한 방향이다 |
| `GET /manual/servo/arm` · `disarm` | 서보 PWM 출력. `disarm` 은 듀티 0 이라 서보가 free 가 된다 |
| `GET /manual/servo/center?deg=` | 서보 중립 60~210° (기본 145°) |
| `GET /manual/servo/limit?deg=` | 좌우 최대 조향각 0~60° (기본 30°) |
| `GET /manual/servo/angle?deg=` | 서보 각도 직접 지정(조그). 중립±오프셋 범위 |
| `GET /manual/servo/recenter` | 조향 중립 복귀 |

`/manual/servo/*` 다섯 개는 전부 **캘리브레이션 게이트**(`calibrationAllowed`)를 통과해야 하며
실패하면 409 `CAL_BLOCKED` 이다. 거부 조건은 `AUTO_ACTIVE`·`ESTOP_LATCHED`·`FAULT_LATCHED`·
deadman 눌림·`manualDriveMmps != 0`·실제 PWM 이 아직 살아 있음이다. 폰은 응답의 `cal` 필드를
보고 입력칸을 잠근다.

### 조종 페이지 (`control_page.h`)

**S15P11A301-312**: 5버튼 D-패드를 터치 조이스틱으로 바꿨다(`drive_test` UI 기준). 위·아래가
전·후진, 좌·우가 조향이며 **누르고 있는 동안만** deadman 이 선다. D-패드에서는 좌/우를
전·후진 홀드 중에만 눌리게 `disabled` 로 막아야 했지만, 조이스틱은 그 규칙이 기하로
표현된다 — 스틱을 좌우 수평으로만 밀면 전·후진 성분이 0 이라 애초에 조향 명령이 나가지
않는다(§34-2).

`ang` 은 **`|lin| >= 100` 일 때만** 싣는다. 100 은 `STEERING_MIN_DRIVE_MMPS(30) /
MANUAL_MAX_DRIVE_MMPS(300) x 1000` 이다 — 펌웨어가 어차피 거부할 조향을 보내면 그 거부가
`STEERING_COMMAND_INVALID`(bit 14)로 올라가 그 비트의 의미를 파괴한다. 화면의 「조향 불가」
표시도 같은 경계를 본다.

**속도 상한은 보드가 갖는다.** `lin` 은 순수 정규화 스로틀이고 `%` 스케일링은
`ingestManualPacket` 이 한다 — 폰이 새로고침되거나 다른 조종자가 붙어도 상한이 유지되어야
하는 값이라 클라이언트에 두지 않았다. 기본 100 이면 그 곱셈은 항등이라 기존 동작과
`test_mode_arbiter` 가 그대로다. 조향 가능 판정은 **실효 `lin`**(상한을 곱한 뒤)을 봐야 하며,
페이지도 그렇게 계산한다.

`MANUAL_MAX_DRIVE_MMPS`·`STEERING_MAX_MDEG`·`STEERING_MIN_DRIVE_MMPS` 세 상수의 사본이 페이지
JS 상단에 있으니 펌웨어에서 값을 바꾸면 같이 바꾼다.

### 조향 캘리브레이션 (S15P11A301-312)

`drive_test.ino` 벤치 스케치의 중립·엔드포인트·출력 on/off 를 펌웨어로 옮긴 것이다. §35-3
실측이 TBD-HW-008 이라 세 값이 전부 임시값이고, 벤치에서 폰으로 돌려 보고 확정하기 위한
창구다. **확정되면 `steering.cpp` 상수로 굳힌다.**

| 항목 | 기본 | 범위 | 비고 |
|---|---|---|---|
| 서보 중립 | 145° | 60~210° | `SERVO_CENTER_DEG` |
| 좌우 최대 조향각 | 30° | 0~60° | δ_max 가 몇 도의 서보 회전으로 나가는지를 정한다 |
| 서보 PWM 출력 | 켬 | — | 끄면 듀티 0, 서보 free |

지켜야 하는 것 넷:

1. **`STEERING_MAX_MDEG` 는 바뀌지 않는다.** 오프셋을 줄이면 δ_max 가 덜 꺾이게 될 뿐
   프로토콜 상한은 그대로다. 젯슨 `vehicle_kinematics` 의 `max_steering_rad` 와 맞춰 둔
   값을 폰에서 흔들면 안 된다. **그래서 자율 주행 중 캘리브레이션은 무조건 거부다** —
   오프셋을 줄여 둔 채 자율로 넘기면 젯슨은 30° 로 계산하는데 실물은 덜 꺾이고, 개루프라
   아무도 그것을 감지하지 못한다. 캘리브레이션 후 자율로 넘기기 전에 오프셋을 30° 로
   되돌렸는지 반드시 확인할 것.
2. **부팅 시 armed 다.** `drive_test` 의 `SERVO_START_ARMED=false` 를 가져오지 않았다.
   §34-6 이 부팅 직후 중립(δ=0) 초기화를 요구하는데 출력이 꺼져 있으면 그 초기화가
   물리적으로 일어나지 않는다.
3. **영속화하지 않는다.** 재부팅하면 145°/30°/armed 로 돌아온다. 잘못 맞춘 중립이 플래시에
   남아 다음 부팅의 §34-6 초기화를 오염시키면 아무도 원인을 찾지 못한다.
4. **조그(`/manual/servo/angle`)는 §34-2 를 의도적으로 우회한다.** 정지 중 조향 금지를
   건너뛰는 유일한 경로이며 용도는 바퀴를 띄운 벤치 실측뿐이다. 슬루레이트와 ±δ_max
   클램프는 그대로 걸린다. `steeringSetTarget` 이 아니라 `steeringJogToMdeg` 를 쓰는 이유가
   이것 하나다.

쓰기는 HTTP 계층이 게이팅하고, 실제 반영은 언제나 `control_task` 의 10ms 틱 하나다 —
`control_task.cpp` 13-20 이 없앤 세 번째 writer 를 되살리지 않기 위해서다.

### mDNS 생명주기

종전 코드는 연결 **전이**에서만 `MDNS.begin()` 을 불렀고 끊길 때 `MDNS.end()` 를 부르지
않았다. 그래서 (a) 첫 등록이 실패하면 재시도가 영원히 없고, (b) WiFi 가 한 번이라도 끊기면
`g_mdnsStarted` 가 true 로 남아 재연결 후 재등록을 건너뛰었다. 둘 다 **HTTP 는 IP 로 멀쩡히
되는데 `sentinel-manual.local` 만 죽는** 증상으로 나타난다. 지금은 연결돼 있고 미등록이면
3초마다 재시도하고, 끊길 때 `MDNS.end()` 로 내린다.

안드로이드는 그와 별개로 `.local` 해석을 잘 못한다. 안 되면 핫스팟 클라이언트 목록에서 IP 를
직접 확인해 쓴다.

멈추는 경로는 그대로다 — pointerup/cancel/lostpointercapture, 긴급 정지 버튼, Space/Escape,
`blur`·`visibilitychange`·`pagehide`, 50ms 재전송 + 단일 in-flight 가드, 유휴 시
`/manual/state` 2Hz 폴링. 페이지를 `file:` 로 열면 보드 없이 레이아웃만 보는 오프라인
미리보기로 뜬다.

### 조종 페이지 (`control_page.h`)

**S15P11A301-312**: 5버튼 D-패드를 터치 조이스틱으로 바꿨다(`drive_test` UI 기준). 위·아래가
전·후진, 좌·우가 조향이며 **누르고 있는 동안만** deadman 이 선다. D-패드에서는 좌/우를
전·후진 홀드 중에만 눌리게 `disabled` 로 막아야 했지만, 조이스틱은 그 규칙이 기하로
표현된다 — 스틱을 좌우 수평으로만 밀면 전·후진 성분이 0 이라 애초에 조향 명령이 나가지
않는다(§34-2).

`ang` 은 **`|lin| >= 100` 일 때만** 싣는다. 100 은 `STEERING_MIN_DRIVE_MMPS(30) /
MANUAL_MAX_DRIVE_MMPS(300) x 1000` 이다 — 펌웨어가 어차피 거부할 조향을 보내면 그 거부가
`STEERING_COMMAND_INVALID`(bit 14)로 올라가 그 비트의 의미를 파괴한다. 화면의 「조향 불가」
표시도 같은 경계를 본다.

속도 슬라이더와 좌우 최대 조향각 슬라이더는 **둘 다 클라이언트 전용**이다. `lin`/`ang` 을
스케일링할 뿐이며 보드에 별도 엔드포인트가 없다. 조향각 슬라이더 상한은
`STEERING_MAX_MDEG`(30°)와 같은 값이어야 한다 — 어긋나면 슬라이더 끝이 실제 δ_max 와
달라지고 그것은 화면에 보이지 않는다. `MANUAL_MAX_DRIVE_MMPS`·`STEERING_MAX_MDEG`·
`STEERING_MIN_DRIVE_MMPS` 세 상수의 사본이 페이지 JS 상단에 있으니 펌웨어에서 값을 바꾸면
같이 바꾼다.

멈추는 경로는 그대로다 — pointerup/cancel/lostpointercapture, 긴급 정지 버튼, Space/Escape,
`blur`·`visibilitychange`·`pagehide`, 50ms 재전송 + 단일 in-flight 가드, 유휴 시
`/manual/state` 2Hz 폴링. 페이지를 `file:` 로 열면 보드 없이 레이아웃만 보는 오프라인
미리보기로 뜬다.

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

## 부팅 상태 표시 (S15P11A301-312)

부팅 후 5초간 폰 핫스팟 접속 상태와 접속 주소(IP·mDNS)를 `boot_status.h/.cpp` 가
**Serial2**(GPIO16=RX2·GPIO17=TX2, 921600)로 보여준다. Jetson 전용 UART(Serial,
921600)는 건드리지 않으며, comm_task 의 시작도 지연시키지 않는다 — 두 채널은 다른
물리 UART라 시간적으로 완전히 독립이다. Jetson 핸드셰이크는 이 창과 무관하게
언제나 부팅과 동시에 즉시 시작한다(아래 「디버그 주의」와 같은 이유).

**WROOM-32 온보드 USB 브리지는 UART0(Jetson용) 하나뿐이다.** 이 표시를 보려면
GPIO16·GPIO17·GND 에 별도 USB-UART 어댑터를 물려야 한다. 컴파일 타임에 끄려면
빌드 정의에 `ENABLE_BOOT_STATUS_SERIAL2=0` 을 추가한다.

## 디버그 주의

WROOM-32 온보드 USB-UART 브리지는 UART 하나뿐이며 지금 이 UART가 바이너리 프로토콜 전용이다. `Serial.print()` 텍스트 디버그를 **어느 파일에도** 추가하지 말 것 — 프레임에 섞이면 CRC 드롭으로 복구는 되지만 노이즈와 오탐 카운트를 유발한다. `tank_drive` 를 병합하면서 그쪽의 `Serial.print*` 와 별도 텍스트 시리얼 초기화는 전부 버렸다. 상태 확인은 GPIO/LED 또는 `/manual/state` 응답으로, 텍스트 로그가 꼭 필요하면 별도 핀의 `Serial2`를 컴파일 타임 매크로로 게이트해 사용할 것.

## 호스트 테스트

```
cd test
g++ -std=c++17 -I.. -I../../../jetson-comm/src \
    test_mode_arbiter.cpp ../mode_arbiter.cpp ../../../jetson-comm/src/protocol.cpp \
    -o test_mode_arbiter && ./test_mode_arbiter

g++ -std=c++17 -I.. -I../../../jetson-comm/src \
    test_motor_protocol.cpp ../motor_protocol.cpp ../../../jetson-comm/src/protocol.cpp \
    -o test_motor_protocol && ./test_motor_protocol
```

Arduino IDE 는 스케치 폴더의 `test/` 를 컴파일하지 않으므로 여기 두어도 안전하다
(`jetson-comm` 이 쓰는 방식과 같다).
