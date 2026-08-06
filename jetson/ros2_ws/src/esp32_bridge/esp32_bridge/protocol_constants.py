"""Jetson<->ESP32 직렬 프로토콜 상수와 struct 포맷 문자열.

`hardware/esp32/jetson-comm/src/message_ids.h`, `fault_codes.h`, `protocol.h`와 값이
반드시 동일해야 한다(수동 동기화 - 한쪽을 바꾸면 반드시 다른 쪽도 함께 고칠 것).
struct 포맷 문자열은 `<`(little-endian, 표준 크기, 패딩 없음)로 시작해 C++ 쪽의
필드별 memcpy 직렬화와 동일한 바이트 배치를 보장한다.
"""

from __future__ import annotations

PROTOCOL_VERSION = 1
MAX_PAYLOAD_BYTES = 128

# 프레임 헤더 10바이트(protocolVersion~senderUptimeMs) + payload + crc16 2바이트
FRAME_HEADER_BYTES = 10
FRAME_CRC_BYTES = 2

MSG_HELLO = 0x01
MSG_HELLO_ACK = 0x02

MSG_DRIVE_COMMAND = 0x10
MSG_STOP_COMMAND = 0x11
MSG_ESTOP_COMMAND = 0x12

MSG_DRIVE_STATE = 0x20
MSG_DIAGNOSTIC = 0x21
MSG_COMMAND_ACK = 0x22
MSG_ENVIRONMENT_STATE = 0x23
MSG_PROXIMITY_STATE = 0x24
MSG_ENCODER_STATE = 0x25
MSG_IMU_STATE = 0x26

# GET/SET은 코드 하나(0x30)를 공유하고 payload.operation으로 구분한다(§34-5).
MSG_CONFIG = 0x30

KNOWN_MESSAGE_TYPES = frozenset(
    {
        MSG_HELLO,
        MSG_HELLO_ACK,
        MSG_DRIVE_COMMAND,
        MSG_STOP_COMMAND,
        MSG_ESTOP_COMMAND,
        MSG_DRIVE_STATE,
        MSG_DIAGNOSTIC,
        MSG_COMMAND_ACK,
        MSG_ENVIRONMENT_STATE,
        MSG_PROXIMITY_STATE,
        MSG_ENCODER_STATE,
        MSG_IMU_STATE,
        MSG_CONFIG,
    }
)

BOARD_ROLE_MOTOR = 1
BOARD_ROLE_SENSOR = 2

ACK_RESULT_ACCEPTED = 0
ACK_RESULT_REJECTED_STATE = 1
ACK_RESULT_REJECTED_STALE_SEQUENCE = 2

CONFIG_OP_GET = 0
CONFIG_OP_SET = 1

# ---- IMU_STATE.status_flags (message_ids.h의 ImuStatusFlag) ----
IMU_STATUS_VALID = 1 << 0
IMU_STATUS_CALIBRATING = 1 << 1
IMU_STATUS_RANGE_ERROR = 1 << 2
IMU_STATUS_BUS_ERROR = 1 << 3

IMU_STATUS_NAMES: dict[int, str] = {
    IMU_STATUS_VALID: "VALID",
    IMU_STATUS_CALIBRATING: "CALIBRATING",
    IMU_STATUS_RANGE_ERROR: "RANGE_ERROR",
    IMU_STATUS_BUS_ERROR: "BUS_ERROR",
}

# temperature_centi_c의 "미지원/무효" sentinel(message_ids.h의 IMU_TEMPERATURE_INVALID).
IMU_TEMPERATURE_INVALID = -32768

# ---- fault_codes.h (docs/03-제어-캘리브레이션.md §34-9) ----
FAULT_COMM_TIMEOUT_MOTOR = 1 << 0
FAULT_CRC_ERROR_RATE_HIGH = 1 << 1
FAULT_DRIVE_ENCODER_FAULT = 1 << 2
FAULT_WHEEL_SPEED_MISMATCH = 1 << 3
FAULT_DRIVER_FAULT = 1 << 4
FAULT_OVERCURRENT = 1 << 5
FAULT_UNDERVOLTAGE = 1 << 6
FAULT_ESTOP_ACTIVE = 1 << 7
FAULT_CONFIG_INVALID = 1 << 8
FAULT_INTERNAL_WATCHDOG_RESET = 1 << 9
FAULT_PROXIMITY_SENSOR_FAULT = 1 << 10
FAULT_ENVIRONMENT_SENSOR_FAULT = 1 << 11
FAULT_COMM_TIMEOUT_SENSOR = 1 << 12
FAULT_IMU_SENSOR_FAULT = 1 << 13
# 2026-08-06 전륜 서보 조향 전환으로 의미를 갖게 된 두 비트(§34-9 bit 14/15).
FAULT_STEERING_COMMAND_INVALID = 1 << 14
FAULT_STEERING_RESPONSE_MISMATCH = 1 << 15

