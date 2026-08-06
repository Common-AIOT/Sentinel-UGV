"""Jetson<->ESP32 직렬 프로토콜 코덱: CRC-16/CCITT-FALSE, COBS, 프레임 build/parse,
메시지별 pack/unpack.

`rclpy`를 import하지 않는 순수 로직이라 ROS 없이도 pytest로 검증할 수 있다
(`sentinel_bridge`의 `message_mapper`/`mqtt_client`와 같은 패턴).
`hardware/esp32/jetson-comm/src/protocol.h`/`protocol.cpp`와 바이트 단위로 동일하게
동작해야 한다 - 로직을 바꾸면 반드시 양쪽을 함께 고칠 것.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .protocol_constants import (
    FRAME_CRC_BYTES,
    FRAME_HEADER_BYTES,
    KNOWN_MESSAGE_TYPES,
    MAX_PAYLOAD_BYTES,
    PROTOCOL_VERSION,
    STRUCT_COMMAND_ACK,
    STRUCT_CONFIG,
    STRUCT_DIAGNOSTIC,
    STRUCT_DRIVE_COMMAND,
    STRUCT_DRIVE_STATE,
    STRUCT_ENCODER_STATE,
    STRUCT_ENVIRONMENT_STATE,
    STRUCT_HELLO_ACK,
    STRUCT_IMU_STATE,
    STRUCT_PROXIMITY_STATE,
    STRUCT_SET_MODE,
)

_HEADER_STRUCT = "<BBHHI"  # protocolVersion u8, messageType u8, sequence u16, payloadLength u16, senderUptimeMs u32


class ProtocolError(Exception):
    """모든 프레임 파싱 오류의 베이스."""


class CobsError(ProtocolError):
    """COBS 디코딩 형식 오류."""


class LengthError(ProtocolError):
    """payloadLength가 실제 길이와 맞지 않거나 MAX_PAYLOAD_BYTES를 초과."""


class VersionError(ProtocolError):
    """protocolVersion 불일치."""


class CrcError(ProtocolError):
    """CRC-16 불일치."""


class UnknownMessageTypeError(ProtocolError):
    """정의되지 않은 messageType."""


# ---- CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, 반전 없음) ----
# 표준 check value: crc16_ccitt_false(b"123456789") == 0x29B1


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


# ---- COBS ----


def cobs_encode(data: bytes) -> bytes:
    """0x00 구분자는 포함하지 않은 COBS 블록을 반환한다."""
    output = bytearray()
    output.append(0)  # code 바이트 placeholder
    code_index = 0
    code = 1

    for byte in data:
        if byte == 0:
            output[code_index] = code
            code = 1
            code_index = len(output)
            output.append(0)
        else:
            output.append(byte)
            code += 1
            if code == 0xFF:
                output[code_index] = code
                code = 1
                code_index = len(output)
                output.append(0)
    output[code_index] = code
    return bytes(output)


def cobs_decode(data: bytes) -> bytes:
    """data는 0x00 구분자를 제외한 COBS 블록이어야 한다."""
    output = bytearray()
    read_index = 0
    length = len(data)

    while read_index < length:
        code = data[read_index]
        if code == 0 or (read_index + code > length and code != 1):
            raise CobsError("malformed COBS block")
        read_index += 1

        for _ in range(1, code):
            output.append(data[read_index])
            read_index += 1

        if code != 0xFF and read_index != length:
            output.append(0)

    return bytes(output)


# ---- 프레임 build/parse ----


@dataclass
class ParsedFrame:
    protocol_version: int
    message_type: int
    sequence: int
    payload_length: int
    sender_uptime_ms: int
    payload: bytes


def build_frame(message_type: int, sequence: int, sender_uptime_ms: int, payload: bytes = b"") -> bytes:
    """헤더+payload+crc16을 COBS 인코딩 후 0x00 구분자를 붙여 반환한다."""
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"payload too large: {len(payload)} > {MAX_PAYLOAD_BYTES}")

    header = struct.pack(
        _HEADER_STRUCT,
        PROTOCOL_VERSION,
        message_type,
        sequence & 0xFFFF,
        len(payload),
        sender_uptime_ms & 0xFFFFFFFF,
    )
    raw = header + payload
    crc = crc16_ccitt_false(raw)
    raw += struct.pack("<H", crc)
    return cobs_encode(raw) + b"\x00"


def parse_frame(cobs_frame: bytes) -> ParsedFrame:
    """cobs_frame은 0x00 구분자를 제외한 상태여야 한다(스트림 분리는 호출자 책임)."""
    raw = cobs_decode(cobs_frame)

    if len(raw) < FRAME_HEADER_BYTES + FRAME_CRC_BYTES:
        raise LengthError("frame shorter than header+crc")

    protocol_version, message_type, sequence, payload_length, sender_uptime_ms = struct.unpack(
        _HEADER_STRUCT, raw[:FRAME_HEADER_BYTES]
    )

    expected_total = FRAME_HEADER_BYTES + payload_length + FRAME_CRC_BYTES
    if payload_length > MAX_PAYLOAD_BYTES or expected_total != len(raw):
        raise LengthError(f"payload length mismatch: declared={payload_length} frame={len(raw)}")
    if protocol_version != PROTOCOL_VERSION:
        raise VersionError(f"unexpected protocol version {protocol_version}")
    if message_type not in KNOWN_MESSAGE_TYPES:
        raise UnknownMessageTypeError(f"unknown message type 0x{message_type:02x}")

    received_crc = struct.unpack("<H", raw[-FRAME_CRC_BYTES:])[0]
    computed_crc = crc16_ccitt_false(raw[:-FRAME_CRC_BYTES])
    if received_crc != computed_crc:
        raise CrcError("crc mismatch")

    payload = bytes(raw[FRAME_HEADER_BYTES : FRAME_HEADER_BYTES + payload_length])
    return ParsedFrame(protocol_version, message_type, sequence, payload_length, sender_uptime_ms, payload)


def is_sequence_newer(candidate: int, last: int) -> bool:
    """RFC1982 스타일 랩어라운드-세이프 16비트 시퀀스 비교."""
    delta = (candidate - last) & 0xFFFF
    return delta != 0 and delta < 0x8000


# ==== 페이로드 (docs/03-제어-캘리브레이션.md §34-5 확정분) ====


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
    return DriveCommand(*struct.unpack(STRUCT_DRIVE_COMMAND, payload))


@dataclass
class SetMode:
    """모드 전환 원샷 명령(0x13) 페이로드.

    `flags`는 예약이며 수신 측은 값을 보지 않는다. 이 프레임은 재전송하지 않는다 -
    `STOP_COMMAND`와 달리 멱등이 아니어서 3프레임이면 3ACK·3전이가 된다.
    """

    requested_mode: int
    flags: int = 0


def pack_set_mode(cmd: SetMode) -> bytes:
    return struct.pack(STRUCT_SET_MODE, cmd.requested_mode, cmd.flags)


def unpack_set_mode(payload: bytes) -> SetMode:
    """길이가 다르면 `LengthError`를 낸다(`unpack_imu_state`와 같은 근거)."""
    expected = struct.calcsize(STRUCT_SET_MODE)
    if len(payload) != expected:
        raise LengthError(f"SET_MODE payload must be {expected} bytes, got {len(payload)}")
    return SetMode(*struct.unpack(STRUCT_SET_MODE, payload))


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
    return DriveState(*struct.unpack(STRUCT_DRIVE_STATE, payload))


@dataclass
class EncoderState:
    drive_encoder_ticks_left: int
    drive_encoder_ticks_right: int
    drive_speed_left_mmps: int
    drive_speed_right_mmps: int
    measured_steering_mdeg: int
    sample_age_ms: int


def pack_encoder_state(state: EncoderState) -> bytes:
    return struct.pack(
        STRUCT_ENCODER_STATE,
        state.drive_encoder_ticks_left,
        state.drive_encoder_ticks_right,
        state.drive_speed_left_mmps,
        state.drive_speed_right_mmps,
        state.measured_steering_mdeg,
        state.sample_age_ms,
    )


def unpack_encoder_state(payload: bytes) -> EncoderState:
    return EncoderState(*struct.unpack(STRUCT_ENCODER_STATE, payload))


@dataclass
class ImuState:
    """MPU6050 원시 gyro/accel 1샘플.

    `sample_time_us`는 센서 ESP32의 monotonic 측정 시각(µs, esp_timer)이며 수신
    시각으로 대신하지 않는다(§34-5). ROS 시각 변환은 `imu_clock.BoardClockOffset`이
    맡는다.
    """

    sample_time_us: int
    gyro_x_radps: float
    gyro_y_radps: float
    gyro_z_radps: float
    accel_x_mps2: float
    accel_y_mps2: float
    accel_z_mps2: float
    temperature_centi_c: int
    status_flags: int


def pack_imu_state(state: ImuState) -> bytes:
    return struct.pack(
        STRUCT_IMU_STATE,
        state.sample_time_us,
        state.gyro_x_radps,
        state.gyro_y_radps,
        state.gyro_z_radps,
        state.accel_x_mps2,
        state.accel_y_mps2,
        state.accel_z_mps2,
        state.temperature_centi_c,
        state.status_flags,
    )


def unpack_imu_state(payload: bytes) -> ImuState:
    """길이가 다르면 `LengthError`를 낸다(C++ `unpackImuState`의 false와 같은 취급).

    `parse_frame`은 헤더의 payload_length와 프레임 길이만 맞춰 보고 타입별 크기는
    보지 않는다. 펌웨어/브리지 버전이 어긋나면 여기서 걸러야 하며, 그때
    `struct.error`가 아니라 `_FRAME_PARSE_ERRORS`에 속한 예외를 던져야 수신
    루프가 조용히 죽지 않는다.
    """
    expected = struct.calcsize(STRUCT_IMU_STATE)
    if len(payload) != expected:
        raise LengthError(f"IMU_STATE payload must be {expected} bytes, got {len(payload)}")
    return ImuState(*struct.unpack(STRUCT_IMU_STATE, payload))


@dataclass
class EnvironmentState:
    temperature_deci_c: int
    humidity_deci_pct: int
    status_flags: int
    sample_age_ms: int


def pack_environment_state(state: EnvironmentState) -> bytes:
    return struct.pack(
        STRUCT_ENVIRONMENT_STATE,
        state.temperature_deci_c,
        state.humidity_deci_pct,
        state.status_flags,
        state.sample_age_ms,
    )


def unpack_environment_state(payload: bytes) -> EnvironmentState:
    return EnvironmentState(*struct.unpack(STRUCT_ENVIRONMENT_STATE, payload))


@dataclass
class ProximityState:
    front_min_distance_mm: int
    valid_sensor_mask: int
    protective_stop: int
    sample_age_ms: int


def pack_proximity_state(state: ProximityState) -> bytes:
    return struct.pack(
        STRUCT_PROXIMITY_STATE,
        state.front_min_distance_mm,
        state.valid_sensor_mask,
        state.protective_stop,
        state.sample_age_ms,
    )


def unpack_proximity_state(payload: bytes) -> ProximityState:
    return ProximityState(*struct.unpack(STRUCT_PROXIMITY_STATE, payload))


# ==== 페이로드 (문서에 없어 이번 작업에서 신규 확정, README 참고) ====

# HELLO(0x01)는 payload 없음(0바이트) - pack/unpack 불필요.


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
    return HelloAck(*struct.unpack(STRUCT_HELLO_ACK, payload))


@dataclass
class Diagnostic:
    board_role: int
    board_state: int
    fault_flags: int
    crc_error_count: int
    dropped_frame_count: int
    stale_sequence_count: int
    free_heap_bytes: int


def pack_diagnostic(diag: Diagnostic) -> bytes:
    return struct.pack(
        STRUCT_DIAGNOSTIC,
        diag.board_role,
        diag.board_state,
        diag.fault_flags,
        diag.crc_error_count,
        diag.dropped_frame_count,
        diag.stale_sequence_count,
        diag.free_heap_bytes,
    )


def unpack_diagnostic(payload: bytes) -> Diagnostic:
    return Diagnostic(*struct.unpack(STRUCT_DIAGNOSTIC, payload))


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
    return CommandAck(*struct.unpack(STRUCT_COMMAND_ACK, payload))


@dataclass
class ConfigMessage:
    operation: int
    key_id: int
    value: int


def pack_config_message(msg: ConfigMessage) -> bytes:
    return struct.pack(STRUCT_CONFIG, msg.operation, msg.key_id, msg.value)


def unpack_config_message(payload: bytes) -> ConfigMessage:
    return ConfigMessage(*struct.unpack(STRUCT_CONFIG, payload))
