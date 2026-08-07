#include "mode_arbiter.h"

#include <protocol.h>

#include "steering_limits.h"

namespace {

constexpr int16_t NORMALIZED_MAX = 1000;

int16_t clampI16(int32_t value, int32_t lo, int32_t hi) {
  if (value < lo) return (int16_t)lo;
  if (value > hi) return (int16_t)hi;
  return (int16_t)value;
}

int32_t absI32(int32_t value) {
  return value < 0 ? -value : value;
}

int32_t clampI32(int32_t value, int32_t lo, int32_t hi) {
  if (value < lo) return lo;
  if (value > hi) return hi;
  return value;
}

bool boardLatched(const MotorSharedState& s) {
  return s.state == MotorBoardState::ESTOP_LATCHED ||
         s.state == MotorBoardState::FAULT_LATCHED;
}

// 수동 구동·조향을 0 으로 되돌린다. **래치는 건드리지 않는다.**
void quietManualOutputs(MotorSharedState& s) {
  s.manualDriveMmps = 0;
  s.manualSteeringRequested = false;
}

}  // namespace

bool jetsonActuationAllowed(const MotorSharedState& s) {
  // 수동 래치가 유일한 차단 사유다. ESTOP/FAULT 는 arbitrateDrive 의 0번 분기가
  // 먼저 걸러내므로 여기서 다시 보지 않는다.
  return !s.manualLatched;
}

