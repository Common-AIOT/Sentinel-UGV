#include "protocol.h"

#include <cstring>

// ---- CRC-16/CCITT-FALSE ----

static uint16_t crc16UpdateByte(uint16_t crc, uint8_t byte) {
  crc ^= (uint16_t)byte << 8;
  for (int i = 0; i < 8; i++) {
    if (crc & 0x8000) {
      crc = (uint16_t)((crc << 1) ^ 0x1021);
    } else {
      crc = (uint16_t)(crc << 1);
    }
  }
  return crc;
}

uint16_t crc16CcittFalse(const uint8_t* data, size_t len) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < len; i++) {
    crc = crc16UpdateByte(crc, data[i]);
  }
  return crc;
}

// ---- COBS ----
// 표준 COBS 알고리즘(0x00 구분자용). 인코딩 결과에는 0x00이 나타나지 않는다.

size_t cobsEncode(const uint8_t* src, size_t len, uint8_t* dst, size_t dstCap) {
  if (dstCap < 1) return 0;

  size_t readIndex = 0;
  size_t writeIndex = 1;
  size_t codeIndex = 0;
  uint8_t code = 1;

  while (readIndex < len) {
    if (writeIndex >= dstCap) return 0;
    if (src[readIndex] == 0) {
      dst[codeIndex] = code;
      code = 1;
      codeIndex = writeIndex++;
      readIndex++;
    } else {
      dst[writeIndex++] = src[readIndex++];
      code++;
      if (code == 0xFF) {
        if (writeIndex >= dstCap) return 0;
        dst[codeIndex] = code;
        code = 1;
        codeIndex = writeIndex++;
      }
    }
  }
  dst[codeIndex] = code;
  return writeIndex;
}

size_t cobsDecode(const uint8_t* src, size_t len, uint8_t* dst, size_t dstCap) {
  size_t readIndex = 0;
  size_t writeIndex = 0;

  while (readIndex < len) {
    uint8_t code = src[readIndex];
    if (code == 0 || (readIndex + code > len && code != 1)) {
      return 0;  // 형식 오류
    }
    readIndex++;

    for (uint8_t i = 1; i < code; i++) {
      if (writeIndex >= dstCap) return 0;
      dst[writeIndex++] = src[readIndex++];
    }
    if (code != 0xFF && readIndex != len) {
      if (writeIndex >= dstCap) return 0;
      dst[writeIndex++] = 0;
    }
  }
  return writeIndex;
}

// ---- 프레임 build/parse ----

static bool isKnownMessageType(uint8_t type) {
  switch (type) {
    case MSG_HELLO:
    case MSG_HELLO_ACK:
    case MSG_DRIVE_COMMAND:
    case MSG_STOP_COMMAND:
    case MSG_ESTOP_COMMAND:
    case MSG_SET_MODE:
    case MSG_DRIVE_STATE:
    case MSG_DIAGNOSTIC:
    case MSG_COMMAND_ACK:
    case MSG_ENVIRONMENT_STATE:
    case MSG_PROXIMITY_STATE:
    case MSG_ENCODER_STATE:
    case MSG_IMU_STATE:
    case MSG_CONFIG:
      return true;
    default:
      return false;
  }
}

size_t buildFrame(uint8_t messageType, uint16_t sequence, uint32_t senderUptimeMs,
                   const uint8_t* payload, uint16_t payloadLen,
                   uint8_t* outBuf, size_t outCap) {
  if (payloadLen > MAX_PAYLOAD_BYTES) return 0;
  if (outCap < 1) return 0;

  uint8_t raw[FRAME_HEADER_BYTES + MAX_PAYLOAD_BYTES + FRAME_CRC_BYTES];
  size_t offset = 0;
  writeU8(raw, offset, PROTOCOL_VERSION);
  writeU8(raw, offset, messageType);
  writeU16LE(raw, offset, sequence);
  writeU16LE(raw, offset, payloadLen);
  writeU32LE(raw, offset, senderUptimeMs);
  if (payloadLen > 0) {
    std::memcpy(raw + offset, payload, payloadLen);
    offset += payloadLen;
  }
  uint16_t crc = crc16CcittFalse(raw, offset);
  writeU16LE(raw, offset, crc);

  // outCap 중 1바이트는 뒤에 붙일 0x00 구분자용으로 남겨둔다.
  size_t encodedLen = cobsEncode(raw, offset, outBuf, outCap - 1);
  if (encodedLen == 0) return 0;

  outBuf[encodedLen] = 0x00;
  return encodedLen + 1;
}

