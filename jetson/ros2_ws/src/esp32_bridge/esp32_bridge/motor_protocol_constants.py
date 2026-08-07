"""모터 ESP32 전용 프로토콜 상수 (S15P11A301-321).

`hardware/esp32/motor/esp32_motor_comm/motor_protocol.h`와 값이 반드시 동일해야
한다(수동 동기화 - 한쪽을 바꾸면 반드시 다른 쪽도 함께 고칠 것).

센서 ESP32는 여전히 `protocol_constants.py`/`packet_codec.py`(COBS+CRC16, 옛
프레이밍)를 그대로 쓴다 - 이 파일은 모터 링크만을 위한 것이다. 메시지 코드
(MSG_HELLO 등)는 옛 `protocol_constants.py`와 값이 같다 - 프레이밍만 바뀌었지
메시지 종류·의미는 그대로이기 때문이다(§34-5).
"""

from __future__ import annotations

# HELLO_ACK.protocol_version에 실리는 값. 옛 PROTOCOL_VERSION(=1, 센서가 쓰는
# COBS+CRC16 세대)과 겹치지 않는 값공간을 쓴다 - 프레이밍을 통째로 바꿨으니
# 번호도 새로 매겨, 구펌웨어/신펌웨어가 우연히 같은 값으로 핸드셰이크에
# 성공하는 일을 막는다.
MOTOR_PROTOCOL_VERSION = 2

SYNC0 = 0xA5
SYNC1 = 0x5A

# DIAGNOSTIC(22바이트, link_silence_ms 포함)이 모터가 쓰는 메시지 중 가장 크다.
PAYLOAD_BYTES = 22

# sync(2) + type(1) + sequence(1) + payload(22) + crc8(1)
FRAME_BYTES = 2 + 1 + 1 + PAYLOAD_BYTES + 1

MSG_HELLO = 0x01
MSG_HELLO_ACK = 0x02

MSG_DRIVE_COMMAND = 0x10
MSG_STOP_COMMAND = 0x11
MSG_ESTOP_COMMAND = 0x12
MSG_SET_MODE = 0x13

MSG_DRIVE_STATE = 0x20
MSG_DIAGNOSTIC = 0x21
MSG_COMMAND_ACK = 0x22

MSG_CONFIG = 0x30

KNOWN_MESSAGE_TYPES = frozenset(
    {
        MSG_HELLO,
        MSG_HELLO_ACK,
        MSG_DRIVE_COMMAND,
        MSG_STOP_COMMAND,
        MSG_ESTOP_COMMAND,
        MSG_SET_MODE,
        MSG_DRIVE_STATE,
        MSG_DIAGNOSTIC,
        MSG_COMMAND_ACK,
        MSG_CONFIG,
    }
)

BOARD_ROLE_MOTOR = 1

ACK_RESULT_ACCEPTED = 0
ACK_RESULT_REJECTED_STATE = 1
ACK_RESULT_REJECTED_STALE_SEQUENCE = 2

ACK_RESULT_NAMES: dict[int, str] = {
    ACK_RESULT_ACCEPTED: "ACCEPTED",
    ACK_RESULT_REJECTED_STATE: "REJECTED_STATE",
    ACK_RESULT_REJECTED_STALE_SEQUENCE: "REJECTED_STALE_SEQUENCE",
}

MESSAGE_TYPE_NAMES: dict[int, str] = {
    MSG_HELLO: "HELLO",
    MSG_HELLO_ACK: "HELLO_ACK",
    MSG_DRIVE_COMMAND: "DRIVE_COMMAND",
    MSG_STOP_COMMAND: "STOP_COMMAND",
    MSG_ESTOP_COMMAND: "ESTOP_COMMAND",
    MSG_SET_MODE: "SET_MODE",
    MSG_DRIVE_STATE: "DRIVE_STATE",
    MSG_DIAGNOSTIC: "DIAGNOSTIC",
    MSG_COMMAND_ACK: "COMMAND_ACK",
    MSG_CONFIG: "CONFIG",
}


def ack_result_name(value: int) -> str:
    return ACK_RESULT_NAMES.get(value, f"UNKNOWN({value})")


def message_type_name(value: int) -> str:
    return MESSAGE_TYPE_NAMES.get(value, f"UNKNOWN({value})")


BOARD_MODE_MANUAL = 1
BOARD_MODE_AUTO = 2

BOARD_MODE_VALUES: dict[str, int] = {
    "MANUAL": BOARD_MODE_MANUAL,
    "AUTO": BOARD_MODE_AUTO,
}

CONFIG_OP_GET = 0
CONFIG_OP_SET = 1

# ---- fault_codes.h (docs/03-제어-캘리브레이션.md §34-9) - 값은 protocol_constants.py와
# 동일하다(공유 헤더). 모터가 보는 것만 옮겨 둔다. ----
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
    FAULT_STEERING_COMMAND_INVALID: "STEERING_COMMAND_INVALID",
    FAULT_STEERING_RESPONSE_MISMATCH: "STEERING_RESPONSE_MISMATCH",
}


def fault_flag_names(fault_flags: int) -> list[str]:
    return [name for bit, name in FAULT_NAMES.items() if fault_flags & bit]


# ---- struct 포맷 (motor_protocol.cpp의 packX/unpackX와 필드 순서가 반드시 일치) ----
STRUCT_DRIVE_COMMAND = "<BBhhhHHH"  # 14 bytes
STRUCT_SET_MODE = "<BB"  # 2 bytes
STRUCT_DRIVE_STATE = "<HBHhhhhBB"  # 15 bytes
STRUCT_HELLO_ACK = "<BBBBBBHB"  # 9 bytes
# MotorDiagnostic - protocol_constants.STRUCT_DIAGNOSTIC(20B, 센서가 쓰는 것)와
# 다르다. link_silence_ms 하나가 더 붙어 22바이트다.
STRUCT_MOTOR_DIAGNOSTIC = "<BBHIIIIH"  # 22 bytes
STRUCT_COMMAND_ACK = "<BHBB"  # 5 bytes
STRUCT_CONFIG = "<BHi"  # 7 bytes