ManualResult ingestManualPacket(MotorSharedState& s, const ManualPacket& packet,
                                 uint32_t nowMs) {
  // ---- 거부 검사. 하나라도 걸리면 상태를 바꾸지 않는다. ----
  if (boardLatched(s)) {
    return ManualResult::REJECTED_LATCHED;
  }
  if (s.manualSessionId == 0 || packet.sessionId != s.manualSessionId) {
    return ManualResult::REJECTED_SESSION;
  }
  if (s.hasManualSequence && !isSequenceNewer(packet.sequence, s.manualSequence)) {
    return ManualResult::REJECTED_SEQUENCE;
  }
  if (absI32(packet.linMilli) > NORMALIZED_MAX || absI32(packet.angMilli) > NORMALIZED_MAX) {
    return ManualResult::REJECTED_ARGUMENT;
  }

  // ---- 이전 값 스냅샷. gapExceeded 계산에 쓴다. ----
  //
  // 이것이 없으면 "30초 간격으로 온 두 패킷" 이 「2패킷·100ms 지속」 조건을
  // 만족해 버린다. run 은 **끊기지 않고 이어진** 입력이어야 한다.
  const bool prevHadInput = s.hasManualInput;
  const uint32_t prevInputMs = s.lastManualInputMs;
  const uint16_t prevTtlMs = s.manualTtlMs;

  // ---- 장부 갱신 ----
  s.manualSequence = packet.sequence;
  s.hasManualSequence = true;
  s.manualTtlMs = (uint16_t)clampI16((int32_t)packet.ttlMs, MANUAL_TTL_MIN_MS, MANUAL_TTL_MAX_MS);
  s.lastManualInputMs = nowMs;
  s.hasManualInput = true;
  s.manualDeadman = packet.deadman;

  const bool gapExceeded = prevHadInput && (nowMs - prevInputMs > prevTtlMs);

  if (!packet.deadman) {
    // 손을 뗐다. 바퀴만 0 이고 **모드는 유지한다** - deadman 해제는 이탈이 아니다.
    // 여기서 re-arm 도 풀린다. 그래야 STOP 을 받은 운영자가 "뗐다가 다시 누른다"
    // 로 복구할 수 있다.
    s.manualRunActive = false;
    s.manualRunPackets = 0;
    s.manualReArmRequired = false;
    quietManualOutputs(s);
    return ManualResult::ACCEPTED;
  }

  if (!s.manualRunActive || gapExceeded) {
    s.manualRunActive = true;
    s.manualRunPackets = 1;
    s.manualRunStartedMs = nowMs;
  } else if (s.manualRunPackets < 0xFF) {
    s.manualRunPackets++;
  }

  if (!s.manualLatched) {
    const bool longEnough = (nowMs - s.manualRunStartedMs) >= MANUAL_PROMOTION_HOLD_MS;
    const bool enoughPackets = s.manualRunPackets >= MANUAL_PROMOTION_PACKETS;
    if (!(longEnough && enoughPackets)) {
      // 아직 자율이 굴린다. 승격 조건을 채우기 전에는 바퀴를 주지 않는다.
      quietManualOutputs(s);
      return ManualResult::ACCEPTED;
    }
    s.manualLatched = true;
    // 보고 상태도 **같은 순간에** 옮긴다. control_task 가 10ms 뒤에 어차피 같은
    // 값을 쓰지만, 그 창 안에 SET_MODE(AUTO) 가 도착하면 거부 ACK 가 아직
    // AUTO_ACTIVE 인 boardState 를 싣고 나간다. 젯슨은 그 필드로 「500ms 가드에
    // 걸렸다」와 「보드가 그냥 거부했다」를 구분하므로, 그 한 프레임이 운영자에게
    // 틀린 사유를 보여 준다.
    s.state = MotorBoardState::MANUAL_ACTIVE;
    // **100ms hold 는 진입만 게이팅한다.** 래치된 뒤의 재누름은 첫 패킷에 즉시
    // 반응한다 - 규칙의 목적은 *자율을* 새로고침에서 보호하는 것이고, 이미 사람이
    // 차량을 소유한 뒤의 100ms 지연은 둔한 조향일 뿐 안전 이득이 없다.
  }

  if (s.manualReArmRequired) {
    // 관제·초음파 중계가 정지시켰다. 손을 떼기 전에는 다시 굴리지 않는다.
    quietManualOutputs(s);
    return ManualResult::ACCEPTED;
  }

  // 속도 상한은 **보드가 갖는다**(S15P11A301-312). 폰이 새로고침되거나 다른
  // 조종자가 붙어도 유지되어야 하는 값이라 클라이언트 스케일링으로 두지 않았다.
  // 기본 100 이면 이 곱셈은 항등이고 기존 동작·테스트가 그대로다.
  const int32_t speedLimited =
      ((int32_t)packet.linMilli * clampI32(s.manualSpeedLimitPercent, 0, 100)) / 100;
  s.manualDriveMmps =
      (int16_t)((speedLimited * MANUAL_MAX_DRIVE_MMPS) / NORMALIZED_MAX);
  s.manualSteeringMdeg =
      (int16_t)(((int32_t)packet.angMilli * STEERING_MAX_MDEG) / NORMALIZED_MAX);
  // 정지 중에는 조향을 아예 요청하지 않는다. steering.cpp 에 보내면 거부되고 그
  // 거부가 bit 14 로 올라가 그 비트의 의미를 파괴한다. 폰에는 HTTP 응답의
  // `nosteer` 로 알린다.
  s.manualSteeringRequested = absI32(s.manualDriveMmps) >= STEERING_MIN_DRIVE_MMPS;
  return ManualResult::ACCEPTED;
}

SetModeResult applySetMode(MotorSharedState& s, uint8_t requestedMode, uint32_t nowMs) {
  if (boardLatched(s)) {
    // E-Stop 을 모드 전환으로 우회할 수 없다. 상태는 불변이다.
    return SetModeResult::REJECTED_STATE;
  }
  if (requestedMode != SET_MODE_MANUAL && requestedMode != SET_MODE_AUTO) {
    return SetModeResult::REJECTED_STATE;
  }

  if (requestedMode == SET_MODE_AUTO) {
    const bool manualFresh =
        s.hasManualInput && (nowMs - s.lastManualInputMs) < MANUAL_FRESHNESS_GUARD_MS;
    if (manualFresh) {
      // 사람이 조종하는 중이다. 상태를 유지한 채 거부하며, 젯슨은
      // boardState=MANUAL_ACTIVE 로 이것이 500ms 가드였음을 안다.
      return SetModeResult::REJECTED_STATE;
    }
    s.manualLatched = false;
    s.manualReArmRequired = false;
    s.manualDeadman = false;
    s.manualDriveMmps = 0;
    s.manualSteeringRequested = false;
    s.manualRunActive = false;
    s.manualRunPackets = 0;
    // 세션을 0 으로 만들어 폰이 `/manual/session` 을 다시 받아야만 - 즉 사람의
    // 명시적 행위가 있어야만 - 다시 조종할 수 있게 한다. 브라우저 탭이 백그라운드에
    // 남아 있다가 되살아나는 것으로 권한이 돌아가면 안 된다.
    s.manualSessionId = 0;
    s.hasManualSequence = false;
    s.jetsonRejectAcked = false;
    s.state = MotorBoardState::AUTO_ACTIVE;
    return SetModeResult::ACCEPTED;
  }

  s.manualLatched = true;
  s.manualReArmRequired = false;
  s.manualDeadman = false;
  s.manualDriveMmps = 0;
  s.manualSteeringRequested = false;
  s.manualRunActive = false;
  s.manualRunPackets = 0;
  s.state = MotorBoardState::MANUAL_ACTIVE;
  return SetModeResult::ACCEPTED;
}