ParseResult parseFrame(const uint8_t* cobsFrame, size_t len,
                        FrameHeader& outHeader, uint8_t* outPayload, size_t payloadCap) {
  uint8_t raw[FRAME_HEADER_BYTES + MAX_PAYLOAD_BYTES + FRAME_CRC_BYTES];
  size_t decodedLen = cobsDecode(cobsFrame, len, raw, sizeof(raw));
  if (decodedLen == 0) return ParseResult::COBS_ERROR;
  if (decodedLen < FRAME_HEADER_BYTES + FRAME_CRC_BYTES) return ParseResult::BAD_LENGTH;

  size_t offset = 0;
  uint8_t version = readU8(raw, offset);
  uint8_t messageType = readU8(raw, offset);
  uint16_t sequence = readU16LE(raw, offset);
  uint16_t payloadLength = readU16LE(raw, offset);
  uint32_t senderUptimeMs = readU32LE(raw, offset);

  size_t expectedTotal = FRAME_HEADER_BYTES + (size_t)payloadLength + FRAME_CRC_BYTES;
  if (payloadLength > MAX_PAYLOAD_BYTES || expectedTotal != decodedLen) {
    return ParseResult::BAD_LENGTH;
  }
  if (version != PROTOCOL_VERSION) return ParseResult::BAD_VERSION;
  if (!isKnownMessageType(messageType)) return ParseResult::UNKNOWN_TYPE;

  uint16_t receivedCrc = (uint16_t)raw[decodedLen - 2] | ((uint16_t)raw[decodedLen - 1] << 8);
  uint16_t computedCrc = crc16CcittFalse(raw, decodedLen - FRAME_CRC_BYTES);
  if (receivedCrc != computedCrc) return ParseResult::BAD_CRC;

  if (payloadLength > payloadCap) return ParseResult::BAD_LENGTH;
  if (payloadLength > 0) {
    std::memcpy(outPayload, raw + offset, payloadLength);
  }

  outHeader.protocolVersion = version;
  outHeader.messageType = messageType;
  outHeader.sequence = sequence;
  outHeader.payloadLength = payloadLength;
  outHeader.senderUptimeMs = senderUptimeMs;
  return ParseResult::OK;
}

bool isSequenceNewer(uint16_t candidate, uint16_t last) {
  uint16_t delta = (uint16_t)(candidate - last);
  return delta != 0 && delta < 0x8000;
}

// ---- 리틀엔디안 바이트 helpers ----

void writeU8(uint8_t* buf, size_t& offset, uint8_t v) {
  buf[offset++] = v;
}

void writeU16LE(uint8_t* buf, size_t& offset, uint16_t v) {
  buf[offset++] = (uint8_t)(v & 0xFF);
  buf[offset++] = (uint8_t)((v >> 8) & 0xFF);
}

void writeI16LE(uint8_t* buf, size_t& offset, int16_t v) {
  writeU16LE(buf, offset, (uint16_t)v);
}

void writeU32LE(uint8_t* buf, size_t& offset, uint32_t v) {
  buf[offset++] = (uint8_t)(v & 0xFF);
  buf[offset++] = (uint8_t)((v >> 8) & 0xFF);
  buf[offset++] = (uint8_t)((v >> 16) & 0xFF);
  buf[offset++] = (uint8_t)((v >> 24) & 0xFF);
}

void writeI32LE(uint8_t* buf, size_t& offset, int32_t v) {
  writeU32LE(buf, offset, (uint32_t)v);
}

void writeU64LE(uint8_t* buf, size_t& offset, uint64_t v) {
  for (int i = 0; i < 8; i++) {
    buf[offset++] = (uint8_t)((v >> (8 * i)) & 0xFF);
  }
}

void writeF32LE(uint8_t* buf, size_t& offset, float v) {
  uint32_t bits = 0;
  std::memcpy(&bits, &v, sizeof(bits));
  writeU32LE(buf, offset, bits);
}

