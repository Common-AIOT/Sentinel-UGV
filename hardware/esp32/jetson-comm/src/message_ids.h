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

  // GET/SET은 하나의 메시지 코드를 쓰고 payload.operation 필드로 구분한다(§34-5).
  MSG_CONFIG = 0x30,
};

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
