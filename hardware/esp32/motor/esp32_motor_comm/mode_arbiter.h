// 액추에이션 권한 중재 (S15P11A301-298, docs/03 §34-2·34-7·34-9).
//
// **`Arduino.h` 를 포함하지 않는다.** 호스트 g++ 로 그대로 빌드되며
// `test/test_mode_arbiter.cpp` 가 실차 없이 전 분기를 돈다
// (`jetson-comm/test/test_protocol.cpp` 와 같은 방식).
//
// ## 왜 중재자가 이 보드인가
//
// 폰이 자기 핫스팟 위에서 이 보드에 직결하고, 젯슨과 관제 PC 는 별개 WiFi 망에
// 있다. 젯슨은 폰에 도달할 수 없으므로 **두 명령 소스가 만나는 유일한 지점이
// 여기**다. 젯슨 쪽 `command_mux` 는 자율 경로만 게이팅하고 폰을 볼 수 없다.
//
// ## 바퀴 소유자를 정하는 틱은 정확히 하나다
//
// 종전에는 액추에이션이 두 곳에서 일어났다 - `comm_task`(prio 2)와
// `control_task`(prio 3)가 `safety_stub.cpp`·`steering.cpp` 의 무보호 file-static
// 을 두고 경쟁했다. 여기에 core 0 의 HTTP 핸들러가 세 번째 writer 로 들어오면
// 잠재된 동일코어 경쟁이 진짜 크로스코어 경쟁이 된다.
//
// 그래서 `comm_task`(시리얼)와 `manual_web_task`(HTTP)는 뮤텍스 아래에서 **의도만
// 기록**하고, `control_task` 가 100Hz 로 `arbitrateDrive()` 를 돌려 **유일하게**
// 액추에이션 계층을 호출한다. 겹침 창이 구조적으로 사라진다.
#pragma once

#include <cstdint>

// `SetModeRequest`(SET_MODE_MANUAL/SET_MODE_AUTO)를 쓴다. 이 헤더도 `Arduino.h` 에
// 의존하지 않아 호스트 빌드가 그대로 된다.
#include <message_ids.h>

#include "board_state.h"

// ---- 승격 (권고 3: 오발 방어) ----
// 폰 화면을 잘못 스치거나 페이지가 새로고침되는 것으로 자율 주행을 빼앗기지
// 않도록, deadman 이 눌린 패킷이 **2개 이상 100ms 이상** 이어져야 래치가 걸린다.
//
// S15P11A301-345 이후 이 조건은 **폴백 경로에서만** 쓰인다. 평시에 폰 입력은
// 아무리 이어져도 래치를 걸지 못하고, 이 run 은 「페이지가 열려 있을 뿐인가,
// 사람이 실제로 조종하고 있는가」를 가르는 판정으로만 남는다.
constexpr uint32_t MANUAL_PROMOTION_HOLD_MS = 100;
constexpr uint8_t MANUAL_PROMOTION_PACKETS = 2;

// ---- 링크 침묵 폴백 (S15P11A301-345) ----
//
// 권한 서열은 `ESTOP > 관제(자율·수동 승인) > 폰 수동` 이다. 폰이 관제 자리를
// 대신하는 조건은 **젯슨 링크가 이 시간 이상 침묵할 때뿐**이다.
//
// 왜 5초인가: 정상 구간의 최대 침묵은 브리지 재접속 1사이클 2.5초다(재시도 1.0 +
// 부팅 정착 1.5). 그보다 짧게 잡으면 재접속마다 권한이 펄럭인다.
//
// 왜 폴백을 남기는가: 수동 조종자가 폰 하나뿐이라(관제 수동 조종 경로는 구현체가
// 없다) 완전히 잠그면 젯슨이 죽었을 때 로봇을 옮길 수단이 사라진다 - 04장 950-953
// 의 「잠그면 들어 옮기는 것 말고 복구 경로가 없다」가 그 상황이다.
constexpr uint32_t MANUAL_FALLBACK_SILENCE_MS = 5000;

// SET_MODE(AUTO) 를 수락하는 조건. 이 시간 안에 수동 입력이 있었으면 거부한다 -
// 사람이 조종하는 중에 관제가 바퀴를 빼앗으면 안 된다.
constexpr uint32_t MANUAL_FRESHNESS_GUARD_MS = 500;

