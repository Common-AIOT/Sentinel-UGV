// Jetson <-> ESP32 직렬 프로토콜: 프레이밍(COBS+CRC16)과 페이로드 pack/unpack.
// Arduino.h에 의존하지 않아 호스트 g++로도 유닛테스트 가능하다 (test/test_protocol.cpp 참고).
#pragma once

#include <cstdint>
#include <cstddef>

#include "message_ids.h"

// ---- 프레임 헤더 ----
struct FrameHeader {
  uint8_t protocolVersion;
  uint8_t messageType;
  uint16_t sequence;
  uint16_t payloadLength;
  uint32_t senderUptimeMs;
};

// ---- CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, 반전 없음) ----
// 표준 검증 벡터: crc16CcittFalse("123456789", 9) == 0x29B1
uint16_t crc16CcittFalse(const uint8_t* data, size_t len);

// ---- COBS ----
// src에 담긴 원본 프레임(0x00 구분자 제외)을 인코딩한다. 반환값 0은 dstCap 부족을 뜻한다.
size_t cobsEncode(const uint8_t* src, size_t len, uint8_t* dst, size_t dstCap);
// src는 0x00 구분자를 제외한 COBS 인코딩 블록이어야 한다. 반환값 0은 실패(형식 오류/dstCap 부족)를 뜻한다.
size_t cobsDecode(const uint8_t* src, size_t len, uint8_t* dst, size_t dstCap);

// 헤더+payload+crc16을 만들어 COBS 인코딩 후 0x00 구분자를 붙여 outBuf에 쓴다.
// 반환값은 구분자를 포함한 총 바이트 수, 실패 시 0.
size_t buildFrame(uint8_t messageType, uint16_t sequence, uint32_t senderUptimeMs,
                   const uint8_t* payload, uint16_t payloadLen,
                   uint8_t* outBuf, size_t outCap);

enum class ParseResult : uint8_t {
  OK,
  BAD_CRC,
  BAD_VERSION,
  BAD_LENGTH,
  UNKNOWN_TYPE,
  COBS_ERROR,
};

// cobsFrame은 0x00 구분자를 제외한 상태여야 한다(스트림 분리는 호출자 책임).
ParseResult parseFrame(const uint8_t* cobsFrame, size_t len,
                        FrameHeader& outHeader, uint8_t* outPayload, size_t payloadCap);

// RFC1982 스타일 랩어라운드-세이프 시퀀스 비교: candidate가 last보다 새 값이면 true.
bool isSequenceNewer(uint16_t candidate, uint16_t last);

// ---- 리틀엔디안 바이트 read/write 헬퍼 (구조체 캐스팅 대신 사용) ----
void writeU8(uint8_t* buf, size_t& offset, uint8_t v);
void writeU16LE(uint8_t* buf, size_t& offset, uint16_t v);
void writeI16LE(uint8_t* buf, size_t& offset, int16_t v);
void writeU32LE(uint8_t* buf, size_t& offset, uint32_t v);
void writeI32LE(uint8_t* buf, size_t& offset, int32_t v);
void writeU64LE(uint8_t* buf, size_t& offset, uint64_t v);
// IEEE-754 binary32 리틀엔디안. ESP32(xtensa)와 Jetson(aarch64) 모두 IEEE-754 LE라
// 비트 패턴을 그대로 옮긴다(Python 쪽 struct '<f'와 동일).
void writeF32LE(uint8_t* buf, size_t& offset, float v);

uint8_t readU8(const uint8_t* buf, size_t& offset);
uint16_t readU16LE(const uint8_t* buf, size_t& offset);
int16_t readI16LE(const uint8_t* buf, size_t& offset);
uint32_t readU32LE(const uint8_t* buf, size_t& offset);
int32_t readI32LE(const uint8_t* buf, size_t& offset);
uint64_t readU64LE(const uint8_t* buf, size_t& offset);
float readF32LE(const uint8_t* buf, size_t& offset);

// ==== 페이로드 (docs/03-제어-캘리브레이션.md §34-5 확정분) ====

struct DriveCommand {
  uint8_t mode;
  uint8_t flags;
  int16_t targetDriveLeftMmps;
  int16_t targetDriveRightMmps;
  int16_t targetSteeringMdeg;
  uint16_t maxAccelMmps2;
  uint16_t maxSteeringRateMdps;
  uint16_t commandTimeoutMs;
};
constexpr size_t DRIVE_COMMAND_BYTES = 14;
size_t packDriveCommand(const DriveCommand& in, uint8_t* out);
bool unpackDriveCommand(const uint8_t* payload, uint16_t len, DriveCommand& out);

// 모드 전환 원샷 명령(0x13). flags는 예약이며 수신 측은 값을 보지 않는다.
struct SetMode {
  uint8_t requestedMode;  // SetModeRequest. 1=MANUAL, 2=AUTO. 그 밖은 REJECTED_STATE
  uint8_t flags;          // 예약, 0
};
constexpr size_t SET_MODE_BYTES = 2;
size_t packSetMode(const SetMode& in, uint8_t* out);
bool unpackSetMode(const uint8_t* payload, uint16_t len, SetMode& out);

