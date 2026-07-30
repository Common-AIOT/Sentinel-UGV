#include "comm_task.h"

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "../../jetson-comm/protocol.h"
#include "../../jetson-comm/fault_codes.h"
#include "board_state.h"
#include "safety_stub.h"

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

void sendDriveState() {
  MotorSharedState snapshot = motorSharedStateSnapshot();
  DriveState state{};
  state.appliedSequence = snapshot.lastAcceptedSequence;
  state.state = (uint8_t)snapshot.state;
  state.faultFlags = snapshot.faultFlags;
  // 실제 BTS7960 PWM 값은 후속 티켓 몫이라, 통신 계층 검증용으로 목표값을 그대로 반영한다.
  state.drivePwmLeftPermille = snapshot.targetDriveLeftMmps;
  state.drivePwmRightPermille = snapshot.targetDriveRightMmps;
  state.targetSteeringMdeg = snapshot.targetSteeringMdeg;
  state.steeringActuatorCmd = snapshot.targetSteeringMdeg;
  state.estopActive = (snapshot.state == MotorBoardState::ESTOP_LATCHED) ? 1 : 0;
  state.driverEnabled = 0;  // 실제 driver enable 배선 전이라 항상 0

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
  motorSharedStateUpdate([](MotorSharedState& s) {
    if (s.state == MotorBoardState::ESTOP_LATCHED && (s.faultFlags & FAULT_ESTOP_ACTIVE) == 0) {
      s.state = MotorBoardState::SAFE_IDLE;
    }
  });
  sendHelloAck();
}

void handleDriveCommand(const uint8_t* payload, uint16_t len, uint16_t sequence) {
  DriveCommand cmd{};
  if (!unpackDriveCommand(payload, len, cmd)) {
    motorSharedStateUpdate([](MotorSharedState& s) { s.droppedFrameCount++; });
    return;
  }

  bool accepted = false;
  motorSharedStateUpdate([&](MotorSharedState& s) {
    if (s.hasAcceptedSequence && !isSequenceNewer(sequence, s.lastAcceptedSequence)) {
      s.staleSequenceCount++;
      return;
    }
    if (s.state == MotorBoardState::ESTOP_LATCHED || s.state == MotorBoardState::FAULT_LATCHED) {
      return;  // 래치 상태에서는 DRIVE_COMMAND를 반영하지 않는다.
    }
    s.lastAcceptedSequence = sequence;
    s.hasAcceptedSequence = true;
    s.targetDriveLeftMmps = cmd.targetDriveLeftMmps;
    s.targetDriveRightMmps = cmd.targetDriveRightMmps;
    s.targetSteeringMdeg = cmd.targetSteeringMdeg;
    s.commandTimeoutMs = cmd.commandTimeoutMs;
    s.lastValidDriveCommandMs = millis();
    s.faultFlags &= ~FAULT_COMM_TIMEOUT_MOTOR;
    switch (cmd.mode) {
      case 0: s.state = MotorBoardState::SAFE_IDLE; break;
      case 1: s.state = MotorBoardState::MANUAL_ACTIVE; break;
      case 2: s.state = MotorBoardState::AUTO_ACTIVE; break;
      default: break;
    }
    accepted = true;
  });

  if (accepted) {
    applyDriveTargets(cmd.targetDriveLeftMmps, cmd.targetDriveRightMmps, cmd.targetSteeringMdeg);
  }
}

void handleStopCommand(uint16_t sequence) {
  applySafeOutputs();
  motorSharedStateUpdate([](MotorSharedState& s) {
    s.targetDriveLeftMmps = 0;
    s.targetDriveRightMmps = 0;
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
