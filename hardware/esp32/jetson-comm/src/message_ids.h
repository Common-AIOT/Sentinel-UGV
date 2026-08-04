// Jetson <-> ESP32 직렬 프로토콜 메시지 코드 (docs/03-제어-캘리브레이션.md §34-5)
#pragma once

#include <cstdint>
#include <cstddef>

constexpr uint8_t PROTOCOL_VERSION = 1;
constexpr size_t MAX_PAYLOAD_BYTES = 128;

// 프레임 헤더 10바이트(protocolVersion~senderUptimeMs) + payload + crc16 2바이트
constexpr size_t FRAME_HEADER_BYTES = 10;
constexpr size_t FRAME_CRC_BYTES = 2;
constexpr size_t MAX_FRAME_BYTES = FRAME_HEADER_BYTES + MAX_PAYLOAD_BYTES + FRAME_CRC_BYTES;

enum MessageId : uint8_t {
  MSG_HELLO = 0x01,
  MSG_HELLO_ACK = 0x02,

  MSG_DRIVE_COMMAND = 0x10,
  MSG_STOP_COMMAND = 0x11,
  MSG_ESTOP_COMMAND = 0x12,

  MSG_DRIVE_STATE = 0x20,
  MSG_DIAGNOSTIC = 0x21,
  MSG_COMMAND_ACK = 0x22,
  MSG_ENVIRONMENT_STATE = 0x23,
  MSG_PROXIMITY_STATE = 0x24,
  MSG_ENCODER_STATE = 0x25,
  MSG_IMU_STATE = 0x26,

  // GET/SET은 하나의 메시지 코드를 쓰고 payload.operation 필드로 구분한다(§34-5).
  MSG_CONFIG = 0x30,
};

// IMU_STATE.status_flags (§34-5: VALID, CALIBRATING, RANGE_ERROR, BUS_ERROR)
enum ImuStatusFlag : uint16_t {
  IMU_STATUS_VALID = 1u << 0,        // gyro/accel 값이 EKF에 넣을 수 있는 상태
  IMU_STATUS_CALIBRATING = 1u << 1,  // 자이로 바이어스 수집 중 - 값은 아직 신뢰 불가
  IMU_STATUS_RANGE_ERROR = 1u << 2,  // 축 하나 이상이 측정 범위에서 포화
  IMU_STATUS_BUS_ERROR = 1u << 3,    // I2C 판독 실패 또는 샘플이 갱신되지 않음
};

// IMU_STATE.temperature_centi_c 미지원/무효 sentinel (§34-5 "선택, 미지원 시 INVALID").
constexpr int16_t IMU_TEMPERATURE_INVALID = -32768;

// CONFIG.operation
enum ConfigOperation : uint8_t {
  CONFIG_OP_GET = 0,
  CONFIG_OP_SET = 1,
};

// HELLO_ACK.board_role
enum BoardRole : uint8_t {
  BOARD_ROLE_MOTOR = 1,
  BOARD_ROLE_SENSOR = 2,
};

// COMMAND_ACK.result
enum CommandAckResult : uint8_t {
  ACK_RESULT_ACCEPTED = 0,
  ACK_RESULT_REJECTED_STATE = 1,
  ACK_RESULT_REJECTED_STALE_SEQUENCE = 2,
};
