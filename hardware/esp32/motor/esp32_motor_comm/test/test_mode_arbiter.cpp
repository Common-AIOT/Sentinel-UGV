// 액추에이션 권한 중재 유닛테스트 (S15P11A301-298).
//
// 호스트 컴파일용이며 Arduino/FreeRTOS 의존성이 없다. `board_state.cpp` 는 링크하지
// 않는다 - 그쪽은 FreeRTOS 뮤텍스 구현이고, 여기서 시험하는 것은 그 뮤텍스 아래에서
// 돌아가는 **순수 판정 로직**이다.
//
// 빌드 (test/ 에서):
//   g++ -std=c++17 -I.. -I../../../jetson-comm/src
//       test_mode_arbiter.cpp ../mode_arbiter.cpp
//       ../../../jetson-comm/src/protocol.cpp -o test_mode_arbiter
//
// Arduino IDE 는 스케치 폴더의 test/ 를 컴파일하지 않으므로 여기 두어도 안전하다
// (jetson-comm 이 쓰는 방식과 같다).

#include <cstdio>

#include "../mode_arbiter.h"
#include "../steering_limits.h"

namespace {

int failureCount = 0;

void expectTrue(bool condition, const char* what) {
  if (!condition) {
    std::printf("FAIL: %s\n", what);
    failureCount++;
  } else {
    std::printf("PASS: %s\n", what);
  }
}

// 젯슨이 20Hz 로 정상 스트리밍 중인 보드.
MotorSharedState autoBoard(uint32_t nowMs = 10000) {
  MotorSharedState s;
  s.state = MotorBoardState::AUTO_ACTIVE;
  s.hasAcceptedSequence = true;
  s.lastAcceptedSequence = 100;
  s.lastValidDriveCommandMs = nowMs;
  // 링크 접촉 축(S15P11A301-345 폴백 판정이 보는 값). 스트리밍 중이면 방금 왔다.
  s.lastValidJetsonRxMs = nowMs;
  s.hasReceivedFromJetson = true;
  s.targetDriveLeftMmps = 200;
  s.targetDriveRightMmps = 200;
  s.targetSteeringMdeg = 5000;
  s.maxSteeringRateMdps = 4000;
  return s;
}

// 젯슨 링크가 6초째 침묵인 보드. **폴백 경로는 이 상태에서만 열린다.**
MotorSharedState silentLinkBoard(uint32_t nowMs = 10000) {
  MotorSharedState s = autoBoard(nowMs);
  s.lastValidJetsonRxMs = nowMs - 6000;
  s.lastValidDriveCommandMs = nowMs - 6000;  // 링크가 죽었으니 DRIVE_COMMAND 도 없다
  return s;
}

ManualPacket packet(uint16_t sequence, bool deadman, int16_t lin, int16_t ang,
                    uint32_t sessionId = 0xABCD1234u, uint16_t ttlMs = 250) {
  ManualPacket p{};
  p.sessionId = sessionId;
  p.sequence = sequence;
  p.deadman = deadman;
  p.linMilli = lin;
  p.angMilli = ang;
  p.ttlMs = ttlMs;
  return p;
}

// 관제가 「수동」을 눌러 권한을 넘긴다 - **폰이 권한을 얻는 정상 경로는 이것뿐이다**
// (S15P11A301-345). 이어서 조종 패킷 2개를 넣고 마지막 패킷 시각을 돌려준다.
uint32_t grantManual(MotorSharedState& s, uint32_t startMs) {
  applySetMode(s, SET_MODE_MANUAL, startMs);
  s.manualSessionId = 0xABCD1234u;
  ingestManualPacket(s, packet(1, true, 1000, 0), startMs);
  ingestManualPacket(s, packet(2, true, 1000, 0), startMs + 100);
  return startMs + 100;
}

// 폴백으로 권한을 잡는다. 링크가 침묵한 보드에서 deadman run 을 채우는 것이
// 유일한 경로다.
uint32_t promoteByFallback(MotorSharedState& s, uint32_t startMs) {
  s.manualSessionId = 0xABCD1234u;
  ingestManualPacket(s, packet(1, true, 1000, 0), startMs);
  ingestManualPacket(s, packet(2, true, 1000, 0), startMs + 100);
  return startMs + 100;
}

// ---- 관제 우선 (S15P11A301-345) ----

void testLiveLinkIgnoresPhoneInputEntirely() {
  // 이 티켓이 고치는 사건 그 자체다. 폰 조종 페이지를 열어 둔 사람이 자율 주행을
  // 세우고, 그 사실이 관제·젯슨 어느 화면에도 보이지 않았다.
  MotorSharedState s = autoBoard(10000);
  s.manualSessionId = 0xABCD1234u;

  // 실제로 스틱을 밀고 있다 - 종전 규칙(2패킷·100ms)이라면 여기서 래치가 걸렸다.
  // 그동안 젯슨도 계속 스트리밍한다(두 축 모두 갱신).
  for (uint16_t i = 1; i <= 20; ++i) {
    const uint32_t now = 10000 + i * 50u;
    ingestManualPacket(s, packet(i, true, 1000, 0), now);
    s.lastValidDriveCommandMs = now;
    s.lastValidJetsonRxMs = now;
  }

  expectTrue(!s.manualLatched, "링크가 살아 있으면 폰 입력은 권한을 가져가지 못한다");
  expectTrue(!s.manualFallbackLatched, "폴백도 걸리지 않았다");
  expectTrue(s.manualDriveMmps == 0, "구동 목표는 0 이다 - 장부에만 기록된다");
  expectTrue(s.hasManualInput && s.lastManualInputMs == 11000,
             "기록은 남는다 (관제의 500ms 신선도 가드가 이 값을 본다)");
  expectTrue(s.state == MotorBoardState::AUTO_ACTIVE, "보고 상태도 자율 그대로다");

  DriveDecision d = arbitrateDrive(s, 11010);
  expectTrue(d.owner == DriveOwner::JETSON, "바퀴는 계속 젯슨이 굴린다");
  expectTrue(d.driveLeftMmps == 200, "자율 목표가 그대로 나간다");
}

void testSetModeManualIsTheGrantPath() {
  MotorSharedState s = autoBoard(10000);
  applySetMode(s, SET_MODE_MANUAL, 10000);
  s.manualSessionId = 0xABCD1234u;

  // 관제가 승인했으므로 첫 패킷부터 즉시 바퀴가 돈다 - 2패킷·100ms 를 다시 요구하지
  // 않는다. 그 규칙의 목적은 자율을 오발에서 지키는 것이고, 승인은 오발이 아니다.
  ingestManualPacket(s, packet(1, true, 1000, 0), 10050);

  expectTrue(s.manualLatched, "SET_MODE(MANUAL) 이 권한을 넘긴다");
  expectTrue(!s.manualFallbackLatched, "관제 승인은 폴백이 아니다 - 사유 비트는 0");
  expectTrue(s.manualDriveMmps == MANUAL_MAX_DRIVE_MMPS, "첫 패킷부터 굴린다");
  expectTrue(arbitrateDrive(s, 10060).owner == DriveOwner::MANUAL, "수동이 이긴다");
}

// ---- 링크 침묵 폴백 ----

void testFallbackNeedsBothSilenceAndRealInput() {
  MotorSharedState s = silentLinkBoard(10000);
  s.manualSessionId = 0xABCD1234u;

  // (1) 페이지가 열려만 있는 상태. `/manual/state` 폴링은 이 함수를 아예 부르지
  //     않고, 스틱을 놓은 패킷은 run 을 세우지 못한다.
  ingestManualPacket(s, packet(1, false, 0, 0), 10000);
  ingestManualPacket(s, packet(2, false, 0, 0), 10100);
  expectTrue(!s.manualLatched, "침묵해도 실제 조종 입력이 없으면 권한이 넘어가지 않는다");

  // (2) 스틱을 미는 첫 패킷 하나로는 아직 아니다 (오발 방어).
  ingestManualPacket(s, packet(3, true, 1000, 0), 10150);
  expectTrue(!s.manualLatched, "패킷 하나로는 폴백도 걸리지 않는다");

  // (3) 2패킷·100ms 를 채우면 발동한다.
  ingestManualPacket(s, packet(4, true, 1000, 0), 10250);
  expectTrue(s.manualLatched, "침묵 + 실제 조종이면 폴백이 발동한다");
  expectTrue(s.manualFallbackLatched, "폴백 사유가 래치된다");
  expectTrue(s.state == MotorBoardState::MANUAL_ACTIVE, "보고 상태가 같은 순간에 옮겨간다");
  expectTrue(s.manualDriveMmps == MANUAL_MAX_DRIVE_MMPS, "바퀴를 준다");
}

void testFallbackSilenceThreshold() {
  // 5초는 브리지 재접속 1사이클(2.5초 = 재시도 1.0 + 부팅 정착 1.5)의 두 배다.
  // 경계를 잘못 잡으면 재접속마다 권한이 펄럭인다.
  MotorSharedState s = autoBoard(10000);
  expectTrue(!jetsonLinkSilent(s, 15000), "침묵 5000ms 는 아직 폴백 조건이 아니다");
  expectTrue(jetsonLinkSilent(s, 15001), "5000ms 를 넘기면 침묵이다");

  // 경계 바로 아래에서는 조종 run 을 채워도 권한이 넘어가지 않는다.
  s.manualSessionId = 0xABCD1234u;
  ingestManualPacket(s, packet(1, true, 1000, 0), 14800);
  ingestManualPacket(s, packet(2, true, 1000, 0), 14950);
  expectTrue(!s.manualLatched, "침묵 4950ms 에서는 폴백이 발동하지 않는다");
}

void testJetsonNeverSeenCountsAsSilentAfterBootWindow() {
  // 젯슨 없이 켠 보드. 부팅 직후 5초는 오발 방어 창이고, 그 뒤에는 폰으로 로봇을
  // 옮길 수 있어야 한다 - 잠그면 들어 옮기는 것 말고 복구 경로가 없다(04장 950-953).
  MotorSharedState s;
  expectTrue(!s.hasReceivedFromJetson, "부팅 직후엔 젯슨 접촉 이력이 없다");
  expectTrue(!jetsonLinkSilent(s, 3000), "uptime 3초는 아직 침묵으로 세지 않는다");
  expectTrue(jetsonLinkSilent(s, 6000), "uptime 6초면 「쭉 침묵」이다");
}

void testFallbackDoesNotReturnWhenTheLinkComesBack() {
  MotorSharedState s = silentLinkBoard(10000);
  uint32_t last = promoteByFallback(s, 10000);
  expectTrue(s.manualLatched && s.manualFallbackLatched, "폴백으로 권한을 잡았다");

  // 젯슨이 되살아나 다시 20Hz 로 쏜다. **자동 복귀는 없다** - 사람이 조종 중인데
  // 로봇이 자기 뜻대로 움직이면 안 된다.
  s.lastValidJetsonRxMs = last + 200;
  s.lastValidDriveCommandMs = last + 200;
  ingestManualPacket(s, packet(3, true, 1000, 0), last + 250);

  DriveDecision d = arbitrateDrive(s, last + 260);
  expectTrue(d.owner == DriveOwner::MANUAL, "링크 복구가 권한을 되찾지 않는다");
  expectTrue(s.manualLatched, "래치가 유지된다");
  expectTrue(s.manualFallbackLatched,
             "사유 비트도 유지된다 - 복구 후에 관제가 이것을 봐야 한다");
  expectTrue(!jetsonActuationAllowed(s), "젯슨 명령은 계속 기록만 된다");
}

void testFallbackReasonBitClearsOnlyOnSetModeAuto() {
  MotorSharedState s = silentLinkBoard(10000);
  uint32_t last = promoteByFallback(s, 10000);

  // 바퀴가 서는 경로들은 사유 비트를 내리지 않는다 - 「발동했었다」는 사실이지
  // 「지금 굴리는 중」이 아니다.
  ingestManualPacket(s, packet(3, false, 0, 0), last + 50);
  expectTrue(s.manualFallbackLatched, "deadman 해제는 사유 비트를 내리지 않는다");
  arbitrateDrive(s, last + 500);  // TTL 만료 (순수 함수라 상태를 바꾸지 않는다)
  expectTrue(s.manualFallbackLatched, "TTL 만료도 내리지 않는다");

  // 관제가 권한을 되찾는 순간에만 내려간다.
  expectTrue(applySetMode(s, SET_MODE_AUTO, last + 600) == SetModeResult::ACCEPTED,
             "500ms 창 밖의 SET_MODE(AUTO) 는 수락된다");
  expectTrue(!s.manualLatched, "래치가 풀린다");
  expectTrue(!s.manualFallbackLatched, "사유 비트도 같이 내려간다");
}

// ---- run 판정 (폴백 게이트가 「실제 조종인가」를 가르는 부분) ----

void testGapBreaksTheRun() {
  // 이것이 없으면 "30초 간격 두 패킷" 이 100ms 지속 조건을 만족한다.
  MotorSharedState s = silentLinkBoard();
  s.manualSessionId = 0xABCD1234u;

  ingestManualPacket(s, packet(1, true, 1000, 0), 10000);
  ingestManualPacket(s, packet(2, true, 1000, 0), 40000);  // TTL 250ms 를 크게 넘김

  expectTrue(!s.manualLatched, "TTL 을 넘긴 간격은 run 을 끊는다 (gapExceeded)");
}

void testPromotionIsGatedOnlyOnEntry() {
  MotorSharedState s = autoBoard();
  uint32_t last = grantManual(s, 10000);

  // 손을 뗐다 다시 누른다. 이미 사람이 차량을 소유했으므로 첫 패킷에 즉시 반응해야
  // 한다 - 여기서 100ms 를 더 요구하면 둔한 조향일 뿐 안전 이득이 없다.
  ingestManualPacket(s, packet(3, false, 0, 0), last + 20);
  ingestManualPacket(s, packet(4, true, 1000, 0), last + 40);

  expectTrue(s.manualLatched, "래치는 유지된다");
  expectTrue(s.manualDriveMmps == MANUAL_MAX_DRIVE_MMPS,
             "재누름은 첫 패킷에 즉시 반응한다");
}

// ---- 거부 ----

void testRejectionsLeaveStateUntouched() {
  MotorSharedState s = autoBoard();
  s.manualSessionId = 0xABCD1234u;
  ingestManualPacket(s, packet(5, true, 500, 0), 10000);
  const uint32_t stampAfterAccept = s.lastManualInputMs;

  expectTrue(ingestManualPacket(s, packet(6, true, 0, 0, 0xDEADBEEFu), 10050) ==
                 ManualResult::REJECTED_SESSION,
             "다른 세션의 패킷은 거부한다");
  expectTrue(ingestManualPacket(s, packet(5, true, 0, 0), 10060) ==
                 ManualResult::REJECTED_SEQUENCE,
             "재생·재정렬된 시퀀스는 거부한다");
  expectTrue(ingestManualPacket(s, packet(7, true, 2000, 0), 10070) ==
                 ManualResult::REJECTED_ARGUMENT,
             "정규화 범위 밖 lin 은 거부한다");
  expectTrue(s.lastManualInputMs == stampAfterAccept,
             "거부는 lastManualInputMs 를 건드리지 않는다 (500ms 창이 닫혀야 한다)");

  s.state = MotorBoardState::ESTOP_LATCHED;
  expectTrue(ingestManualPacket(s, packet(8, true, 500, 0), 10080) ==
                 ManualResult::REJECTED_LATCHED,
             "ESTOP 래치 중에는 수동 패킷을 받지 않는다");
}

void testSequenceWraparoundIsAccepted() {
  MotorSharedState s = autoBoard();
  s.manualSessionId = 0xABCD1234u;
  ingestManualPacket(s, packet(65535, true, 500, 0), 10000);

  expectTrue(ingestManualPacket(s, packet(0, true, 500, 0), 10050) ==
                 ManualResult::ACCEPTED,
             "65535 → 0 랩어라운드는 새 시퀀스다");
}

// ---- 조향 사전 필터 ----

void testStationarySteeringIsNotRequested() {
  MotorSharedState s = autoBoard();
  grantManual(s, 10000);

  // 정지 상태에서 좌측만 누른다. steering.cpp 에 보내면 거부되고 그 거부가
  // bit 14 로 올라가 그 비트의 의미가 파괴된다.
  ingestManualPacket(s, packet(9, true, 0, 1000), 10150);

  expectTrue(s.manualDriveMmps == 0, "lin=0 이면 구동도 0 이다");
  expectTrue(!s.manualSteeringRequested,
             "정지 중에는 조향을 요청하지 않는다 (bit 14 오염 방지)");
}

void testDrivingSteeringIsRequestedAndMappedToDeltaMax() {
  MotorSharedState s = autoBoard();
  grantManual(s, 10000);

  ingestManualPacket(s, packet(9, true, 500, 1000), 10150);

  expectTrue(s.manualSteeringRequested, "주행 중에는 조향을 요청한다");
  expectTrue(s.manualSteeringMdeg == STEERING_MAX_MDEG,
             "ang=1000 은 δ_max 로 매핑된다 (슬라이더 끝 == 실제 한계)");
  ingestManualPacket(s, packet(10, true, 500, -1000), 10200);
  expectTrue(s.manualSteeringMdeg == -STEERING_MAX_MDEG, "부호가 보존된다");
}

// ---- SET_MODE ----

void testSetModeManualLatchesAndStopsWheels() {
  MotorSharedState s = autoBoard();

  expectTrue(applySetMode(s, SET_MODE_MANUAL, 10000) == SetModeResult::ACCEPTED,
             "관제 「수동」은 수락된다");
  expectTrue(s.manualLatched, "래치가 걸린다");
  expectTrue(s.state == MotorBoardState::MANUAL_ACTIVE, "상태가 MANUAL_ACTIVE 다");
  expectTrue(s.manualDriveMmps == 0, "아직 폰 입력이 없으므로 구동은 0 이다");
}

void testSetModeAutoIsRejectedInsideTheFreshnessWindow() {
  MotorSharedState s = autoBoard();
  uint32_t last = grantManual(s, 10000);

  // 499ms: 아직 사람이 조종 중이다.
  expectTrue(applySetMode(s, SET_MODE_AUTO, last + 499) == SetModeResult::REJECTED_STATE,
             "500ms 창 안의 SET_MODE(AUTO) 는 거부한다");
  expectTrue(s.manualLatched, "거부는 래치를 유지한다");
  expectTrue(s.state == MotorBoardState::MANUAL_ACTIVE,
             "거부 시 boardState 가 MANUAL_ACTIVE 여야 젯슨이 500ms 가드였음을 안다");

  // 500ms: 조종이 멈췄다.
  expectTrue(applySetMode(s, SET_MODE_AUTO, last + 500) == SetModeResult::ACCEPTED,
             "500ms 창 밖의 SET_MODE(AUTO) 는 수락한다");
  expectTrue(!s.manualLatched, "래치가 풀린다");
  expectTrue(s.state == MotorBoardState::AUTO_ACTIVE, "상태가 AUTO_ACTIVE 다");
  expectTrue(s.manualSessionId == 0,
             "세션을 파괴해 폰이 다시 /manual/session 을 받아야 조종할 수 있다");
  expectTrue(!s.hasManualSequence, "세션이 바뀌므로 시퀀스도 리셋한다");
}

void testSetModeRejectsUndefinedValues() {
  MotorSharedState s = autoBoard();

  // 0(SAFE_IDLE)은 보내는 쪽이 없으므로 거부값이다(D11).
  expectTrue(applySetMode(s, 0, 10000) == SetModeResult::REJECTED_STATE,
             "SET_MODE(0) 은 수락하지 않는다");
  expectTrue(applySetMode(s, 3, 10000) == SetModeResult::REJECTED_STATE,
             "정의되지 않은 값은 거부한다");
  expectTrue(s.state == MotorBoardState::AUTO_ACTIVE, "거부는 상태를 바꾸지 않는다");
}

void testSetModeIsRejectedWhileLatched() {
  MotorSharedState s = autoBoard();
  s.state = MotorBoardState::ESTOP_LATCHED;

  expectTrue(applySetMode(s, SET_MODE_AUTO, 10000) == SetModeResult::REJECTED_STATE,
             "E-Stop 을 모드 전환으로 우회할 수 없다");
  expectTrue(s.state == MotorBoardState::ESTOP_LATCHED, "상태는 불변이다");
}

// ---- 중재 ----

void testJetsonDrivesWhenNoManualLatch() {
  MotorSharedState s = autoBoard(10000);
  DriveDecision d = arbitrateDrive(s, 10010);

  expectTrue(d.owner == DriveOwner::JETSON, "래치가 없으면 젯슨이 굴린다");
  expectTrue(d.driveLeftMmps == 200 && d.driveRightMmps == 200, "젯슨 목표가 그대로 간다");
  expectTrue(d.applySteering && d.reportSteeringFault,
             "젯슨 조향은 적용하고 거부는 bit 14 로 올린다");
  expectTrue(!d.applyNextState, "자율 분기는 상태를 바꾸지 않는다 (D10)");
  expectTrue(d.clearCommTimeout, "정상 스트림은 COMM_TIMEOUT 을 내린다");
}

void testManualWinsOverAStreamingJetson() {
  MotorSharedState s = autoBoard(10000);
  uint32_t last = grantManual(s, 10000);
  s.lastValidDriveCommandMs = last;  // 젯슨도 계속 스트리밍 중

  DriveDecision d = arbitrateDrive(s, last + 10);

  expectTrue(d.owner == DriveOwner::MANUAL, "수동이 이긴다");
  expectTrue(d.driveLeftMmps == MANUAL_MAX_DRIVE_MMPS &&
                 d.driveRightMmps == MANUAL_MAX_DRIVE_MMPS,
             "후륜 좌·우는 같은 값이다");
  expectTrue(d.nextState == MotorBoardState::MANUAL_ACTIVE, "상태는 MANUAL_ACTIVE 다");
  expectTrue(!d.reportSteeringFault, "수동 조향 거부는 bit 14 로 올리지 않는다");
}

void testDeadmanReleaseStopsWheelsButKeepsAuthority() {
  MotorSharedState s = autoBoard(10000);
  uint32_t last = grantManual(s, 10000);
  ingestManualPacket(s, packet(3, false, 0, 0), last + 20);
  s.lastValidDriveCommandMs = last + 20;

  DriveDecision d = arbitrateDrive(s, last + 30);

  expectTrue(d.owner == DriveOwner::MANUAL, "권한은 그대로다");
  expectTrue(d.driveLeftMmps == 0 && d.driveRightMmps == 0, "바퀴는 선다");
  expectTrue(d.nextState == MotorBoardState::MANUAL_ACTIVE,
             "STOPPING 으로 내리면 젯슨이 권한이 풀린 것으로 읽는다");
  expectTrue(s.manualLatched, "deadman 해제는 모드 이탈이 아니다");
}

void testManualTtlExpiryStopsWheelsButKeepsAuthority() {
  MotorSharedState s = autoBoard(10000);
  uint32_t last = grantManual(s, 10000);
  s.lastValidDriveCommandMs = last + 1000;  // 젯슨은 살아 있다

  DriveDecision fresh = arbitrateDrive(s, last + 250);
  DriveDecision stale = arbitrateDrive(s, last + 251);

  expectTrue(fresh.driveLeftMmps == MANUAL_MAX_DRIVE_MMPS, "TTL 경계 안에서는 굴린다");
  expectTrue(stale.driveLeftMmps == 0, "TTL 을 넘기면 바퀴가 선다");
  expectTrue(stale.owner == DriveOwner::MANUAL &&
                 stale.nextState == MotorBoardState::MANUAL_ACTIVE,
             "TTL 만료로 자율이 재개되지 않는다");
}

void testManualSurvivesAStaleJetsonLink() {
  MotorSharedState s = autoBoard(10000);
  uint32_t last = grantManual(s, 10000);
  // 젯슨은 1.2초째 무응답이고(> 300ms), 폰은 100ms 전에 패킷을 보냈다(< 250ms TTL).
  s.lastValidDriveCommandMs = 9000;

  DriveDecision d = arbitrateDrive(s, last + 100);

  expectTrue(d.owner == DriveOwner::MANUAL, "젯슨이 죽어도 수동은 계속 굴린다");
  expectTrue(d.driveLeftMmps == MANUAL_MAX_DRIVE_MMPS, "수동 출력이 보존된다");
  expectTrue(d.raiseCommTimeout, "진단은 사실대로 fault 비트를 올린다");
  expectTrue(d.nextState == MotorBoardState::MANUAL_ACTIVE,
             "워치독이 STOPPING 으로 트립하지 않는다 (CTRL-30)");
}

void testStaleJetsonWithoutManualStops() {
  MotorSharedState s = autoBoard(10000);

  DriveDecision d = arbitrateDrive(s, 10301);

  expectTrue(d.owner == DriveOwner::NONE, "래치가 없으면 워치독이 정지시킨다");
  expectTrue(d.nextState == MotorBoardState::STOPPING, "상태는 STOPPING 이다");
  expectTrue(d.raiseCommTimeout, "COMM_TIMEOUT 을 올린다");
}

void testLatchedBoardActuatesNothingAndHoldsSteering() {
  MotorSharedState s = autoBoard(10000);
  s.state = MotorBoardState::ESTOP_LATCHED;

  DriveDecision d = arbitrateDrive(s, 10010);

  expectTrue(d.owner == DriveOwner::NONE, "래치 상태에서는 아무도 굴리지 않는다");
  expectTrue(!d.applySteering,
             "조향은 미적용 - 정지가 곧 정차가 아니므로 중립으로 꺾지 않는다 (§34-7)");
  expectTrue(!d.applyNextState, "이미 래치 상태이므로 상태를 다시 쓰지 않는다");
}

void testReArmBlocksDriveUntilRelease() {
  MotorSharedState s = autoBoard(10000);
  uint32_t last = grantManual(s, 10000);
  s.lastValidDriveCommandMs = last;

  // 관제·초음파 중계의 STOP_COMMAND 가 세운 것과 같은 상태.
  s.manualReArmRequired = true;
  DriveDecision blocked = arbitrateDrive(s, last + 10);
  expectTrue(blocked.driveLeftMmps == 0, "re-arm 중에는 누르고 있어도 서 있다");
  expectTrue(blocked.owner == DriveOwner::MANUAL, "권한은 유지된다");

  // 누른 채로 패킷이 더 와도 풀리지 않는다.
  ingestManualPacket(s, packet(3, true, 1000, 0), last + 50);
  expectTrue(s.manualReArmRequired, "홀드 중에는 re-arm 이 풀리지 않는다");
  expectTrue(arbitrateDrive(s, last + 60).driveLeftMmps == 0, "여전히 서 있다");

  // 손을 뗐다 다시 누르면 풀린다 - 정지 구역에서 후진으로 빠져나올 수 있어야 한다.
  ingestManualPacket(s, packet(4, false, 0, 0), last + 100);
  expectTrue(!s.manualReArmRequired, "deadman 해제가 re-arm 을 푼다");
  ingestManualPacket(s, packet(5, true, -1000, 0), last + 150);
  DriveDecision reversing = arbitrateDrive(s, last + 160);
  expectTrue(reversing.driveLeftMmps == -MANUAL_MAX_DRIVE_MMPS,
             "다시 누르면 후진으로 탈출할 수 있다");
}

void testJetsonActuationAllowedIsLatchOnly() {
  MotorSharedState s = autoBoard();
  expectTrue(jetsonActuationAllowed(s), "래치가 없으면 젯슨이 액추에이션한다");
  s.manualLatched = true;
  expectTrue(!jetsonActuationAllowed(s), "수동 래치가 유일한 차단 사유다");
}

void testConstantsMatchTheSpec() {
  expectTrue(MANUAL_MAX_DRIVE_MMPS == 300, "수동 상한은 0.30 m/s 다 (docs/04 961)");
  expectTrue(MANUAL_PROMOTION_HOLD_MS == 100 && MANUAL_PROMOTION_PACKETS == 2,
             "폴백 승격 조건은 2패킷·100ms 다");
  expectTrue(MANUAL_FALLBACK_SILENCE_MS == 5000,
             "폴백 침묵 문턱은 5초다 - 브리지 재접속 1사이클(2.5초)보다 커야 "
             "재접속마다 권한이 펄럭이지 않는다 (S15P11A301-345)");
  expectTrue(MANUAL_FALLBACK_SILENCE_MS > JETSON_WATCHDOG_TIMEOUT_MS,
             "권한 이양 문턱은 안전 정지 워치독보다 훨씬 길어야 한다 - 둘은 다른 축이다");
  expectTrue(MANUAL_FRESHNESS_GUARD_MS == 500, "자동 전환 가드는 500ms 다");
  expectTrue(JETSON_WATCHDOG_TIMEOUT_MS == 300, "젯슨 워치독은 300ms 다 (§34-7)");
  expectTrue(MANUAL_STEERING_RATE_MDPS != 0,
             "0 은 「제한 없음」이라 계단 입력이 된다 - 금지값이다");
  expectTrue(!AUTO_REQUIRES_EXPLICIT_SET_MODE,
             "이 커밋에서는 기존 젯슨 파이프라인 동작이 완전히 같아야 한다 (D10)");
}

}  // namespace

