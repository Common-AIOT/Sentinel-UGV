"""모터 ESP32 전용 프레이밍 코덱 (S15P11A301-321).

`rclpy`를 import하지 않는 순수 로직이라 ROS 없이도 pytest로 검증할 수 있다.
`hardware/esp32/motor/esp32_motor_comm/motor_protocol.h`/`.cpp`와 바이트 단위로
동일하게 동작해야 한다 - 로직을 바꾸면 반드시 양쪽을 함께 고칠 것.

센서 ESP32는 `packet_codec.py`(COBS+CRC16, 옛 프레이밍)를 그대로 쓴다. 이 파일은
모터 링크만을 위한 것이며, `packet_codec.py`는 건드리지 않는다.

프레임 레이아웃(고정 27바이트, 델리미터 없음):

    [0]     sync0    = 0xA5
    [1]     sync1    = 0x5A
    [2]     type     u8
    [3]     sequence u8 (0..255 랩어라운드)
    [4:26]  payload  22바이트 고정, 실제 메시지 길이보다 짧으면 뒤를 0으로 채운다
    [26]    crc8     u8, CRC-8(poly=0x07, init=0x00)를 [2:26] 위에서 계산

길이·버전 필드가 없다 - 메시지 타입마다 payload 길이가 프로토콜상 고정이므로
양쪽이 타입→길이 대응을 정적으로 알고, protocol_version은 HELLO_ACK payload의
기존 필드로 옮겼다(motor_protocol_constants.MOTOR_PROTOCOL_VERSION).

아래 unpack_x() 들은 `packet_codec.py`와 달리 길이를 검증하지 않는다 - 항상
`parse_motor_frame()`이 돌려주는, PAYLOAD_BYTES(22)로 고정 패딩된 버퍼만
받는다는 것이 이 프로토콜의 불변식이라 검증할 "선언된 길이"가 애초에 없다.
필요한 접두 바이트만 읽고 나머지 패딩은 조용히 버린다.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .motor_protocol_constants import (
    FRAME_BYTES,
    PAYLOAD_BYTES,
    STRUCT_COMMAND_ACK,
    STRUCT_CONFIG,
    STRUCT_DRIVE_COMMAND,
    MOTOR_DRIVE_STATE_BYTES,
    STRUCT_DRIVE_STATE,
    STRUCT_HELLO_ACK,
    STRUCT_MOTOR_DIAGNOSTIC,
    STRUCT_SET_MODE,
    SYNC0,
    SYNC1,
)


class ProtocolError(Exception):
    """모든 프레임 파싱 오류의 베이스."""


class LengthError(ProtocolError):
    """프레임이 FRAME_BYTES 길이가 아니다."""


class SyncError(ProtocolError):
    """동기 워드(0xA5 0x5A) 불일치 - 슬라이딩 윈도우가 아직 정렬 전이다."""


class CrcError(ProtocolError):
    """CRC-8 불일치."""


# ---- CRC-8 (poly 0x07, init 0x00, 반전 없음) ----
# 손계산 검증 벡터: crc8(bytes([0x01])) == 0x07, crc8(bytes([0x02])) == 0x0E
# (motor_protocol.h 헤더 주석에 유도 과정이 있다).


def crc8(data: bytes) -> int:
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def is_motor_sequence_newer(candidate: int, last: int) -> bool:
    """RFC1982 스타일이지만 8비트 폭이다."""
    delta = (candidate - last) & 0xFF
    return delta != 0 and delta < 0x80


# ---- 프레임 build/parse ----


@dataclass
class ParsedMotorFrame:
    message_type: int
    sequence: int
    payload: bytes  # 항상 PAYLOAD_BYTES 길이(패딩 포함)


def build_motor_frame(message_type: int, sequence: int, payload: bytes = b"") -> bytes:
    if len(payload) > PAYLOAD_BYTES:
        raise ValueError(f"payload too large: {len(payload)} > {PAYLOAD_BYTES}")

    padded = payload + bytes(PAYLOAD_BYTES - len(payload))
    body = bytes([message_type, sequence & 0xFF]) + padded
    crc = crc8(body)
    return bytes([SYNC0, SYNC1]) + body + bytes([crc])


def parse_motor_frame(frame: bytes) -> ParsedMotorFrame:
    if len(frame) != FRAME_BYTES:
        raise LengthError(f"frame must be {FRAME_BYTES} bytes, got {len(frame)}")
    if frame[0] != SYNC0 or frame[1] != SYNC1:
        raise SyncError(f"bad sync word: {frame[0]:#04x} {frame[1]:#04x}")

    body = frame[2:-1]
    received_crc = frame[-1]
    computed_crc = crc8(body)
    if received_crc != computed_crc:
        raise CrcError(f"crc mismatch: received={received_crc:#04x} computed={computed_crc:#04x}")

    message_type = body[0]
    sequence = body[1]
    payload = bytes(body[2:])
    return ParsedMotorFrame(message_type, sequence, payload)


# ==== 페이로드 (§34-5, 의미는 옛 프로토콜과 동일 - 프레이밍만 바뀌었다) ====


@dataclass
class DriveCommand:
    mode: int
    flags: int
    target_drive_left_mmps: int
    target_drive_right_mmps: int
    target_steering_mdeg: int
    max_accel_mmps2: int
    max_steering_rate_mdps: int
    command_timeout_ms: int


def pack_drive_command(cmd: DriveCommand) -> bytes:
    return struct.pack(
        STRUCT_DRIVE_COMMAND,
        cmd.mode,
        cmd.flags,
        cmd.target_drive_left_mmps,
        cmd.target_drive_right_mmps,
        cmd.target_steering_mdeg,
        cmd.max_accel_mmps2,
        cmd.max_steering_rate_mdps,
        cmd.command_timeout_ms,
    )


def unpack_drive_command(payload: bytes) -> DriveCommand:
    size = struct.calcsize(STRUCT_DRIVE_COMMAND)
    return DriveCommand(*struct.unpack(STRUCT_DRIVE_COMMAND, payload[:size]))


@dataclass
class SetMode:
    requested_mode: int
    flags: int = 0


def pack_set_mode(cmd: SetMode) -> bytes:
    return struct.pack(STRUCT_SET_MODE, cmd.requested_mode, cmd.flags)


def unpack_set_mode(payload: bytes) -> SetMode:
    size = struct.calcsize(STRUCT_SET_MODE)
    return SetMode(*struct.unpack(STRUCT_SET_MODE, payload[:size]))


@dataclass
class DriveState:
    applied_sequence: int
    state: int
    fault_flags: int
    drive_pwm_left_permille: int
    drive_pwm_right_permille: int
    target_steering_mdeg: int
    steering_actuator_cmd: int
    estop_active: int
    driver_enabled: int
    # 16번째 바이트 (S15P11A301-345). **기본값이 있는 것은 의도다** — 구판
    # 펌웨어는 15바이트만 보내고, 그때는 「폴백 여부를 알 수 없음」이 0 이다.
    # 이 필드가 없다고 파싱을 실패시키면 플래시 순서 하나로 모터 링크가 죽는다.
    authority_flags: int = 0


def pack_drive_state(state: DriveState) -> bytes:
    return struct.pack(
        STRUCT_DRIVE_STATE,
        state.applied_sequence,
        state.state,
        state.fault_flags,
        state.drive_pwm_left_permille,
        state.drive_pwm_right_permille,
        state.target_steering_mdeg,
        state.steering_actuator_cmd,
        state.estop_active,
        state.driver_enabled,
    )


def unpack_drive_state(payload: bytes) -> DriveState:
    """15바이트(구판)와 16바이트(S15P11A301-345) 둘 다 받는다.

    **길이로 갈라 읽는 것이 요점이다.** 16바이트를 강제하면 구판 펌웨어가 올라간
    보드에서 모터 링크가 통째로 죽고, 15바이트만 읽으면 새 펌웨어의 폴백 사실을
    영영 못 본다. 플래시 순서가 어느 쪽이든 안전해야 한다 — 모터 보드와 젯슨은
    따로 배포되고, 2026-08-09 에 실제로 센서 보드만 플래시된 상태가 있었다.
    """
    size = struct.calcsize(STRUCT_DRIVE_STATE)
    state = DriveState(*struct.unpack(STRUCT_DRIVE_STATE, payload[:size]))
    if len(payload) >= MOTOR_DRIVE_STATE_BYTES:
        state.authority_flags = payload[size]
    return state


@dataclass
class HelloAck:
    board_role: int
    firmware_major: int
    firmware_minor: int
    firmware_patch: int
    protocol_version: int
    board_state: int
    fault_flags: int
    reserved: int = 0


def pack_hello_ack(ack: HelloAck) -> bytes:
    return struct.pack(
        STRUCT_HELLO_ACK,
        ack.board_role,
        ack.firmware_major,
        ack.firmware_minor,
        ack.firmware_patch,
        ack.protocol_version,
        ack.board_state,
        ack.fault_flags,
        ack.reserved,
    )


def unpack_hello_ack(payload: bytes) -> HelloAck:
    size = struct.calcsize(STRUCT_HELLO_ACK)
    return HelloAck(*struct.unpack(STRUCT_HELLO_ACK, payload[:size]))


@dataclass
class MotorDiagnostic:
    board_role: int
    board_state: int
    fault_flags: int
    crc_error_count: int
    dropped_frame_count: int
    stale_sequence_count: int
    free_heap_bytes: int
    # 링크 자체가 침묵한 시간(ms, 0xFFFF는 "Jetson과 한 번도 접촉 없음" sentinel).
    # motor_protocol.h의 MotorDiagnostic 주석 참고 - mode_arbiter가 올리는
    # FAULT_COMM_TIMEOUT_MOTOR(DRIVE_COMMAND 수신 빈도만 봄)와는 다른 축이다.
    link_silence_ms: int


def pack_motor_diagnostic(diag: MotorDiagnostic) -> bytes:
    return struct.pack(
        STRUCT_MOTOR_DIAGNOSTIC,
        diag.board_role,
        diag.board_state,
        diag.fault_flags,
        diag.crc_error_count,
        diag.dropped_frame_count,
        diag.stale_sequence_count,
        diag.free_heap_bytes,
        diag.link_silence_ms,
    )


def unpack_motor_diagnostic(payload: bytes) -> MotorDiagnostic:
    size = struct.calcsize(STRUCT_MOTOR_DIAGNOSTIC)
    return MotorDiagnostic(*struct.unpack(STRUCT_MOTOR_DIAGNOSTIC, payload[:size]))


@dataclass
class CommandAck:
    acked_message_type: int
    acked_sequence: int
    result: int
    board_state: int


def pack_command_ack(ack: CommandAck) -> bytes:
    return struct.pack(
        STRUCT_COMMAND_ACK,
        ack.acked_message_type,
        ack.acked_sequence,
        ack.result,
        ack.board_state,
    )


def unpack_command_ack(payload: bytes) -> CommandAck:
    size = struct.calcsize(STRUCT_COMMAND_ACK)
    return CommandAck(*struct.unpack(STRUCT_COMMAND_ACK, payload[:size]))


@dataclass
class ConfigMessage:
    operation: int
    key_id: int
    value: int


def pack_config_message(msg: ConfigMessage) -> bytes:
    return struct.pack(STRUCT_CONFIG, msg.operation, msg.key_id, msg.value)


def unpack_config_message(payload: bytes) -> ConfigMessage:
    size = struct.calcsize(STRUCT_CONFIG)
    return ConfigMessage(*struct.unpack(STRUCT_CONFIG, payload[:size]))