// 수동 채널 TTL. 폰이 죽거나 WiFi 가 끊기면 이 시간 안에 바퀴가 선다(docs/05 31-6).
// 젯슨의 300ms 워치독은 죽은 *젯슨*을 막고 죽은 *폰*은 막지 못하므로, 폰 상실
// 보호는 전적으로 이 값이다.
constexpr uint16_t MANUAL_TTL_DEFAULT_MS = 250;
constexpr uint16_t MANUAL_TTL_MIN_MS = 100;
constexpr uint16_t MANUAL_TTL_MAX_MS = 500;

// 수동 최대 속도. 0.30 m/s (docs/04 961). **내리지 않는다** - 대신 수동 경로에서
// 무엇이 보호하지 않는지를 문서에 직설한다(docs/06 보호 공백 표).
constexpr int16_t MANUAL_MAX_DRIVE_MMPS = 300;

// 수동 조향 슬루레이트. **0 을 쓰면 안 된다** - 프로토콜상 0 은 「제한 없음」이고
// 그러면 steering.cpp 가 목표로 즉시 점프하는데, 그것이 §34-2 「조향 변화율 제한」이
// 금지하는 계단 입력이다. 60°/s 는 잠정값이며 CTRL-25 에서 확정한다.
constexpr uint16_t MANUAL_STEERING_RATE_MDPS = 60000;

// 젯슨 링크 워치독 (§34-7). 링크 생존만 본다.
constexpr uint32_t JETSON_WATCHDOG_TIMEOUT_MS = 300;

// stale-sequence ACK 레이트리밋. 20Hz 스트림이 통째로 밀리면 초당 20개의 ACK 가
// 나가는데, 그것은 진단이 아니라 소음이다.
constexpr uint32_t STALE_ACK_MIN_INTERVAL_MS = 200;

// `false` 인 동안은 `DRIVE_COMMAND.mode=2` 가 계속 `AUTO_ACTIVE` 로 올린다. 즉
// `SET_MODE` 의 유일한 책무는 **래치 해제**이고, 기존 젯슨 파이프라인의 동작은
// 완전히 같다. 이 플래그를 `true` 로 뒤집는 것(= 모드 진입도 명시 명령만 허용)은
// 후속 티켓이다.
constexpr bool AUTO_REQUIRES_EXPLICIT_SET_MODE = false;

// ---- 수동 패킷 ----

// HTTP `/manual/drive` 한 번. `lin`/`ang` 은 −1000..1000 정규화 밀리 단위이며
// 보드에서 float 파싱을 하지 않기 위한 형식이다. `ang` 은 CCW=+(REP-103).
struct ManualPacket {
  uint32_t sessionId;
  uint16_t sequence;
  bool deadman;
  int16_t linMilli;
  int16_t angMilli;
  uint16_t ttlMs;
};

enum class ManualResult : uint8_t {
  ACCEPTED,
  REJECTED_LATCHED,   // ESTOP/FAULT - 423
  REJECTED_SESSION,   // 다른 조종자 - 403
  REJECTED_SEQUENCE,  // 재정렬·재생 - 409
  REJECTED_ARGUMENT,  // 범위 밖 - 400
};

// 젯슨 링크가 `MANUAL_FALLBACK_SILENCE_MS` 이상 침묵했는가 (S15P11A301-345).
//
// `lastValidJetsonRxMs` 는 HELLO 를 포함해 젯슨에서 온 **어떤** 유효 프레임이든
// 갱신하는 축이다(comm_task.cpp `markJetsonContact`). `DRIVE_COMMAND` 수신 빈도만
// 보는 300ms 워치독과 섞지 않는다 - 저쪽은 「상위 파이프라인이 멈췄나」를, 이쪽은
// 「링크 자체가 죽었나」를 재고, 권한을 넘길 근거가 되는 것은 후자뿐이다.
//
// 부팅 후 한 번도 접촉이 없으면 `lastValidJetsonRxMs` 가 0 이라 uptime 자체가 침묵
// 시간이 된다. 특례를 두지 않는 것이 의도다 - 젯슨 없이 켠 보드는 5초 뒤부터
// 폰으로 옮길 수 있어야 하고, 그 5초는 부팅 직후 오발을 막는 창이기도 하다.
bool jetsonLinkSilent(const MotorSharedState& s, uint32_t nowMs);