struct DriveState {
  uint16_t appliedSequence;
  uint8_t state;
  uint16_t faultFlags;
  int16_t drivePwmLeftPermille;
  int16_t drivePwmRightPermille;
  int16_t targetSteeringMdeg;
  int16_t steeringActuatorCmd;
  uint8_t estopActive;
  uint8_t driverEnabled;
};
constexpr size_t DRIVE_STATE_BYTES = 15;
size_t packDriveState(const DriveState& in, uint8_t* out);
bool unpackDriveState(const uint8_t* payload, uint16_t len, DriveState& out);

struct EncoderState {
  int32_t driveEncoderTicksLeft;
  int32_t driveEncoderTicksRight;
  int16_t driveSpeedLeftMmps;
  int16_t driveSpeedRightMmps;
  int16_t measuredSteeringMdeg;
  uint16_t sampleAgeMs;
};
constexpr size_t ENCODER_STATE_BYTES = 16;
size_t packEncoderState(const EncoderState& in, uint8_t* out);
bool unpackEncoderState(const uint8_t* payload, uint16_t len, EncoderState& out);

struct EnvironmentState {
  int16_t temperatureDeciC;
  uint16_t humidityDeciPct;
  uint8_t statusFlags;
  uint16_t sampleAgeMs;
};
constexpr size_t ENVIRONMENT_STATE_BYTES = 7;
size_t packEnvironmentState(const EnvironmentState& in, uint8_t* out);
bool unpackEnvironmentState(const uint8_t* payload, uint16_t len, EnvironmentState& out);

struct ProximityState {
  uint16_t frontMinDistanceMm;
  uint8_t validSensorMask;
  uint8_t protectiveStop;
  uint16_t sampleAgeMs;
};
constexpr size_t PROXIMITY_STATE_BYTES = 6;
size_t packProximityState(const ProximityState& in, uint8_t* out);
bool unpackProximityState(const uint8_t* payload, uint16_t len, ProximityState& out);

// sampleTimeUs는 센서 ESP32의 monotonic 측정 시각(µs)이다. Jetson은 수신 시각으로
// 대신하지 않고 이 값을 ROS timestamp로 변환해 쓴다(§34-5). 자세 추정은 하지 않고
// 원시 gyro/accel만 보낸다. 축은 REP-103(x 전방, y 좌측, z 상방) 기준으로 정렬해 보낸다.
struct ImuState {
  uint64_t sampleTimeUs;
  float gyroXRadps;
  float gyroYRadps;
  float gyroZRadps;
  float accelXMps2;
  float accelYMps2;
  float accelZMps2;
  int16_t temperatureCentiC;  // 미지원/무효 시 IMU_TEMPERATURE_INVALID
  uint16_t statusFlags;       // ImuStatusFlag 비트합
};
constexpr size_t IMU_STATE_BYTES = 36;
size_t packImuState(const ImuState& in, uint8_t* out);
bool unpackImuState(const uint8_t* payload, uint16_t len, ImuState& out);

// ==== 페이로드 (문서에 없어 이번 작업에서 신규 확정, README 참고) ====

// HELLO(0x01)는 payload 없음(0바이트) — pack/unpack 불필요.

struct HelloAck {
  uint8_t boardRole;      // BoardRole
  uint8_t firmwareMajor;
  uint8_t firmwareMinor;
  uint8_t firmwarePatch;
  uint8_t protocolVersion;
  uint8_t boardState;
  uint16_t faultFlags;
  uint8_t reserved;
};
constexpr size_t HELLO_ACK_BYTES = 9;
size_t packHelloAck(const HelloAck& in, uint8_t* out);
bool unpackHelloAck(const uint8_t* payload, uint16_t len, HelloAck& out);

struct Diagnostic {
  uint8_t boardRole;
  uint8_t boardState;
  uint16_t faultFlags;
  uint32_t crcErrorCount;
  uint32_t droppedFrameCount;
  uint32_t staleSequenceCount;
  uint32_t freeHeapBytes;
};
constexpr size_t DIAGNOSTIC_BYTES = 20;
size_t packDiagnostic(const Diagnostic& in, uint8_t* out);
bool unpackDiagnostic(const uint8_t* payload, uint16_t len, Diagnostic& out);

struct CommandAck {
  uint8_t ackedMessageType;
  uint16_t ackedSequence;
  uint8_t result;   // CommandAckResult
  uint8_t boardState;
};
constexpr size_t COMMAND_ACK_BYTES = 5;
size_t packCommandAck(const CommandAck& in, uint8_t* out);
bool unpackCommandAck(const uint8_t* payload, uint16_t len, CommandAck& out);

struct ConfigMessage {
  uint8_t operation; // ConfigOperation
  uint16_t keyId;
  int32_t value;
};
constexpr size_t CONFIG_MESSAGE_BYTES = 7;
size_t packConfigMessage(const ConfigMessage& in, uint8_t* out);
bool unpackConfigMessage(const uint8_t* payload, uint16_t len, ConfigMessage& out);
