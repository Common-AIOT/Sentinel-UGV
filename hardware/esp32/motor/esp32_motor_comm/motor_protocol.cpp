#include "motor_protocol.h"

#include <cstring>

uint8_t motorCrc8(const uint8_t* data, size_t len) {
  uint8_t crc = 0x00;
  for (size_t i = 0; i < len; ++i) {
    crc ^= data[i];
    for (int bit = 0; bit < 8; ++bit) {
      if (crc & 0x80) {
        crc = (uint8_t)((crc << 1) ^ 0x07);
      } else {
        crc = (uint8_t)(crc << 1);
      }
    }
  }
  return crc;
}

bool isMotorSequenceNewer(uint8_t candidate, uint8_t last) {
  int8_t delta = (int8_t)(uint8_t)(candidate - last);
  return delta > 0;
}

size_t buildMotorFrame(uint8_t messageType, uint8_t sequence,
                        const uint8_t* payload, size_t payloadLen,
                        uint8_t* outBuf, size_t outCap) {
  if (payloadLen > MOTOR_PAYLOAD_BYTES || outCap < MOTOR_FRAME_BYTES) return 0;

  outBuf[0] = MOTOR_SYNC0;
  outBuf[1] = MOTOR_SYNC1;
  outBuf[2] = messageType;
  outBuf[3] = sequence;
  for (size_t i = 0; i < MOTOR_PAYLOAD_BYTES; ++i) {
    outBuf[4 + i] = (i < payloadLen) ? payload[i] : 0;
  }
  // CRC는 type+sequence+payload(패딩 포함) 위에서 계산한다 - sync 워드는 제외한다.
  // sync는 프레임 정렬 신호일 뿐 데이터가 아니고, 슬라이딩 윈도우가 우연히 sync와
  // 같은 두 바이트를 payload 안에서 찾아도 CRC가 그 오탐을 걸러낸다.
  outBuf[MOTOR_FRAME_BYTES - 1] = motorCrc8(outBuf + 2, 2 + MOTOR_PAYLOAD_BYTES);
  return MOTOR_FRAME_BYTES;
}

MotorParseResult parseMotorFrame(const uint8_t* frame, size_t len,
                                  uint8_t& outMessageType, uint8_t& outSequence,
                                  uint8_t* outPayload, size_t payloadCap) {
  if (len != MOTOR_FRAME_BYTES || payloadCap < MOTOR_PAYLOAD_BYTES) {
    return MotorParseResult::BAD_LENGTH;
  }
  if (frame[0] != MOTOR_SYNC0 || frame[1] != MOTOR_SYNC1) {
    return MotorParseResult::BAD_SYNC;
  }
  uint8_t computedCrc = motorCrc8(frame + 2, 2 + MOTOR_PAYLOAD_BYTES);
  if (computedCrc != frame[MOTOR_FRAME_BYTES - 1]) {
    return MotorParseResult::BAD_CRC;
  }
  outMessageType = frame[2];
  outSequence = frame[3];
  std::memcpy(outPayload, frame + 4, MOTOR_PAYLOAD_BYTES);
  return MotorParseResult::OK;
}

size_t packMotorDiagnostic(const MotorDiagnostic& in, uint8_t* out) {
  size_t offset = 0;
  writeU8(out, offset, in.boardRole);
  writeU8(out, offset, in.boardState);
  writeU16LE(out, offset, in.faultFlags);
  writeU32LE(out, offset, in.crcErrorCount);
  writeU32LE(out, offset, in.droppedFrameCount);
  writeU32LE(out, offset, in.staleSequenceCount);
  writeU32LE(out, offset, in.freeHeapBytes);
  writeU16LE(out, offset, in.linkSilenceMs);
  return offset;
}

bool unpackMotorDiagnostic(const uint8_t* payload, size_t len, MotorDiagnostic& out) {
  if (len != MOTOR_DIAGNOSTIC_BYTES) return false;
  size_t offset = 0;
  out.boardRole = readU8(payload, offset);
  out.boardState = readU8(payload, offset);
  out.faultFlags = readU16LE(payload, offset);
  out.crcErrorCount = readU32LE(payload, offset);
  out.droppedFrameCount = readU32LE(payload, offset);
  out.staleSequenceCount = readU32LE(payload, offset);
  out.freeHeapBytes = readU32LE(payload, offset);
  out.linkSilenceMs = readU16LE(payload, offset);
  return true;
}