uint8_t readU8(const uint8_t* buf, size_t& offset) {
  return buf[offset++];
}

uint16_t readU16LE(const uint8_t* buf, size_t& offset) {
  uint16_t v = (uint16_t)buf[offset] | ((uint16_t)buf[offset + 1] << 8);
  offset += 2;
  return v;
}

int16_t readI16LE(const uint8_t* buf, size_t& offset) {
  return (int16_t)readU16LE(buf, offset);
}

uint32_t readU32LE(const uint8_t* buf, size_t& offset) {
  uint32_t v = (uint32_t)buf[offset] | ((uint32_t)buf[offset + 1] << 8) |
               ((uint32_t)buf[offset + 2] << 16) | ((uint32_t)buf[offset + 3] << 24);
  offset += 4;
  return v;
}

int32_t readI32LE(const uint8_t* buf, size_t& offset) {
  return (int32_t)readU32LE(buf, offset);
}

uint64_t readU64LE(const uint8_t* buf, size_t& offset) {
  uint64_t v = 0;
  for (int i = 0; i < 8; i++) {
    v |= (uint64_t)buf[offset + i] << (8 * i);
  }
  offset += 8;
  return v;
}

float readF32LE(const uint8_t* buf, size_t& offset) {
  uint32_t bits = readU32LE(buf, offset);
  float v = 0.0f;
  std::memcpy(&v, &bits, sizeof(v));
  return v;
}

// ==== 페이로드 pack/unpack (§34-5 확정분) ====

size_t packDriveCommand(const DriveCommand& in, uint8_t* out) {
  size_t offset = 0;
  writeU8(out, offset, in.mode);
  writeU8(out, offset, in.flags);
  writeI16LE(out, offset, in.targetDriveLeftMmps);
  writeI16LE(out, offset, in.targetDriveRightMmps);
  writeI16LE(out, offset, in.targetSteeringMdeg);
  writeU16LE(out, offset, in.maxAccelMmps2);
  writeU16LE(out, offset, in.maxSteeringRateMdps);
  writeU16LE(out, offset, in.commandTimeoutMs);
  return offset;
}

bool unpackDriveCommand(const uint8_t* payload, uint16_t len, DriveCommand& out) {
  if (len != DRIVE_COMMAND_BYTES) return false;
  size_t offset = 0;
  out.mode = readU8(payload, offset);
  out.flags = readU8(payload, offset);
  out.targetDriveLeftMmps = readI16LE(payload, offset);
  out.targetDriveRightMmps = readI16LE(payload, offset);
  out.targetSteeringMdeg = readI16LE(payload, offset);
  out.maxAccelMmps2 = readU16LE(payload, offset);
  out.maxSteeringRateMdps = readU16LE(payload, offset);
  out.commandTimeoutMs = readU16LE(payload, offset);
  return true;
}

size_t packSetMode(const SetMode& in, uint8_t* out) {
  size_t offset = 0;
  writeU8(out, offset, in.requestedMode);
  writeU8(out, offset, in.flags);
  return offset;
}

bool unpackSetMode(const uint8_t* payload, uint16_t len, SetMode& out) {
  if (len != SET_MODE_BYTES) return false;
  size_t offset = 0;
  out.requestedMode = readU8(payload, offset);
  out.flags = readU8(payload, offset);
  return true;
}

size_t packDriveState(const DriveState& in, uint8_t* out) {
  size_t offset = 0;
  writeU16LE(out, offset, in.appliedSequence);
  writeU8(out, offset, in.state);
  writeU16LE(out, offset, in.faultFlags);
  writeI16LE(out, offset, in.drivePwmLeftPermille);
  writeI16LE(out, offset, in.drivePwmRightPermille);
  writeI16LE(out, offset, in.targetSteeringMdeg);
  writeI16LE(out, offset, in.steeringActuatorCmd);
  writeU8(out, offset, in.estopActive);
  writeU8(out, offset, in.driverEnabled);
  return offset;
}

