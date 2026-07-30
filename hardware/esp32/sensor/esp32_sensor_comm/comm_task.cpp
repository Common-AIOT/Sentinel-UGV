#include "comm_task.h"

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "../../jetson-comm/protocol.h"
#include "../../jetson-comm/fault_codes.h"
#include "board_state.h"

namespace {

constexpr uint8_t FW_MAJOR = 0;
constexpr uint8_t FW_MINOR = 1;
constexpr uint8_t FW_PATCH = 0;

constexpr uint32_t ENCODER_STATE_INTERVAL_MS = 20;      // 50Hz (§34-5)
constexpr uint32_t ENVIRONMENT_STATE_INTERVAL_MS = 1000; // ~1Hz (§34-5)
constexpr uint32_t PROXIMITY_STATE_INTERVAL_MS = 66;     // ~15Hz (§34-5, 10~20Hz)
constexpr uint32_t DIAGNOSTIC_INTERVAL_MS = 200;         // 5Hz (§34-5)
constexpr uint32_t COMM_WATCHDOG_TIMEOUT_MS = 300;       // §34-7
constexpr size_t RX_ACCUM_CAP = 256;

uint8_t g_rxAccum[RX_ACCUM_CAP];
size_t g_rxAccumLen = 0;
uint16_t g_outboundSequence = 0;

uint16_t nextOutboundSequence() {
  return g_outboundSequence++;
}

// Arduino.h가 min()을 매크로로 정의하는 경우가 있어 템플릿 호출과 충돌할 수 있으므로
// 직접 클램프한다.
uint16_t sampleAgeMsSince(uint32_t lastUpdateMs) {
  uint32_t age = millis() - lastUpdateMs;
  return age > 0xFFFF ? (uint16_t)0xFFFF : (uint16_t)age;
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
  SensorSharedState snapshot = sensorSharedStateSnapshot();
  HelloAck ack{};
  ack.boardRole = BOARD_ROLE_SENSOR;
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

void sendEncoderState() {
  SensorSharedState snapshot = sensorSharedStateSnapshot();
  EncoderState state{};
  state.driveEncoderTicksLeft = snapshot.driveEncoderTicksLeft;
  state.driveEncoderTicksRight = snapshot.driveEncoderTicksRight;
  state.driveSpeedLeftMmps = snapshot.driveSpeedLeftMmps;
  state.driveSpeedRightMmps = snapshot.driveSpeedRightMmps;
  state.measuredSteeringMdeg = snapshot.measuredSteeringMdeg;
  state.sampleAgeMs = sampleAgeMsSince(snapshot.lastSensorUpdateMs);

  uint8_t payload[ENCODER_STATE_BYTES];
  size_t len = packEncoderState(state, payload);
  sendFrame(MSG_ENCODER_STATE, payload, (uint16_t)len);
}

void sendEnvironmentState() {
  SensorSharedState snapshot = sensorSharedStateSnapshot();
  EnvironmentState state{};
  state.temperatureDeciC = snapshot.temperatureDeciC;
  state.humidityDeciPct = snapshot.humidityDeciPct;
  state.statusFlags = snapshot.environmentStatusFlags;
  state.sampleAgeMs = sampleAgeMsSince(snapshot.lastSensorUpdateMs);

  uint8_t payload[ENVIRONMENT_STATE_BYTES];
  size_t len = packEnvironmentState(state, payload);
  sendFrame(MSG_ENVIRONMENT_STATE, payload, (uint16_t)len);
}

void sendProximityState() {
  SensorSharedState snapshot = sensorSharedStateSnapshot();
  ProximityState state{};
  state.frontMinDistanceMm = snapshot.frontMinDistanceMm;
  state.validSensorMask = snapshot.validSensorMask;
  state.protectiveStop = snapshot.protectiveStop;
  state.sampleAgeMs = sampleAgeMsSince(snapshot.lastSensorUpdateMs);

  uint8_t payload[PROXIMITY_STATE_BYTES];
  size_t len = packProximityState(state, payload);
  sendFrame(MSG_PROXIMITY_STATE, payload, (uint16_t)len);
}

void sendDiagnostic() {
  SensorSharedState snapshot = sensorSharedStateSnapshot();
  Diagnostic diag{};
  diag.boardRole = BOARD_ROLE_SENSOR;
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

// HELLO/CONFIG 등 Jetson으로부터 온 유효한 프레임을 받을 때마다 호출한다. 300ms 통신
// 워치독(§34-7)이 이 시각을 기준으로 COMM_LOST 여부를 판단한다.
void markJetsonContact() {
  sensorSharedStateUpdate([](SensorSharedState& s) {
    s.hasReceivedFromJetson = true;
    s.lastValidJetsonRxMs = millis();
    if (s.state == SensorBoardState::BOOT || s.state == SensorBoardState::COMM_LOST) {
      s.state = SensorBoardState::STREAMING;
    }
    s.faultFlags &= ~FAULT_COMM_TIMEOUT_SENSOR;
  });
}

void handleHello() {
  markJetsonContact();
  sendHelloAck();
}

void handleConfig(const uint8_t* payload, uint16_t len) {
  markJetsonContact();

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
    sensorSharedStateUpdate([](SensorSharedState& s) { s.crcErrorCount++; });
    return;
  }
  if (result != ParseResult::OK) {
    sensorSharedStateUpdate([](SensorSharedState& s) { s.droppedFrameCount++; });
    return;
  }

  switch (header.messageType) {
    case MSG_HELLO: handleHello(); break;
    case MSG_CONFIG: handleConfig(payload, header.payloadLength); break;
    default: break;  // 센서 보드가 받을 일 없는 타입(DRIVE_COMMAND 등)은 무시
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
      sensorSharedStateUpdate([](SensorSharedState& s) { s.droppedFrameCount++; });
      g_rxAccumLen = 0;
      continue;
    }
    g_rxAccum[g_rxAccumLen++] = byte;
  }
}

void checkCommWatchdog() {
  uint32_t now = millis();
  sensorSharedStateUpdate([now](SensorSharedState& s) {
    if (!s.hasReceivedFromJetson) return;  // 아직 첫 접촉 전이면 BOOT 유지
    bool timedOut = (now - s.lastValidJetsonRxMs) > COMM_WATCHDOG_TIMEOUT_MS;
    if (timedOut && s.state != SensorBoardState::COMM_LOST) {
      s.state = SensorBoardState::COMM_LOST;
      s.faultFlags |= FAULT_COMM_TIMEOUT_SENSOR;
      // 센서 보드는 로컬 액추에이터가 없어 정지 동작은 없다(§34-7) - STALE 플래그만 보고한다.
    }
  });
}

}  // namespace

void commTaskFn(void* pvParameters) {
  (void)pvParameters;
  Serial.begin(921600);

  uint32_t lastEncoderStateMs = 0;
  uint32_t lastEnvironmentStateMs = 0;
  uint32_t lastProximityStateMs = 0;
  uint32_t lastDiagnosticMs = 0;

  for (;;) {
    pollSerial();
    checkCommWatchdog();

    uint32_t now = millis();
    if (now - lastEncoderStateMs >= ENCODER_STATE_INTERVAL_MS) {
      lastEncoderStateMs = now;
      sendEncoderState();
    }
    if (now - lastEnvironmentStateMs >= ENVIRONMENT_STATE_INTERVAL_MS) {
      lastEnvironmentStateMs = now;
      sendEnvironmentState();
    }
    if (now - lastProximityStateMs >= PROXIMITY_STATE_INTERVAL_MS) {
      lastProximityStateMs = now;
      sendProximityState();
    }
    if (now - lastDiagnosticMs >= DIAGNOSTIC_INTERVAL_MS) {
      lastDiagnosticMs = now;
      sendDiagnostic();
    }

    vTaskDelay(pdMS_TO_TICKS(1));
  }
}
