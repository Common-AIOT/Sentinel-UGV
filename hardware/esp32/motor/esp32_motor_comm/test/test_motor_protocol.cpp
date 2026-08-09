// 모터 전용 프레이밍(motor_protocol.h) 호스트 유닛테스트 (S15P11A301-321).
//
// Arduino 의존성이 없으므로 그대로 g++로 빌드된다. jetson-comm의 pack/unpack과
// LE 헬퍼를 재사용하므로 protocol.cpp도 함께 링크한다.
//
// 빌드 (이 폴더에서):
//   g++ -std=c++17 -I.. -I../../../jetson-comm/src \
//       test_motor_protocol.cpp ../motor_protocol.cpp \
//       ../../../jetson-comm/src/protocol.cpp -o test_motor_protocol
//   ./test_motor_protocol
//
// Arduino IDE는 스케치 폴더의 test/를 컴파일하지 않으므로 여기 두어도 안전하다
// (jetson-comm, test_mode_arbiter.cpp와 같은 방식).

#include <cstdio>
#include <cstring>

#include "../motor_protocol.h"

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

void expectEqU8(uint8_t actual, uint8_t expected, const char* what) {
  if (actual != expected) {
    std::printf("FAIL: %s (actual=0x%02x expected=0x%02x)\n", what, actual, expected);
    failureCount++;
  } else {
    std::printf("PASS: %s\n", what);
  }
}

// ---- CRC-8: 손계산으로 검증한 벡터 (motor_protocol.h 헤더 주석 참고) ----
//
// poly=0x07, init=0x00. 단일 비트 1개짜리 바이트는 그 비트가 최상위까지 밀려
// 올라가는 시프트 횟수만큼만 다항식이 XOR돼 들어가므로 손으로 추적 가능하다:
//   crc8([0x01]): 7번 시프트 후 최상위 비트에 도달 -> 8번째 시프트에서 poly가
//                 그대로 XOR돼 결과는 poly 자신, 0x07.
//   crc8([0x02]): 6번째 시프트에서 도달 -> 결과는 0x07을 한 번 더 시프트한
//                 0x0E (그 뒤 시프트에서 최상위 비트가 서지 않아 poly가 다시
//                 섞이지 않는다).
void testCrc8HandVerifiedVectors() {
  const uint8_t one[] = {0x01};
  const uint8_t two[] = {0x02};
  expectEqU8(motorCrc8(one, 1), 0x07, "crc8([0x01]) == 0x07 (hand-derived)");
  expectEqU8(motorCrc8(two, 1), 0x0E, "crc8([0x02]) == 0x0E (hand-derived)");
  expectEqU8(motorCrc8(nullptr, 0), 0x00, "crc8([]) == init value(0x00)");
}

void testSequenceWraparound() {
  expectTrue(isMotorSequenceNewer(1, 0), "seq 1 is newer than 0");
  expectTrue(!isMotorSequenceNewer(0, 0), "seq 0 is not newer than itself");
  expectTrue(isMotorSequenceNewer(0, 255), "seq 0 is newer than 255 (wrap)");
  expectTrue(!isMotorSequenceNewer(255, 0), "seq 255 is not newer than 0 (wrapped past)");
}

void testFixedSizes() {
  expectTrue(MOTOR_PAYLOAD_BYTES == 22, "MOTOR_PAYLOAD_BYTES == 22 (fits DIAGNOSTIC)");
  expectTrue(MOTOR_FRAME_BYTES == 27, "MOTOR_FRAME_BYTES == 27 (2 sync+1 type+1 seq+22 payload+1 crc)");
  expectTrue(MOTOR_DIAGNOSTIC_BYTES == 22, "MOTOR_DIAGNOSTIC_BYTES == 22");
  expectTrue(MOTOR_DRIVE_STATE_BYTES == 16, "MOTOR_DRIVE_STATE_BYTES == 16 (15 + authorityFlags)");
}

void testBuildParseRoundTripHello() {
  uint8_t frame[MOTOR_FRAME_BYTES];
  size_t len = buildMotorFrame(MSG_HELLO, 7, nullptr, 0, frame, sizeof(frame));
  expectTrue(len == MOTOR_FRAME_BYTES, "HELLO build produces a full-size frame");

  uint8_t msgType = 0, seq = 0;
  uint8_t payload[MOTOR_PAYLOAD_BYTES];
  MotorParseResult result = parseMotorFrame(frame, len, msgType, seq, payload, sizeof(payload));
  expectTrue(result == MotorParseResult::OK, "HELLO frame parses OK");
  expectEqU8(msgType, MSG_HELLO, "HELLO round-trip message type");
  expectEqU8(seq, 7, "HELLO round-trip sequence");

  bool allZero = true;
  for (size_t i = 0; i < MOTOR_PAYLOAD_BYTES; ++i) {
    if (payload[i] != 0) allZero = false;
  }
  expectTrue(allZero, "0-length payload is zero-padded on the wire");
}