// 수동 패킷 하나를 장부에 반영한다. **거부하면 상태를 하나도 바꾸지 않는다.**
//
// 순서가 중요하다. 이전 입력 시각·TTL 을 먼저 스냅샷해 `gapExceeded` 를 계산한다 -
// 그것이 없으면 "30초 간격 두 패킷" 이 100ms 지속 조건을 만족해 버린다.
//
// **수락은 액추에이션이 아니다** (S15P11A301-345). 래치가 없으면 이 함수는 장부만
// 갱신하고 구동·조향 목표를 0 으로 둔다. 폰이 권한을 얻는 유일한 경로는 관제의
// `SET_MODE(MANUAL)` 이고, 예외는 `jetsonLinkSilent()` 가 참일 때의 폴백 승격뿐이다.
ManualResult ingestManualPacket(MotorSharedState& s, const ManualPacket& packet,
                                 uint32_t nowMs);

// ---- SET_MODE (0x13) ----

enum class SetModeResult : uint8_t {
  ACCEPTED,
  REJECTED_STATE,
};

// 수락되는 요청은 MANUAL(1)·AUTO(2) **둘뿐이다.** 0(SAFE_IDLE)은 보내는 쪽이 없어
// 정의하지 않았고 거부값이다.
//
// 거부 사유는 새 ack-result 코드 없이 `COMMAND_ACK.boardState` 로 구분된다 -
// `REJECTED_STATE` + `boardState=MANUAL_ACTIVE` 는 500ms 신선도 가드에 걸렸다는
// 뜻이며, 그것이 「자율」 버튼이 거부되는 유일한 정상 사유다.
SetModeResult applySetMode(MotorSharedState& s, uint8_t requestedMode, uint32_t nowMs);

// ---- 중재 ----

enum class DriveOwner : uint8_t {
  NONE,    // 아무도 굴리지 않는다. 안전 출력.
  MANUAL,  // 폰이 권한을 쥐고 있다(움직이는 중인지는 별개다).
  JETSON,
};

// 이 틱의 결론. `control_task` 가 그대로 집행한다.
struct DriveDecision {
  DriveOwner owner = DriveOwner::NONE;
  int16_t driveLeftMmps = 0;
  int16_t driveRightMmps = 0;

  bool applySteering = false;
  int16_t steeringMdeg = 0;
  uint16_t steeringRateMdps = 0;
  // 정지 중 조향 금지 판정(§34-2)에 쓸 선속도 크기.
  int16_t steeringDriveMagnitudeMmps = 0;
  // 조향 거부를 bit 14 로 올릴 것인가. 젯슨 명령에만 참이다 - 수동은 애초에
  // 거부될 명령을 보내지 않도록 미리 걸렀다.
  bool reportSteeringFault = false;

  bool applyNextState = false;
  MotorBoardState nextState = MotorBoardState::SAFE_IDLE;

  bool raiseCommTimeout = false;
  bool clearCommTimeout = false;
};

// 젯슨의 DRIVE_COMMAND 가 바퀴에 닿아도 되는가. 수동 래치가 유일한 차단 사유다.
bool jetsonActuationAllowed(const MotorSharedState& s);

// 이 틱의 바퀴 소유자를 정한다. 상태를 바꾸지 않는 순수 함수다.
//
// **수동 분기에서 `nextState` 는 무조건 `MANUAL_ACTIVE` 다.** 바퀴가 0 이어도,
// `STOP_COMMAND` 를 받은 뒤에도 `STOPPING` 으로 가지 않는다. `DRIVE_STATE` 에
// 여분 필드가 없고 젯슨의 계약은 "보고된 상태를 따른다" 이므로, 래치를 쥔 채
// `STOPPING` 으로 넘어가면 젯슨이 「권한이 풀렸다」로 읽고 스트리밍을 시작한다.
//
// 그래서 `MANUAL_ACTIVE` 의 의미는 정확히 **"수동 채널이 액추에이션 권한을
// 보유한다"** 이고, 실제로 움직이는지는 `drivePwmLeft/RightPermille`·`driverEnabled`
// 로 읽는다. 이것이 와이어 포맷 변경이 필요 없는 이유다.
DriveDecision arbitrateDrive(const MotorSharedState& s, uint32_t nowMs);
