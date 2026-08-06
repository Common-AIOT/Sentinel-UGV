#include "comm_task.h"

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include <protocol.h>
#include <fault_codes.h>
#include "board_state.h"
#include "mode_arbiter.h"
#include "safety_stub.h"
#include "steering.h"

namespace {

constexpr uint8_t FW_MAJOR = 0;
constexpr uint8_t FW_MINOR = 1;
constexpr uint8_t FW_PATCH = 0;

constexpr uint32_t DRIVE_STATE_INTERVAL_MS = 20;   // 50Hz (§34-5)
constexpr uint32_t DIAGNOSTIC_INTERVAL_MS = 200;   // 5Hz (§34-5)
constexpr size_t RX_ACCUM_CAP = 256;

uint8_t g_rxAccum[RX_ACCUM_CAP];
size_t g_rxAccumLen = 0;
uint16_t g_outboundSequence = 0;

uint16_t nextOutboundSequence() {
  return g_outboundSequence++;
}

void sendFrame(uint8_t messageType, const uint8_t* payload, uint16_t payloadLen) {
  uint8_t frameBuf[MAX_FRAME_BYTES + 4];
  size_t frameLen = buildFrame(messageType, nextOutboundSequence(), millis(), payload, payloadLen,
                                frameBuf, sizeof(frameBuf));
  if (frameLen > 0) {
    Serial.write(frameBuf, frameLen);
  }
}

void sendHelloAck() {
  MotorSharedState snapshot = motorSharedStateSnapshot();
  HelloAck ack{};
  ack.boardRole = BOARD_ROLE_MOTOR;
  ack.firmwareMajor = FW_MAJOR;
  ack.firmwareMinor = FW_MINOR;
  ack.firmwarePatch = FW_PATCH;
  ack.protocolVersion = PROTOCOL_VERSION;
  ack.boardState = (uint8_t)snapshot.state;
  ack.faultFlags = snapshot.faultFlags;
  ack.reserved = 0;

  uint8_t payload[HELLO_ACK_BYTES];
  size_t len = packHelloAck(ack, payload);
  sendFrame(MSG_HELLO_ACK, payload, (uint16_t)len);
}

void sendCommandAck(uint8_t ackedType, uint16_t ackedSequence, uint8_t result) {
  MotorSharedState snapshot = motorSharedStateSnapshot();
  CommandAck ack{};
  ack.ackedMessageType = ackedType;
  ack.ackedSequence = ackedSequence;
  ack.result = result;
  ack.boardState = (uint8_t)snapshot.state;

  uint8_t payload[COMMAND_ACK_BYTES];
  size_t len = packCommandAck(ack, payload);
  sendFrame(MSG_COMMAND_ACK, payload, (uint16_t)len);
}

// PWM 듀티(-255..255)를 프로토콜 단위인 permille(-1000..1000)로 변환한다.
int16_t pwmToPermille(int16_t pwm) {
  return static_cast<int16_t>((static_cast<int32_t>(pwm) * 1000) / 255);
}

void sendDriveState() {
  MotorSharedState snapshot = motorSharedStateSnapshot();
  DriveState state{};
  // **`lastAcceptedSequence` 가 아니다** (S15P11A301-298). 수동 래치 중에는 젯슨
  // 명령을 받아 두기만 하고 바퀴에 걸지 않으므로, 수락 시퀀스를 보고하면 "반영
  // 했다"고 거짓말하게 된다. CTRL-29 가 래치 중 이 값의 동결을 확인한다.
  state.appliedSequence = snapshot.lastAppliedSequence;
  state.state = (uint8_t)snapshot.state;
  state.faultFlags = snapshot.faultFlags;
  state.drivePwmLeftPermille = pwmToPermille(motorDriverAppliedPwmLeft());
  state.drivePwmRightPermille = pwmToPermille(motorDriverAppliedPwmRight());
  // §34-5: target은 슬루레이트 제한 후 실제 추종 중인 목표, actuator는 서보에
  // 내보낸 펄스폭(µs)이다. 조향각 피드백이 없으므로(개루프) 이 둘이 Jetson이 볼 수
  // 있는 조향 상태의 전부다.
  state.targetSteeringMdeg = steeringTargetMdeg();
  state.steeringActuatorCmd = steeringActuatorCmdUs();
  state.estopActive = (snapshot.state == MotorBoardState::ESTOP_LATCHED) ? 1 : 0;
  state.driverEnabled = motorDriverEnabled() ? 1 : 0;

  uint8_t payload[DRIVE_STATE_BYTES];
  size_t len = packDriveState(state, payload);
  sendFrame(MSG_DRIVE_STATE, payload, (uint16_t)len);
}

void sendDiagnostic() {
  MotorSharedState snapshot = motorSharedStateSnapshot();
  Diagnostic diag{};
  diag.boardRole = BOARD_ROLE_MOTOR;
  diag.boardState = (uint8_t)snapshot.state;
  diag.faultFlags = snapshot.faultFlags;
  diag.crcErrorCount = snapshot.crcErrorCount;
  diag.droppedFrameCount = snapshot.droppedFrameCount;
  diag.staleSequenceCount = snapshot.staleSequenceCount;
  diag.freeHeapBytes = ESP.getFreeHeap();

  uint8_t payload[DIAGNOSTIC_BYTES];
  size_t len = packDiagnostic(diag, payload);
  sendFrame(MSG_DIAGNOSTIC, payload, (uint16_t)len);
}

void handleHello() {
  // ESTOP_LATCHED 상태에서 새 HELLO를 받으면 SAFE_IDLE로 복귀한다(§34-6: handshake는
  // 상태를 자동 복원하지 않는다 - 여기서도 AUTO/MANUAL이 아니라 SAFE_IDLE까지만 되돌린다).
  // 물리 E-Stop 입력 판독 배선은 후속 하드웨어 티켓 몫이라, 이번 스텁은 FAULT_ESTOP_ACTIVE
  // 비트가 이미 꺼져 있는지만으로 판단한다.
  //
  // **수동 래치는 건드리지 않는다** (S15P11A301-298). 젯슨 프로세스 재시작이
  // 조작 중인 사람에게서 바퀴를 빼앗아선 안 된다. 진짜 보드 리부트는 RAM 이 날아가
  // 래치가 함께 사라지므로 「스테일 래치」 문제는 존재하지 않는다.
  motorSharedStateUpdate([](MotorSharedState& s) {
    if (s.state == MotorBoardState::ESTOP_LATCHED && (s.faultFlags & FAULT_ESTOP_ACTIVE) == 0) {
      s.state = MotorBoardState::SAFE_IDLE;
    }
  });
  sendHelloAck();
}

// 이 핸들러는 **액추에이션을 하지 않는다** (S15P11A301-298). 목표만 기록하고 실제
// 반영은 control_task 의 arbitrateDrive() 가 100Hz 로 결정한다. 바퀴 소유자를 정하는
// 틱이 하나여야 core 0 의 HTTP 핸들러와 경쟁하지 않는다.
//
// 핵심 재배열: **링크 수락과 액추에이션 권한을 분리한다.** 젯슨 명령의 액추에이션
// 거부는 통신 실패가 아니므로 `lastValidDriveCommandMs` 는 권한과 무관하게 항상
// 갱신한다. 이 한 줄이 "수동 중 300ms 워치독이 STOPPING 으로 트립하지 않는다" 를
// 참으로 만든다(CTRL-30).
void handleDriveCommand(const uint8_t* payload, uint16_t len, uint16_t sequence) {
  DriveCommand cmd{};
  if (!unpackDriveCommand(payload, len, cmd)) {
    motorSharedStateUpdate([](MotorSharedState& s) { s.droppedFrameCount++; });
    return;
  }

  bool sendStaleAck = false;
  bool sendRefusedAck = false;
  motorSharedStateUpdate([&](MotorSharedState& s) {
    const uint32_t now = millis();
    if (s.hasAcceptedSequence && !isSequenceNewer(sequence, s.lastAcceptedSequence)) {
      s.staleSequenceCount++;
      // 20Hz 스트림이 통째로 밀리면 초당 20개의 ACK 가 나간다. 그것은 진단이
      // 아니라 소음이므로 200ms 로 레이트리밋한다.
      if (!s.hasStaleAck || now - s.lastStaleAckMs >= STALE_ACK_MIN_INTERVAL_MS) {
        s.lastStaleAckMs = now;
        s.hasStaleAck = true;
        sendStaleAck = true;
      }
      return;
    }
    if (s.state == MotorBoardState::ESTOP_LATCHED || s.state == MotorBoardState::FAULT_LATCHED) {
      return;  // 래치 상태에서는 DRIVE_COMMAND를 반영하지 않는다.
    }

    // ---- 링크 신선도. 액추에이션 권한과 무관하게 항상 갱신한다. ----
    s.lastAcceptedSequence = sequence;
    s.hasAcceptedSequence = true;
    s.lastValidDriveCommandMs = now;
    s.faultFlags &= ~FAULT_COMM_TIMEOUT_MOTOR;

    // ---- 목표만 기록. 반영 여부는 control_task 가 정한다. ----
    s.targetDriveLeftMmps = cmd.targetDriveLeftMmps;
    s.targetDriveRightMmps = cmd.targetDriveRightMmps;
    s.targetSteeringMdeg = cmd.targetSteeringMdeg;
    s.maxSteeringRateMdps = cmd.maxSteeringRateMdps;
    s.commandTimeoutMs = cmd.commandTimeoutMs;

    if (!jetsonActuationAllowed(s)) {
      s.jetsonRefusedCount++;
      // 엣지 게이팅. 50Hz REJECTED 홍수를 막되 "거부가 시작됐다" 는 한 번은
      // 반드시 알린다 - 오늘은 거부된 STOP_COMMAND 조차 완전 무음이다.
      if (!s.jetsonRejectAcked) {
        s.jetsonRejectAcked = true;
        sendRefusedAck = true;
      }
      return;
    }
    s.jetsonRejectAcked = false;
    // D10: AUTO_REQUIRES_EXPLICIT_SET_MODE 가 false 인 동안은 mode 바이트가 계속
    // 상태를 올린다. SET_MODE 의 유일한 책무는 래치 해제이며, 그 덕에 이 커밋이
    // 기존 젯슨 파이프라인의 동작을 바꾸지 않는다.
    switch (cmd.mode) {
      case 0: s.state = MotorBoardState::SAFE_IDLE; break;
      case 1: s.state = MotorBoardState::MANUAL_ACTIVE; break;
      case 2: s.state = MotorBoardState::AUTO_ACTIVE; break;
      default: break;
    }
  });

  if (sendStaleAck) {
    sendCommandAck(MSG_DRIVE_COMMAND, sequence, ACK_RESULT_REJECTED_STALE_SEQUENCE);
  }
  if (sendRefusedAck) {
    sendCommandAck(MSG_DRIVE_COMMAND, sequence, ACK_RESULT_REJECTED_STATE);
  }
}

// 모드 전환 원샷 명령(0x13). 페이로드가 깨졌으면 ACK 를 내지 않고 카운터만 올린다 -
// 어느 명령에 대한 답인지 모르는 ACK 는 젯슨 쪽 상관을 망친다.
void handleSetMode(const uint8_t* payload, uint16_t len, uint16_t sequence) {
  SetMode request{};
  if (!unpackSetMode(payload, len, request)) {
    motorSharedStateUpdate([](MotorSharedState& s) { s.droppedFrameCount++; });
    return;
  }

  SetModeResult result = SetModeResult::REJECTED_STATE;
  motorSharedStateUpdate([&](MotorSharedState& s) {
    result = applySetMode(s, request.requestedMode, millis());
  });

  // 수락된 전환은 어느 방향이든 바퀴를 0 으로 만든다. MANUAL 진입은 아직 폰 입력이
  // 없고, AUTO 복귀는 다음 DRIVE_COMMAND 까지 굴릴 근거가 없다.
  if (result == SetModeResult::ACCEPTED) {
    applySafeOutputs();
  }
  sendCommandAck(MSG_SET_MODE, sequence,
                 result == SetModeResult::ACCEPTED ? ACK_RESULT_ACCEPTED
                                                    : ACK_RESULT_REJECTED_STATE);
}

// STOP/ESTOP 모두 구동만 끊고 **조향각은 마지막 목표를 유지한다**(§34-7). 물리
// E-Stop만이 서보 전원을 끊어 조향을 무여자로 만들며, 그것은 소프트웨어가 관여할
// 수 없는 경로다(§21.4).
void handleStopCommand(uint16_t sequence) {
  applySafeOutputs();
  motorSharedStateUpdate([](MotorSharedState& s) {
    s.targetDriveLeftMmps = 0;
    s.targetDriveRightMmps = 0;
    if (s.manualLatched) {
      // 수동 권한은 **막지 않는다**. 바퀴만 세우고 손을 떼었다 다시 눌러야
      // 움직이게 한다(S15P11A301-298). 상태를 STOPPING 으로 내리면 젯슨이
      // 「권한이 풀렸다」로 읽고 스트리밍을 시작한다.
      s.manualReArmRequired = true;
      s.manualDriveMmps = 0;
      s.manualSteeringRequested = false;
      s.state = MotorBoardState::MANUAL_ACTIVE;
      return;
    }
    s.state = MotorBoardState::STOPPING;
  });
  sendCommandAck(MSG_STOP_COMMAND, sequence, ACK_RESULT_ACCEPTED);
}

void handleEstopCommand(uint16_t sequence) {
  applySafeOutputs();
  motorSharedStateUpdate([](MotorSharedState& s) {
    s.targetDriveLeftMmps = 0;
    s.targetDriveRightMmps = 0;
    s.state = MotorBoardState::ESTOP_LATCHED;
    s.faultFlags |= FAULT_ESTOP_ACTIVE;
    // E-Stop 은 수동 권한을 **막는 게 아니라 벗긴다**. 래치와 세션을 함께 파괴해
    // 폰이 새 세션을 받기 전에는 아무것도 못 하게 한다. 모든 것을 무효화하는
    // 최상위 안전 장치이므로 다른 정지 경로와 다른 층에 있다.
    s.manualLatched = false;
    s.manualReArmRequired = false;
    s.manualDeadman = false;
    s.manualSessionId = 0;
    s.hasManualSequence = false;
    s.manualDriveMmps = 0;
    s.manualSteeringRequested = false;
    s.manualRunActive = false;
    s.manualRunPackets = 0;
  });
  sendCommandAck(MSG_ESTOP_COMMAND, sequence, ACK_RESULT_ACCEPTED);
}

void handleConfig(const uint8_t* payload, uint16_t len) {
  ConfigMessage msg{};
  if (!unpackConfigMessage(payload, len, msg)) return;

  // 키 테이블이 아직 정의되지 않아(§8 참고) 이번 티켓은 항상 value=0으로 응답한다.
  ConfigMessage reply{};
  reply.operation = msg.operation;
  reply.keyId = msg.keyId;
  reply.value = 0;

  uint8_t out[CONFIG_MESSAGE_BYTES];
  size_t outLen = packConfigMessage(reply, out);
  sendFrame(MSG_CONFIG, out, (uint16_t)outLen);
}

void dispatchFrame(const uint8_t* cobsFrame, size_t len) {
  FrameHeader header{};
  uint8_t payload[MAX_PAYLOAD_BYTES];
  ParseResult result = parseFrame(cobsFrame, len, header, payload, sizeof(payload));

  if (result == ParseResult::BAD_CRC) {
    motorSharedStateUpdate([](MotorSharedState& s) { s.crcErrorCount++; });
    return;
  }
  if (result != ParseResult::OK) {
    // BAD_VERSION / BAD_LENGTH / UNKNOWN_TYPE / COBS_ERROR: 실행하지 않고 카운터만 증가.
    motorSharedStateUpdate([](MotorSharedState& s) { s.droppedFrameCount++; });
    return;
  }

  switch (header.messageType) {
    case MSG_HELLO: handleHello(); break;
    case MSG_DRIVE_COMMAND: handleDriveCommand(payload, header.payloadLength, header.sequence); break;
    case MSG_STOP_COMMAND: handleStopCommand(header.sequence); break;
    case MSG_ESTOP_COMMAND: handleEstopCommand(header.sequence); break;
    case MSG_SET_MODE: handleSetMode(payload, header.payloadLength, header.sequence); break;
    case MSG_CONFIG: handleConfig(payload, header.payloadLength); break;
    default: break;  // 모터 보드가 받을 일 없는 타입(텔레메트리류 등)은 무시
  }
}

void pollSerial() {
  while (Serial.available() > 0) {
    uint8_t byte = (uint8_t)Serial.read();
    if (byte == 0x00) {
      if (g_rxAccumLen > 0) {
        dispatchFrame(g_rxAccum, g_rxAccumLen);
      }
      g_rxAccumLen = 0;
      continue;
    }
    if (g_rxAccumLen >= RX_ACCUM_CAP) {
      // 구분자 없이 버퍼가 가득 참 - 프레이밍 유실로 보고 리셋한다.
      motorSharedStateUpdate([](MotorSharedState& s) { s.droppedFrameCount++; });
      g_rxAccumLen = 0;
      continue;
    }
    g_rxAccum[g_rxAccumLen++] = byte;
  }
}

}  // namespace

void commTaskFn(void* pvParameters) {
  (void)pvParameters;
  Serial.begin(921600);

  uint32_t lastDriveStateMs = 0;
  uint32_t lastDiagnosticMs = 0;

  for (;;) {
    pollSerial();

    uint32_t now = millis();
    if (now - lastDriveStateMs >= DRIVE_STATE_INTERVAL_MS) {
      lastDriveStateMs = now;
      sendDriveState();
    }
    if (now - lastDiagnosticMs >= DIAGNOSTIC_INTERVAL_MS) {
      lastDiagnosticMs = now;
      sendDiagnostic();
    }

    vTaskDelay(pdMS_TO_TICKS(1));
  }
}