int main() {
  testLiveLinkIgnoresPhoneInputEntirely();
  testSetModeManualIsTheGrantPath();
  testFallbackNeedsBothSilenceAndRealInput();
  testFallbackSilenceThreshold();
  testJetsonNeverSeenCountsAsSilentAfterBootWindow();
  testFallbackDoesNotReturnWhenTheLinkComesBack();
  testFallbackReasonBitClearsOnlyOnSetModeAuto();
  testGapBreaksTheRun();
  testPromotionIsGatedOnlyOnEntry();
  testRejectionsLeaveStateUntouched();
  testSequenceWraparoundIsAccepted();
  testStationarySteeringIsNotRequested();
  testDrivingSteeringIsRequestedAndMappedToDeltaMax();
  testSetModeManualLatchesAndStopsWheels();
  testSetModeAutoIsRejectedInsideTheFreshnessWindow();
  testSetModeRejectsUndefinedValues();
  testSetModeIsRejectedWhileLatched();
  testJetsonDrivesWhenNoManualLatch();
  testManualWinsOverAStreamingJetson();
  testDeadmanReleaseStopsWheelsButKeepsAuthority();
  testManualTtlExpiryStopsWheelsButKeepsAuthority();
  testManualSurvivesAStaleJetsonLink();
  testStaleJetsonWithoutManualStops();
  testLatchedBoardActuatesNothingAndHoldsSteering();
  testReArmBlocksDriveUntilRelease();
  testJetsonActuationAllowedIsLatchOnly();
  testConstantsMatchTheSpec();

  if (failureCount == 0) {
    std::printf("\nAll tests passed.\n");
    return 0;
  }
  std::printf("\n%d test(s) failed.\n", failureCount);
  return 1;
}