DriveDecision arbitrateDrive(const MotorSharedState& s, uint32_t nowMs) {
  DriveDecision decision{};

  // 0) 래치 상태. 구동은 0 이고 조향은 **미적용**이다 - 정지가 곧 정차가 아니므로
  //    관성 주행 중 중립으로 꺾으면 피하려던 쪽으로 밀린다(§34-7).
  if (boardLatched(s)) {
    decision.owner = DriveOwner::NONE;
    return decision;
  }

  const bool jetsonStale =
      s.hasAcceptedSequence &&
      (nowMs - s.lastValidDriveCommandMs > JETSON_WATCHDOG_TIMEOUT_MS);

  // 1) 수동이 이긴다.
  if (s.manualLatched) {
    decision.owner = DriveOwner::MANUAL;
    decision.applyNextState = true;
    decision.nextState = MotorBoardState::MANUAL_ACTIVE;
    // 진단은 사실대로 올린다. 젯슨 링크가 실제로 끊겼으면 fault 비트는 서야 하고,
    // 다만 그것이 수동 출력을 끊지는 않는다 - 그 구간의 정지 보장은 수동 채널의
    // TTL+deadman 이 담당한다.
    decision.raiseCommTimeout = jetsonStale;

    const bool manualFresh =
        s.hasManualInput && (nowMs - s.lastManualInputMs) <= s.manualTtlMs;
    if (s.manualReArmRequired || !manualFresh || !s.manualDeadman) {
      return decision;  // 바퀴 0, 권한 유지
    }

    decision.driveLeftMmps = s.manualDriveMmps;
    decision.driveRightMmps = s.manualDriveMmps;
    decision.applySteering = s.manualSteeringRequested;
    decision.steeringMdeg = s.manualSteeringMdeg;
    decision.steeringRateMdps = MANUAL_STEERING_RATE_MDPS;
    decision.steeringDriveMagnitudeMmps = (int16_t)absI32(s.manualDriveMmps);
    return decision;
  }

  // 2) 젯슨 링크가 죽었다.
  if (jetsonStale) {
    decision.owner = DriveOwner::NONE;
    decision.applyNextState = true;
    decision.nextState = MotorBoardState::STOPPING;
    decision.raiseCommTimeout = true;
    return decision;
  }

  // 3) 자율. 상태는 handleDriveCommand 가 mode 바이트로 이미 정했으므로 여기서
  //    바꾸지 않는다(D10).
  decision.owner = DriveOwner::JETSON;
  decision.driveLeftMmps = s.targetDriveLeftMmps;
  decision.driveRightMmps = s.targetDriveRightMmps;
  decision.applySteering = true;
  decision.reportSteeringFault = true;
  decision.steeringMdeg = s.targetSteeringMdeg;
  decision.steeringRateMdps = s.maxSteeringRateMdps;
  decision.steeringDriveMagnitudeMmps = (int16_t)
      (absI32(s.targetDriveLeftMmps) > absI32(s.targetDriveRightMmps)
           ? absI32(s.targetDriveLeftMmps)
           : absI32(s.targetDriveRightMmps));
  decision.clearCommTimeout = true;
  return decision;
}