bool unpackDriveState(const uint8_t* payload, uint16_t len, DriveState& out) {
  if (len != DRIVE_STATE_BYTES) return false;
  size_t offset = 0;
  out.appliedSequence = readU16LE(payload, offset);
  out.state = readU8(payload, offset);
  out.faultFlags = readU16LE(payload, offset);
  out.drivePwmLeftPermille = readI16LE(payload, offset);
  out.drivePwmRightPermille = readI16LE(payload, offset);
  out.targetSteeringMdeg = readI16LE(payload, offset);
  out.steeringActuatorCmd = readI16LE(payload, offset);
  out.estopActive = readU8(payload, offset);
  out.driverEnabled = readU8(payload, offset);
  return true;
}

size_t packEncoderState(const EncoderState& in, uint8_t* out) {
  size_t offset = 0;
  writeI32LE(out, offset, in.driveEncoderTicksLeft);
  writeI32LE(out, offset, in.driveEncoderTicksRight);
  writeI16LE(out, offset, in.driveSpeedLeftMmps);
  writeI16LE(out, offset, in.driveSpeedRightMmps);
  writeI16LE(out, offset, in.measuredSteeringMdeg);
  writeU16LE(out, offset, in.sampleAgeMs);
  return offset;
}

bool unpackEncoderState(const uint8_t* payload, uint16_t len, EncoderState& out) {
  if (len != ENCODER_STATE_BYTES) return false;
  size_t offset = 0;
  out.driveEncoderTicksLeft = readI32LE(payload, offset);
  out.driveEncoderTicksRight = readI32LE(payload, offset);
  out.driveSpeedLeftMmps = readI16LE(payload, offset);
  out.driveSpeedRightMmps = readI16LE(payload, offset);
  out.measuredSteeringMdeg = readI16LE(payload, offset);
  out.sampleAgeMs = readU16LE(payload, offset);
  return true;
}

size_t packEnvironmentState(const EnvironmentState& in, uint8_t* out) {
  size_t offset = 0;
  writeI16LE(out, offset, in.temperatureDeciC);
  writeU16LE(out, offset, in.humidityDeciPct);
  writeU8(out, offset, in.statusFlags);
  writeU16LE(out, offset, in.sampleAgeMs);
  return offset;
}

bool unpackEnvironmentState(const uint8_t* payload, uint16_t len, EnvironmentState& out) {
  if (len != ENVIRONMENT_STATE_BYTES) return false;
  size_t offset = 0;
  out.temperatureDeciC = readI16LE(payload, offset);
  out.humidityDeciPct = readU16LE(payload, offset);
  out.statusFlags = readU8(payload, offset);
  out.sampleAgeMs = readU16LE(payload, offset);
  return true;
}

size_t packProximityState(const ProximityState& in, uint8_t* out) {
  size_t offset = 0;
  writeU16LE(out, offset, in.frontMinDistanceMm);
  writeU16LE(out, offset, in.rearMinDistanceMm);
  writeU8(out, offset, in.validSensorMask);
  writeU8(out, offset, in.protectiveStop);
  writeU16LE(out, offset, in.sampleAgeMs);
  return offset;
}

bool unpackProximityState(const uint8_t* payload, uint16_t len, ProximityState& out) {
  if (len != PROXIMITY_STATE_BYTES) return false;
  size_t offset = 0;
  out.frontMinDistanceMm = readU16LE(payload, offset);
  out.rearMinDistanceMm = readU16LE(payload, offset);
  out.validSensorMask = readU8(payload, offset);
  out.protectiveStop = readU8(payload, offset);
  out.sampleAgeMs = readU16LE(payload, offset);
  return true;
}

size_t packImuState(const ImuState& in, uint8_t* out) {
  size_t offset = 0;
  writeU64LE(out, offset, in.sampleTimeUs);
  writeF32LE(out, offset, in.gyroXRadps);
  writeF32LE(out, offset, in.gyroYRadps);
  writeF32LE(out, offset, in.gyroZRadps);
  writeF32LE(out, offset, in.accelXMps2);
  writeF32LE(out, offset, in.accelYMps2);
  writeF32LE(out, offset, in.accelZMps2);
  writeI16LE(out, offset, in.temperatureCentiC);
  writeU16LE(out, offset, in.statusFlags);
  return offset;
}