void testBuildParseRoundTripDriveCommand() {
  DriveCommand cmd{};
  cmd.mode = 2;
  cmd.flags = 0;
  cmd.targetDriveLeftMmps = 250;
  cmd.targetDriveRightMmps = -250;
  cmd.targetSteeringMdeg = 12345;
  cmd.maxAccelMmps2 = 500;
  cmd.maxSteeringRateMdps = 60000;
  cmd.commandTimeoutMs = 300;

  uint8_t rawPayload[DRIVE_COMMAND_BYTES];
  size_t rawLen = packDriveCommand(cmd, rawPayload);
  expectTrue(rawLen == DRIVE_COMMAND_BYTES, "packDriveCommand produces 14 bytes");

  uint8_t frame[MOTOR_FRAME_BYTES];
  size_t frameLen = buildMotorFrame(MSG_DRIVE_COMMAND, 42, rawPayload, rawLen, frame, sizeof(frame));
  expectTrue(frameLen == MOTOR_FRAME_BYTES, "DRIVE_COMMAND build produces a full-size frame");

  uint8_t msgType = 0, seq = 0;
  uint8_t payload[MOTOR_PAYLOAD_BYTES];
  MotorParseResult result = parseMotorFrame(frame, frameLen, msgType, seq, payload, sizeof(payload));
  expectTrue(result == MotorParseResult::OK, "DRIVE_COMMAND frame parses OK");
  expectEqU8(msgType, MSG_DRIVE_COMMAND, "DRIVE_COMMAND round-trip message type");
  expectEqU8(seq, 42, "DRIVE_COMMAND round-trip sequence");

  DriveCommand out{};
  bool unpacked = unpackDriveCommand(payload, DRIVE_COMMAND_BYTES, out);
  expectTrue(unpacked, "unpackDriveCommand accepts the fixed-length prefix of the padded payload");
  expectTrue(out.mode == cmd.mode && out.targetDriveLeftMmps == cmd.targetDriveLeftMmps &&
                 out.targetDriveRightMmps == cmd.targetDriveRightMmps &&
                 out.targetSteeringMdeg == cmd.targetSteeringMdeg &&
                 out.maxAccelMmps2 == cmd.maxAccelMmps2 &&
                 out.maxSteeringRateMdps == cmd.maxSteeringRateMdps &&
                 out.commandTimeoutMs == cmd.commandTimeoutMs,
             "DRIVE_COMMAND fields survive the sync+CRC8 framing unchanged");
}

// ---- DRIVE_STATE 권한 확장 (S15P11A301-345) ----

DriveState sampleDriveState() {
  DriveState state{};
  state.appliedSequence = 4321;
  state.state = 3;  // MANUAL_ACTIVE
  state.faultFlags = 0x0001;
  state.drivePwmLeftPermille = -400;
  state.drivePwmRightPermille = -400;
  state.targetSteeringMdeg = -12000;
  state.steeringActuatorCmd = 1400;
  state.estopActive = 0;
  state.driverEnabled = 1;
  return state;
}

void testMotorDriveStateRoundTrip() {
  const DriveState state = sampleDriveState();

  uint8_t payload[MOTOR_DRIVE_STATE_BYTES];
  size_t len = packMotorDriveState(state, AUTHORITY_FLAG_MANUAL_FALLBACK, payload);
  expectTrue(len == MOTOR_DRIVE_STATE_BYTES, "packMotorDriveState produces 16 bytes (15 + authority)");
  expectTrue(MOTOR_DRIVE_STATE_BYTES <= MOTOR_PAYLOAD_BYTES,
             "the extra byte still fits the fixed 22-byte payload (no frame change)");

  DriveState out{};
  uint8_t flags = 0xFF;
  bool ok = unpackMotorDriveState(payload, MOTOR_DRIVE_STATE_BYTES, out, flags);
  expectTrue(ok, "unpackMotorDriveState accepts its own fixed length");
  expectTrue(out.appliedSequence == state.appliedSequence && out.state == state.state &&
                 out.faultFlags == state.faultFlags &&
                 out.drivePwmLeftPermille == state.drivePwmLeftPermille &&
                 out.drivePwmRightPermille == state.drivePwmRightPermille &&
                 out.targetSteeringMdeg == state.targetSteeringMdeg &&
                 out.steeringActuatorCmd == state.steeringActuatorCmd &&
                 out.estopActive == state.estopActive &&
                 out.driverEnabled == state.driverEnabled,
             "the 15-byte DriveState prefix round-trips unchanged");
  expectEqU8(flags, AUTHORITY_FLAG_MANUAL_FALLBACK, "authorityFlags round-trips");

  expectTrue(!unpackMotorDriveState(payload, DRIVE_STATE_BYTES, out, flags),
             "the old 15-byte length is not accepted by the extended unpack");
}