FAULT_NAMES: dict[int, str] = {
    FAULT_COMM_TIMEOUT_MOTOR: "COMM_TIMEOUT_MOTOR",
    FAULT_CRC_ERROR_RATE_HIGH: "CRC_ERROR_RATE_HIGH",
    FAULT_DRIVE_ENCODER_FAULT: "DRIVE_ENCODER_FAULT",
    FAULT_WHEEL_SPEED_MISMATCH: "WHEEL_SPEED_MISMATCH",
    FAULT_DRIVER_FAULT: "DRIVER_FAULT",
    FAULT_OVERCURRENT: "OVERCURRENT",
    FAULT_UNDERVOLTAGE: "UNDERVOLTAGE",
    FAULT_ESTOP_ACTIVE: "ESTOP_ACTIVE",
    FAULT_CONFIG_INVALID: "CONFIG_INVALID",
    FAULT_INTERNAL_WATCHDOG_RESET: "INTERNAL_WATCHDOG_RESET",
    FAULT_PROXIMITY_SENSOR_FAULT: "PROXIMITY_SENSOR_FAULT",
    FAULT_ENVIRONMENT_SENSOR_FAULT: "ENVIRONMENT_SENSOR_FAULT",
    FAULT_COMM_TIMEOUT_SENSOR: "COMM_TIMEOUT_SENSOR",
    # bit 13이 빠져 있던 동안은 IMU만 고장 났을 때 /diagnostics의 level만 ERROR로
    # 오르고 나열된 fault 키는 전부 0이라, "이름 없는 비트가 서 있다"를 추론해야
    # 했다(TESTING.md 10-4). 이름을 붙여 그 추론을 없앤다.
    FAULT_IMU_SENSOR_FAULT: "IMU_SENSOR_FAULT",
    # 조향 서보는 fault 출력도 각도 출력도 없어 이 두 비트가 조향의 유일한 진단
    # 창구다(§34-9). COMMAND_INVALID는 모터 ESP32가 클램프·거부할 때 즉시 세우고,
    # RESPONSE_MISMATCH는 IMU 기반 간접 판정이라 아직 미구현이다.
    FAULT_STEERING_COMMAND_INVALID: "STEERING_COMMAND_INVALID",
    FAULT_STEERING_RESPONSE_MISMATCH: "STEERING_RESPONSE_MISMATCH",
}


def fault_flag_names(fault_flags: int) -> list[str]:
    return [name for bit, name in FAULT_NAMES.items() if fault_flags & bit]


def imu_status_flag_names(status_flags: int) -> list[str]:
    """status_flags에 선 비트 이름 목록. 정의되지 않은 비트는 UNKNOWN(0xNN)으로 남긴다."""
    names = [name for bit, name in IMU_STATUS_NAMES.items() if status_flags & bit]
    unknown = status_flags & ~sum(IMU_STATUS_NAMES)
    if unknown:
        names.append(f"UNKNOWN(0x{unknown:04x})")
    return names


# ---- struct 포맷 (protocol.h의 packX/unpackX와 필드 순서가 반드시 일치해야 한다) ----
STRUCT_DRIVE_COMMAND = "<BBhhhHHH"  # 14 bytes
STRUCT_DRIVE_STATE = "<HBHhhhhBB"  # 15 bytes
STRUCT_ENCODER_STATE = "<iihhhH"  # 16 bytes
# f32 6개는 IEEE-754 binary32 리틀엔디안이다. ESP32(xtensa)·Jetson(aarch64) 모두
# IEEE-754 LE라 protocol.cpp의 writeF32LE가 쓴 비트 패턴을 그대로 읽는다.
STRUCT_IMU_STATE = "<QffffffhH"  # 36 bytes
STRUCT_ENVIRONMENT_STATE = "<hHBH"  # 7 bytes
STRUCT_PROXIMITY_STATE = "<HBBH"  # 6 bytes
STRUCT_HELLO_ACK = "<BBBBBBHB"  # 9 bytes
STRUCT_DIAGNOSTIC = "<BBHIIII"  # 20 bytes
STRUCT_COMMAND_ACK = "<BHBB"  # 5 bytes
STRUCT_CONFIG = "<BHi"  # 7 bytes