bool unpackImuState(const uint8_t* payload, uint16_t len, ImuState& out) {
  if (len != IMU_STATE_BYTES) return false;
  size_t offset = 0;
  out.sampleTimeUs = readU64LE(payload, offset);
  out.gyroXRadps = readF32LE(payload, offset);
  out.gyroYRadps = readF32LE(payload, offset);
  out.gyroZRadps = readF32LE(payload, offset);
  out.accelXMps2 = readF32LE(payload, offset);
  out.accelYMps2 = readF32LE(payload, offset);
  out.accelZMps2 = readF32LE(payload, offset);
  out.temperatureCentiC = readI16LE(payload, offset);
  out.statusFlags = readU16LE(payload, offset);
  return true;
}

// ==== 페이로드 pack/unpack (신규 확정분) ====

size_t packHelloAck(const HelloAck& in, uint8_t* out) {
  size_t offset = 0;
  writeU8(out, offset, in.boardRole);
  writeU8(out, offset, in.firmwareMajor);
  writeU8(out, offset, in.firmwareMinor);
  writeU8(out, offset, in.firmwarePatch);
  writeU8(out, offset, in.protocolVersion);
  writeU8(out, offset, in.boardState);
  writeU16LE(out, offset, in.faultFlags);
  writeU8(out, offset, in.reserved);
  return offset;
}

bool unpackHelloAck(const uint8_t* payload, uint16_t len, HelloAck& out) {
  if (len != HELLO_ACK_BYTES) return false;
  size_t offset = 0;
  out.boardRole = readU8(payload, offset);
  out.firmwareMajor = readU8(payload, offset);
  out.firmwareMinor = readU8(payload, offset);
  out.firmwarePatch = readU8(payload, offset);
  out.protocolVersion = readU8(payload, offset);
  out.boardState = readU8(payload, offset);
  out.faultFlags = readU16LE(payload, offset);
  out.reserved = readU8(payload, offset);
  return true;
}

size_t packDiagnostic(const Diagnostic& in, uint8_t* out) {
  size_t offset = 0;
  writeU8(out, offset, in.boardRole);
  writeU8(out, offset, in.boardState);
  writeU16LE(out, offset, in.faultFlags);
  writeU32LE(out, offset, in.crcErrorCount);
  writeU32LE(out, offset, in.droppedFrameCount);
  writeU32LE(out, offset, in.staleSequenceCount);
  writeU32LE(out, offset, in.freeHeapBytes);
  return offset;
}

bool unpackDiagnostic(const uint8_t* payload, uint16_t len, Diagnostic& out) {
  if (len != DIAGNOSTIC_BYTES) return false;
  size_t offset = 0;
  out.boardRole = readU8(payload, offset);
  out.boardState = readU8(payload, offset);
  out.faultFlags = readU16LE(payload, offset);
  out.crcErrorCount = readU32LE(payload, offset);
  out.droppedFrameCount = readU32LE(payload, offset);
  out.staleSequenceCount = readU32LE(payload, offset);
  out.freeHeapBytes = readU32LE(payload, offset);
  return true;
}

size_t packCommandAck(const CommandAck& in, uint8_t* out) {
  size_t offset = 0;
  writeU8(out, offset, in.ackedMessageType);
  writeU16LE(out, offset, in.ackedSequence);
  writeU8(out, offset, in.result);
  writeU8(out, offset, in.boardState);
  return offset;
}

bool unpackCommandAck(const uint8_t* payload, uint16_t len, CommandAck& out) {
  if (len != COMMAND_ACK_BYTES) return false;
  size_t offset = 0;
  out.ackedMessageType = readU8(payload, offset);
  out.ackedSequence = readU16LE(payload, offset);
  out.result = readU8(payload, offset);
  out.boardState = readU8(payload, offset);
  return true;
}

size_t packConfigMessage(const ConfigMessage& in, uint8_t* out) {
  size_t offset = 0;
  writeU8(out, offset, in.operation);
  writeU16LE(out, offset, in.keyId);
  writeI32LE(out, offset, in.value);
  return offset;
}

bool unpackConfigMessage(const uint8_t* payload, uint16_t len, ConfigMessage& out) {
  if (len != CONFIG_MESSAGE_BYTES) return false;
  size_t offset = 0;
  out.operation = readU8(payload, offset);
  out.keyId = readU16LE(payload, offset);
  out.value = readI32LE(payload, offset);
  return true;
}