// 이 티켓의 호환성 주장 전부가 여기에 걸려 있다. 확장 바이트는 **뒤에** 붙으므로
// 앞 15바이트는 구 젯슨 디코더가 읽던 것과 정확히 같아야 한다 - 한 바이트라도
// 밀리면 관제 화면의 PWM·조향값이 조용히 틀려진다.
void testAuthorityByteIsAppendedNotInserted() {
  const DriveState state = sampleDriveState();

  uint8_t legacy[DRIVE_STATE_BYTES];
  packDriveState(state, legacy);

  uint8_t extended[MOTOR_DRIVE_STATE_BYTES];
  packMotorDriveState(state, AUTHORITY_FLAG_MANUAL_FALLBACK, extended);

  expectTrue(std::memcmp(legacy, extended, DRIVE_STATE_BYTES) == 0,
             "the first 15 bytes are byte-identical to packDriveState (old decoders unaffected)");
  expectEqU8(extended[DRIVE_STATE_BYTES], AUTHORITY_FLAG_MANUAL_FALLBACK,
             "the authority byte sits at offset 15, in what used to be zero padding");

  // 폴백이 아닌 평시에는 0 이다 - 구 디코더가 보던 패딩 값과 같다.
  packMotorDriveState(state, 0, extended);
  expectEqU8(extended[DRIVE_STATE_BYTES], 0,
             "no fallback -> the byte reads 0, exactly the padding old firmware sent");
}

void testMotorDiagnosticRoundTrip() {
  MotorDiagnostic diag{};
  diag.boardRole = BOARD_ROLE_MOTOR;
  diag.boardState = 4;
  diag.faultFlags = 0x0021;
  diag.crcErrorCount = 3;
  diag.droppedFrameCount = 5;
  diag.staleSequenceCount = 7;
  diag.freeHeapBytes = 123456;
  diag.linkSilenceMs = 42;

  uint8_t payload[MOTOR_DIAGNOSTIC_BYTES];
  size_t len = packMotorDiagnostic(diag, payload);
  expectTrue(len == MOTOR_DIAGNOSTIC_BYTES, "packMotorDiagnostic produces 22 bytes");

  MotorDiagnostic out{};
  bool ok = unpackMotorDiagnostic(payload, MOTOR_DIAGNOSTIC_BYTES, out);
  expectTrue(ok, "unpackMotorDiagnostic accepts its own fixed length");
  expectTrue(out.boardRole == diag.boardRole && out.boardState == diag.boardState &&
                 out.faultFlags == diag.faultFlags && out.crcErrorCount == diag.crcErrorCount &&
                 out.droppedFrameCount == diag.droppedFrameCount &&
                 out.staleSequenceCount == diag.staleSequenceCount &&
                 out.freeHeapBytes == diag.freeHeapBytes && out.linkSilenceMs == diag.linkSilenceMs,
             "MotorDiagnostic (incl. linkSilenceMs) round-trips");
}

void testCorruptedCrcIsRejected() {
  uint8_t frame[MOTOR_FRAME_BYTES];
  buildMotorFrame(MSG_STOP_COMMAND, 1, nullptr, 0, frame, sizeof(frame));
  frame[10] ^= 0xFF;  // payload 영역 한 바이트를 뒤집는다

  uint8_t msgType = 0, seq = 0;
  uint8_t payload[MOTOR_PAYLOAD_BYTES];
  MotorParseResult result = parseMotorFrame(frame, sizeof(frame), msgType, seq, payload, sizeof(payload));
  expectTrue(result == MotorParseResult::BAD_CRC, "single-byte corruption is caught by CRC-8");
}

void testBadSyncIsRejected() {
  uint8_t frame[MOTOR_FRAME_BYTES];
  buildMotorFrame(MSG_ESTOP_COMMAND, 1, nullptr, 0, frame, sizeof(frame));
  frame[0] = 0x00;  // 동기 워드를 깬다

  uint8_t msgType = 0, seq = 0;
  uint8_t payload[MOTOR_PAYLOAD_BYTES];
  MotorParseResult result = parseMotorFrame(frame, sizeof(frame), msgType, seq, payload, sizeof(payload));
  expectTrue(result == MotorParseResult::BAD_SYNC, "wrong sync bytes are rejected before CRC is even checked");
}

void testWrongLengthIsRejected() {
  uint8_t frame[MOTOR_FRAME_BYTES];
  buildMotorFrame(MSG_HELLO, 1, nullptr, 0, frame, sizeof(frame));

  uint8_t msgType = 0, seq = 0;
  uint8_t payload[MOTOR_PAYLOAD_BYTES];
  MotorParseResult result = parseMotorFrame(frame, sizeof(frame) - 1, msgType, seq, payload, sizeof(payload));
  expectTrue(result == MotorParseResult::BAD_LENGTH, "a truncated frame is rejected by length, not mistaken for garbage");
}

// comm_task.cpp의 feedByte()와 동일한 슬라이딩 윈도우 알고리즘을 여기 재구현해,
// "가비지 뒤에 온 진짜 프레임이 결국 복구되는가"를 통합 시나리오로 확인한다.
// (comm_task.cpp의 실제 함수는 익명 네임스페이스라 직접 링크해 재사용할 수 없다 -
//  알고리즘이 바뀌면 이 사본도 함께 고칠 것.)
void testResyncAfterGarbageRecoversNextFrame() {
  uint8_t frame[MOTOR_FRAME_BYTES];
  buildMotorFrame(MSG_HELLO, 99, nullptr, 0, frame, sizeof(frame));

  // 진짜 프레임 앞에 동기 워드와 절대 안 겹치는 가비지 5바이트를 흘려 보낸다.
  const uint8_t garbage[] = {0x11, 0x22, 0x33, 0x44, 0x55};

  uint8_t window[MOTOR_FRAME_BYTES];
  size_t windowLen = 0;
  bool dispatched = false;
  uint8_t dispatchedType = 0, dispatchedSeq = 0;

  auto feed = [&](uint8_t byte) {
    if (windowLen < MOTOR_FRAME_BYTES) {
      window[windowLen++] = byte;
    } else {
      std::memmove(window, window + 1, MOTOR_FRAME_BYTES - 1);
      window[MOTOR_FRAME_BYTES - 1] = byte;
    }
    if (windowLen < MOTOR_FRAME_BYTES) return;

    uint8_t msgType = 0, seq = 0;
    uint8_t payload[MOTOR_PAYLOAD_BYTES];
    MotorParseResult result = parseMotorFrame(window, MOTOR_FRAME_BYTES, msgType, seq, payload, sizeof(payload));
    if (result == MotorParseResult::OK) {
      dispatched = true;
      dispatchedType = msgType;
      dispatchedSeq = seq;
      windowLen = 0;
    }
    // 실패하면 windowLen을 그대로 두어 다음 바이트가 1바이트 밀게 한다.
  };

  for (uint8_t b : garbage) feed(b);
  for (size_t i = 0; i < MOTOR_FRAME_BYTES; ++i) feed(frame[i]);

  expectTrue(dispatched, "sliding window resyncs past leading garbage and finds the real frame");
  expectEqU8(dispatchedType, MSG_HELLO, "resynced frame reports the correct message type");
  expectEqU8(dispatchedSeq, 99, "resynced frame reports the correct sequence");
}

}  // namespace

int main() {
  testCrc8HandVerifiedVectors();
  testSequenceWraparound();
  testFixedSizes();
  testBuildParseRoundTripHello();
  testBuildParseRoundTripDriveCommand();
  testMotorDriveStateRoundTrip();
  testAuthorityByteIsAppendedNotInserted();
  testMotorDiagnosticRoundTrip();
  testCorruptedCrcIsRejected();
  testBadSyncIsRejected();
  testWrongLengthIsRejected();
  testResyncAfterGarbageRecoversNextFrame();

  if (failureCount == 0) {
    std::printf("\nAll tests passed.\n");
    return 0;
  }
  std::printf("\n%d test(s) failed.\n", failureCount);
  return 1;
}
